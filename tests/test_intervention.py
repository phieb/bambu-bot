"""Intervention alerts — the printer stopped and needs a human.

The bot used to be silent (and !progress actively misleading) exactly when
someone had to walk over: PAUSE counted as a healthy running state.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config  # noqa: E402
import handlers  # noqa: E402
import store  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_debounce():
    handlers._LAST_CONDITION = None
    yield
    handlers._LAST_CONDITION = None


def _setup(tmp_path, monkeypatch, item, printer, groups=("group.x",)):
    config.DB_PATH = str(tmp_path / "t.db")
    store.init_db()
    for g in groups:
        store.add_queued(g, "+1", "half height Box1 — Sticks", 4, 42)
        store.set_stage(store.last_queued_job(g)["id"], "printing")

    sent = []
    monkeypatch.setattr(handlers.signal_client, "send_to_group",
                        lambda gid, msg, **kw: sent.append((gid, msg)) or _none())

    async def _get_item(_id):
        return item

    async def _pstatus(_pid):
        return printer
    monkeypatch.setattr(handlers.bambuddy, "get_queue_item", _get_item)
    monkeypatch.setattr(handlers.bambuddy, "printer_status", _pstatus)
    return sent


async def _none():
    return None


def _poll(n=1):
    for _ in range(n):
        asyncio.run(handlers._check_completions())


# ----- the state-set split -----

def test_paused_is_not_a_running_state():
    assert "PAUSE" not in handlers._RUNNING_STATES
    assert "PAUSE" in handlers._PAUSED_STATES
    assert handlers._PAUSED_STATES & handlers._RUNNING_STATES == set()
    # still "a print is loaded" for !progress purposes
    assert "PAUSE" in handlers._ACTIVE_STATES


def test_start_not_announced_while_paused(tmp_path, monkeypatch):
    """A job paused on an error at dispatch must not read as 'druckt jetzt los'."""
    config.DB_PATH = str(tmp_path / "t.db")
    store.init_db()
    store.add_queued("group.x", "+1", "Benchy", 4, 42)      # stays 'queued'
    item = {"status": "printing"}
    printer = {"state": "PAUSE", "hms_errors": []}
    sent = []
    monkeypatch.setattr(handlers.signal_client, "send_to_group",
                        lambda gid, msg, **kw: sent.append((gid, msg)) or _none())

    async def _get_item(_id):
        return item

    async def _pstatus(_pid):
        return printer
    monkeypatch.setattr(handlers.bambuddy, "get_queue_item", _get_item)
    monkeypatch.setattr(handlers.bambuddy, "printer_status", _pstatus)
    monkeypatch.setattr(handlers, "_job_thumbnail", lambda job: _none())

    _poll(2)
    assert sent == []
    assert store.queued_jobs_with_item()[0]["stage"] == "queued"

    printer["state"] = "RUNNING"
    _poll()
    assert len(sent) == 1 and "druckt jetzt" in sent[0][1]


# ----- alerting once -----

def test_pause_alerts_once(tmp_path, monkeypatch):
    printer = {"state": "PAUSE", "hms_errors": []}
    sent = _setup(tmp_path, monkeypatch, {"status": "printing"}, printer)

    _poll()                                   # first sighting only arms the debounce
    assert sent == []
    _poll(3)                                  # holds → exactly one alert, then quiet
    assert len(sent) == 1
    assert "pausiert" in sent[0][1] and "half height Box1" in sent[0][1]


def test_transient_pause_is_debounced(tmp_path, monkeypatch):
    """An M600 filament change blips PAUSE for one poll — don't cry wolf."""
    printer = {"state": "RUNNING", "hms_errors": []}
    sent = _setup(tmp_path, monkeypatch, {"status": "printing"}, printer)

    _poll()
    printer["state"] = "PAUSE"
    _poll()
    printer["state"] = "RUNNING"
    _poll(2)
    assert sent == []


def test_hms_alerts_once_and_a_new_code_alerts_again(tmp_path, monkeypatch):
    printer = {"state": "RUNNING",
               "hms_errors": [{"code": "0500-4003", "description": "Filament ausgegangen"}]}
    sent = _setup(tmp_path, monkeypatch, {"status": "printing"}, printer)

    _poll(3)
    assert len(sent) == 1
    assert "0500-4003" in sent[0][1] and "Fehler" in sent[0][1]

    printer["hms_errors"] = [{"code": "0300-0100", "description": "Anderes Problem"}]
    _poll(3)
    assert len(sent) == 2 and "0300-0100" in sent[1][1]


def test_alert_explains_a_real_bambuddy_error(tmp_path, monkeypatch):
    """Bambuddy sends no description — the alert must still say what happened."""
    printer = {"state": "PAUSE", "hms_errors": [
        {"code": "0x20001", "attr": 0x07002000, "module": 7, "severity": 2,
         "actions": [], "job_id": None, "full_code": "0700200000020001"}]}
    sent = _setup(tmp_path, monkeypatch, {"status": "printing"}, printer)
    _poll(3)
    assert len(sent) == 1
    assert "AMS A Slot 1 Filament ist aufgebraucht" in sent[0][1]
    assert "0700-2000-0002-0001" in sent[0][1]


def test_hms_wins_over_pause(tmp_path, monkeypatch):
    """A pause caused by an error should read as the error, not a bare pause."""
    printer = {"state": "PAUSE", "hms_errors": [{"code": "0500-4003", "description": "x"}]}
    sent = _setup(tmp_path, monkeypatch, {"status": "printing"}, printer)
    _poll(3)
    assert len(sent) == 1 and "0500-4003" in sent[0][1]


def test_recovery_message_and_rearm(tmp_path, monkeypatch):
    printer = {"state": "PAUSE", "hms_errors": []}
    sent = _setup(tmp_path, monkeypatch, {"status": "printing"}, printer)

    _poll(2)
    assert len(sent) == 1

    printer["state"] = "RUNNING"
    _poll(2)
    assert len(sent) == 2 and "läuft wieder" in sent[1][1]

    # a genuinely new incident must alert again
    printer["state"] = "PAUSE"
    _poll(2)
    assert len(sent) == 3 and "pausiert" in sent[2][1]


def test_alert_reaches_every_subscriber_of_that_print(tmp_path, monkeypatch):
    printer = {"state": "PAUSE", "hms_errors": []}
    sent = _setup(tmp_path, monkeypatch, {"status": "printing"}, printer,
                  groups=("group.a", "group.b"))
    _poll(2)
    assert {gid for gid, _ in sent} == {"group.a", "group.b"}


def test_no_alert_for_a_job_that_is_not_printing(tmp_path, monkeypatch):
    printer = {"state": "PAUSE", "hms_errors": []}
    sent = _setup(tmp_path, monkeypatch, {"status": "pending"}, printer)
    _poll(3)
    assert sent == []


# ----- defensive parsing -----

@pytest.mark.parametrize("errors", [
    ["0500-4003"],                      # bare strings
    [{}],                               # empty dicts
    [{"hms_code": "0500-4003"}],        # alternate key
    "not-a-list",                       # wrong type entirely
])
def test_odd_hms_shapes_never_raise(tmp_path, monkeypatch, errors):
    """An exception in the poller kills the cycle for every tracker, not just one."""
    printer = {"state": "RUNNING", "hms_errors": errors}
    sent = _setup(tmp_path, monkeypatch, {"status": "printing"}, printer)
    _poll(3)
    assert len(sent) <= 1               # alerted or not, but never crashed
    assert store.queued_jobs_with_item()          # tracker survived


def test_healthy_printer_produces_no_condition():
    assert handlers._condition({"state": "RUNNING", "hms_errors": []}) is None
    assert handlers._condition({}) is None
    assert handlers._condition(None) is None


# ----- !progress -----

def _progress_setup(monkeypatch, status):
    sent = []
    monkeypatch.setattr(handlers.signal_client, "send_to_group",
                        lambda gid, msg, **kw: sent.append(msg) or _none())
    monkeypatch.setattr(handlers.bambuddy, "printer_status", lambda pid: _status(status))
    monkeypatch.setattr(handlers.bambuddy, "camera_snapshot", lambda pid: _none())
    return sent


async def _status(s):
    return s


def test_progress_flags_pause_and_hides_the_eta(tmp_path, monkeypatch):
    config.DB_PATH = str(tmp_path / "t.db")
    store.init_db()
    sent = _progress_setup(monkeypatch, {
        "state": "PAUSE", "current_print": "Box1", "progress": 42,
        "remaining_time": 70, "hms_errors": []})
    asyncio.run(handlers._progress("group.x"))
    out = sent[-1]
    assert "⏸️" in out and "pausiert" in out
    assert "fertig ~" not in out          # frozen remaining_time must not be shown


def test_progress_reports_hms(tmp_path, monkeypatch):
    config.DB_PATH = str(tmp_path / "t.db")
    store.init_db()
    sent = _progress_setup(monkeypatch, {
        "state": "RUNNING", "current_print": "Box1", "progress": 42,
        "hms_errors": [{"code": "0500-4003", "description": "Filament ausgegangen"}]})
    asyncio.run(handlers._progress("group.x"))
    assert "0500-4003" in sent[-1]


def test_progress_explains_a_real_bambuddy_error(tmp_path, monkeypatch):
    config.DB_PATH = str(tmp_path / "t.db")
    store.init_db()
    sent = _progress_setup(monkeypatch, {
        "state": "PAUSE", "current_print": "Box1", "progress": 42,
        "hms_errors": [{"code": "0x20001", "attr": 0x07002000, "severity": 2,
                        "full_code": "0700200000020001"}]})
    asyncio.run(handlers._progress("group.x"))
    out = sent[-1]
    assert "AMS A Slot 1 Filament ist aufgebraucht" in out
    # the explanation is a sentence — it must not be crammed into the " · " strip
    assert "\n" in out and " · AMS" not in out


def test_progress_idle_explains_awaiting_plate_clear(tmp_path, monkeypatch):
    config.DB_PATH = str(tmp_path / "t.db")
    store.init_db()
    sent = _progress_setup(monkeypatch, {
        "state": "FINISH", "current_print": "", "awaiting_plate_clear": True,
        "hms_errors": []})
    asyncio.run(handlers._progress("group.x"))
    assert "!go" in sent[-1]


def test_progress_running_still_shows_eta(tmp_path, monkeypatch):
    """Guard against the pause change swallowing the normal ETA."""
    config.DB_PATH = str(tmp_path / "t.db")
    store.init_db()
    sent = _progress_setup(monkeypatch, {
        "state": "RUNNING", "current_print": "Box1", "progress": 42,
        "remaining_time": 70, "hms_errors": []})
    asyncio.run(handlers._progress("group.x"))
    out = sent[-1]
    assert "fertig ~" in out and "⏸️" not in out
