"""Standing subscription ('Dauer-Abo') — adopt every *future* queue item.

!abo all only ever took a snapshot of what was open at that moment, so a print
queued later (Studio Send, someone else's job) reached nobody.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import classify  # noqa: E402
import config  # noqa: E402
import handlers  # noqa: E402
import store  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(tmp_path):
    config.DB_PATH = str(tmp_path / "t.db")
    store.init_db()
    handlers._PLATE_CACHE.clear()
    handlers._LAST_CONDITION = None
    yield
    handlers._PLATE_CACHE.clear()


async def _none():
    return None


def _stub(monkeypatch, items, printer=None):
    sent = []
    monkeypatch.setattr(handlers.signal_client, "send_to_group",
                        lambda gid, msg, **kw: sent.append((gid, msg)) or _none())

    async def list_queue():
        return items

    async def get_item(iid):
        return next((i for i in items if i["id"] == iid), None)

    async def pstatus(_pid):
        return printer or {"state": "IDLE", "hms_errors": []}
    monkeypatch.setattr(handlers.bambuddy, "list_queue", list_queue)
    monkeypatch.setattr(handlers.bambuddy, "get_queue_item", get_item)
    monkeypatch.setattr(handlers.bambuddy, "printer_status", pstatus)
    monkeypatch.setattr(handlers, "_job_thumbnail", lambda job: _none())
    return sent


def _items(*specs):
    return [{"id": i, "status": s, "library_file_name": n, "library_file_id": None}
            for i, s, n in specs]


# ----- parsing -----

def test_standing_parsing():
    """"all" and "immer" mean the same thing: everything, future prints included."""
    for msg in ("!abo all", "!abo alle", "!abo immer", "!abo always", "!abo dauer",
                "!abo on", "!abo an", "!abo ein", "!abonnieren permanent"):
        assert classify.abo_command(msg) == {
            "action": "subscribe", "all": True, "positions": [], "standing": True}, msg
    # A bare stop stops everything, standing included. "off" must have an "on".
    for msg in ("!abo stop", "!abo off", "!abo aus", "!deabo", "!abo stop all",
                "!abo stop immer", "!deabo immer"):
        assert classify.abo_command(msg) == {
            "action": "unsubscribe", "all": True, "positions": [], "standing": True}, msg


def test_numbered_form_is_still_a_one_off():
    """Only !abo <positions> is a snapshot — it must not turn on the standing flag."""
    assert classify.abo_command("!abo 2 3") == {
        "action": "subscribe", "all": False, "positions": [2, 3]}
    assert classify.abo_command("!abo stop 2") == {
        "action": "unsubscribe", "all": False, "positions": [2]}
    assert classify.abo_command("!abo") == {"action": "help"}


# ----- store -----

def test_standing_roundtrip():
    assert store.get_standing_abo("g1") is False
    store.set_standing_abo("g1", True)
    assert store.get_standing_abo("g1") is True
    assert store.standing_abo_groups() == ["g1"]
    store.set_standing_abo("g1", False)
    assert store.get_standing_abo("g1") is False
    assert store.standing_abo_groups() == []


def test_standing_survives_group_reregistration():
    """The reason it isn't a column on `groups`: save_group is INSERT OR REPLACE
    and would silently wipe it."""
    store.save_group("+43", "g1", "Queue")
    store.set_standing_abo("g1", True)
    store.save_group("+43", "g1", "Queue")          # re-register
    assert store.get_standing_abo("g1") is True


# ----- the poll pass -----

def test_poll_skips_queue_fetch_without_standing_groups(monkeypatch):
    """Load-bearing: every existing _check_completions test stubs only
    get_queue_item/printer_status, so an unconditional list_queue would send
    them at the real Bambuddy."""
    async def boom():
        raise AssertionError("list_queue must not be called")
    monkeypatch.setattr(handlers.bambuddy, "list_queue", boom)

    async def pstatus(_pid):
        return {"state": "IDLE", "hms_errors": []}
    monkeypatch.setattr(handlers.bambuddy, "printer_status", pstatus)
    asyncio.run(handlers._check_completions())      # must not raise


def test_standing_adopts_new_items(monkeypatch):
    items = _items((1, "pending", "Alpha"))
    _stub(monkeypatch, items)
    store.set_standing_abo("g1", True)

    asyncio.run(handlers._check_completions())
    assert {j["queue_item_id"] for j in store.queued_jobs_with_item()} == {1}

    items.append(_items((2, "pending", "Beta"))[0])   # queued later, by anyone
    asyncio.run(handlers._check_completions())
    assert {j["queue_item_id"] for j in store.queued_jobs_with_item()} == {1, 2}


def test_standing_is_idempotent(monkeypatch):
    _stub(monkeypatch, _items((1, "pending", "Alpha")))
    store.set_standing_abo("g1", True)
    for _ in range(3):
        asyncio.run(handlers._check_completions())
    assert len(store.queued_jobs_with_item()) == 1


def test_standing_skips_finished_items(monkeypatch):
    _stub(monkeypatch, _items((1, "completed", "Done"), (2, "failed", "Boom"),
                              (3, "cancelled", "Nope")))
    store.set_standing_abo("g1", True)
    asyncio.run(handlers._check_completions())
    assert store.queued_jobs_with_item() == []


def test_standing_adopts_midprint_without_false_start(monkeypatch):
    sent = _stub(monkeypatch, _items((1, "printing", "Running")),
                 printer={"state": "RUNNING", "hms_errors": []})
    store.set_standing_abo("g1", True)
    asyncio.run(handlers._check_completions())
    assert store.queued_jobs_with_item()[0]["stage"] == "printing"
    assert not any("druckt jetzt" in m for _, m in sent)


def test_standing_respects_abo_stop(monkeypatch):
    """Regression for the DELETE→mute change: a muted print must stay muted."""
    items = _items((1, "pending", "Alpha"))
    _stub(monkeypatch, items)
    store.set_standing_abo("g1", True)
    asyncio.run(handlers._check_completions())

    asyncio.run(handlers._abo("g1", {"action": "unsubscribe", "all": False, "positions": [1]}))
    assert store.queued_jobs_with_item() == []

    for _ in range(2):
        asyncio.run(handlers._check_completions())
    assert store.queued_jobs_with_item() == []      # not silently re-adopted


def test_each_standing_group_gets_its_own_tracker(monkeypatch):
    _stub(monkeypatch, _items((1, "pending", "Alpha")))
    store.set_standing_abo("g1", True)
    store.set_standing_abo("g2", True)
    asyncio.run(handlers._check_completions())
    assert {j["group_id"] for j in store.queued_jobs_with_item()} == {"g1", "g2"}


# ----- the command -----

_ALL = {"action": "subscribe", "all": True, "positions": [], "standing": True}
_STOP = {"action": "unsubscribe", "all": True, "positions": [], "standing": True}


def test_abo_all_turns_on_standing_and_adopts_open_items(monkeypatch):
    sent = _stub(monkeypatch, _items((1, "pending", "Alpha"), (2, "completed", "Old")))
    asyncio.run(handlers._abo("g1", _ALL))
    assert store.get_standing_abo("g1") is True
    assert {j["queue_item_id"] for j in store.queued_jobs_with_item()} == {1}
    # The message must promise future prints, not just "2 subscribed".
    assert "künftigen" in sent[-1][1]


def test_abo_all_then_future_print_is_picked_up(monkeypatch):
    """The whole point of !abo all — a print queued later must reach you."""
    items = _items((1, "pending", "Alpha"))
    _stub(monkeypatch, items)
    asyncio.run(handlers._abo("g1", _ALL))
    items.append(_items((2, "pending", "Queued later"))[0])
    asyncio.run(handlers._check_completions())
    assert {j["queue_item_id"] for j in store.queued_jobs_with_item()} == {1, 2}


def test_abo_stop_ends_standing_and_mutes_everything(monkeypatch):
    sent = _stub(monkeypatch, _items((1, "pending", "Alpha")))
    asyncio.run(handlers._abo("g1", _ALL))
    asyncio.run(handlers._abo("g1", _STOP))
    assert store.get_standing_abo("g1") is False
    assert store.queued_jobs_with_item() == []
    assert "Abo aus" in sent[-1][1]
    # and it stays off — no silent re-adoption on the next poll
    asyncio.run(handlers._check_completions())
    assert store.queued_jobs_with_item() == []


def test_abo_help_shows_standing_state(monkeypatch):
    sent = _stub(monkeypatch, _items((1, "pending", "Alpha")))
    asyncio.run(handlers._abo("g1", {"action": "help"}))
    assert "Abo für alles: aus" in sent[-1][1]
    store.set_standing_abo("g1", True)
    asyncio.run(handlers._abo("g1", {"action": "help"}))
    assert "an für alles" in sent[-1][1]


def test_resubscribing_a_running_print_does_not_fake_a_start(monkeypatch):
    """Regression: !abo stop then !abo all revived a mid-print job as 'queued', so
    the next poll read it as queued→printing and announced "druckt jetzt los" for
    a print that had been running for hours."""
    items = _items((1, "printing", "Been running for hours"))
    sent = _stub(monkeypatch, items, printer={"state": "RUNNING", "hms_errors": []})
    asyncio.run(handlers._abo("g1", _ALL))
    asyncio.run(handlers._abo("g1", _STOP))
    sent.clear()

    asyncio.run(handlers._abo("g1", _ALL))
    assert store.queued_jobs_with_item()[0]["stage"] == "printing"
    asyncio.run(handlers._check_completions())
    assert not any("druckt jetzt" in m for _, m in sent), sent


def test_resubscribing_a_pending_print_still_announces_its_start(monkeypatch):
    """Guard the other direction: a genuinely not-yet-started print must still
    announce when it starts."""
    items = _items((1, "pending", "Not started yet"))
    sent = _stub(monkeypatch, items, printer={"state": "RUNNING", "hms_errors": []})
    asyncio.run(handlers._abo("g1", _ALL))
    asyncio.run(handlers._abo("g1", _STOP))
    asyncio.run(handlers._abo("g1", _ALL))
    sent.clear()

    items[0]["status"] = "printing"
    asyncio.run(handlers._check_completions())
    assert any("druckt jetzt" in m for _, m in sent), sent


def test_resubscribe_after_stop(monkeypatch):
    """A muted tracker still counts as tracked, so re-subscribing has to revive it."""
    _stub(monkeypatch, _items((1, "pending", "Alpha")))
    asyncio.run(handlers._abo("g1", {"action": "subscribe", "all": True, "positions": []}))
    asyncio.run(handlers._abo("g1", {"action": "unsubscribe", "all": False, "positions": [1]}))
    assert store.queued_jobs_with_item() == []

    asyncio.run(handlers._abo("g1", {"action": "subscribe", "all": True, "positions": []}))
    assert {j["queue_item_id"] for j in store.queued_jobs_with_item()} == {1}


def test_abo_help_shows_stopped_prints_as_unsubscribed(monkeypatch):
    """!abo stop → !abo must show 🔕, not 🔔. tracked_item_ids is stage-agnostic
    on purpose (that's what makes the standing pass skip a muted row), so the
    display needs the *active* set instead."""
    sent = _stub(monkeypatch, _items((1, "pending", "Alpha"), (2, "pending", "Beta")))
    asyncio.run(handlers._abo("g1", {"action": "subscribe", "all": True, "positions": []}))
    asyncio.run(handlers._abo("g1", {"action": "help"}))
    assert sent[-1][1].count("🔔") >= 2          # both subscribed

    asyncio.run(handlers._abo("g1", {"action": "unsubscribe", "all": True, "positions": []}))
    asyncio.run(handlers._abo("g1", {"action": "help"}))
    lines = [ln for ln in sent[-1][1].splitlines() if ln[:1].isdigit()]
    assert len(lines) == 2
    assert all("🔕" in ln for ln in lines), sent[-1][1]


def test_abo_help_shows_finished_prints_as_unsubscribed(monkeypatch):
    """A tracker that already ran to completion isn't an active subscription."""
    sent = _stub(monkeypatch, _items((1, "pending", "Alpha")))
    asyncio.run(handlers._abo("g1", {"action": "subscribe", "all": True, "positions": []}))
    store.set_stage(store.last_queued_job("g1")["id"], "done")
    asyncio.run(handlers._abo("g1", {"action": "help"}))
    lines = [ln for ln in sent[-1][1].splitlines() if ln[:1].isdigit()]
    assert all("🔕" in ln for ln in lines), sent[-1][1]
