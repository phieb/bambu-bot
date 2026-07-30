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
    for msg in ("!abo immer", "!abo always", "!abo dauer", "!abonnieren permanent"):
        assert classify.abo_command(msg) == {
            "action": "subscribe", "all": False, "positions": [], "standing": True}, msg
    for msg in ("!abo stop immer", "!deabo immer", "!abo aus always"):
        assert classify.abo_command(msg) == {
            "action": "unsubscribe", "all": False, "positions": [], "standing": True}, msg


def test_existing_abo_shapes_unchanged():
    """The standing key must not leak into the other shapes (exact-equality tests)."""
    assert classify.abo_command("!abo all") == {
        "action": "subscribe", "all": True, "positions": []}
    assert classify.abo_command("!abo 2 3") == {
        "action": "subscribe", "all": False, "positions": [2, 3]}
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

def test_abo_immer_turns_on_and_adopts_open_items(monkeypatch):
    sent = _stub(monkeypatch, _items((1, "pending", "Alpha"), (2, "completed", "Old")))
    asyncio.run(handlers._abo("g1", {"action": "subscribe", "all": False,
                                     "positions": [], "standing": True}))
    assert store.get_standing_abo("g1") is True
    assert {j["queue_item_id"] for j in store.queued_jobs_with_item()} == {1}
    assert "Dauer-Abo an" in sent[-1][1]


def test_abo_stop_immer_keeps_existing_trackers(monkeypatch):
    sent = _stub(monkeypatch, _items((1, "pending", "Alpha")))
    asyncio.run(handlers._abo("g1", {"action": "subscribe", "all": False,
                                     "positions": [], "standing": True}))
    asyncio.run(handlers._abo("g1", {"action": "unsubscribe", "all": False,
                                     "positions": [], "standing": True}))
    assert store.get_standing_abo("g1") is False
    assert len(store.queued_jobs_with_item()) == 1      # legitimately adopted, kept
    assert "Dauer-Abo aus" in sent[-1][1]


def test_abo_help_shows_standing_state(monkeypatch):
    sent = _stub(monkeypatch, _items((1, "pending", "Alpha")))
    asyncio.run(handlers._abo("g1", {"action": "help"}))
    assert "Dauer-Abo: aus" in sent[-1][1]
    store.set_standing_abo("g1", True)
    asyncio.run(handlers._abo("g1", {"action": "help"}))
    assert "Dauer-Abo: **an**" in sent[-1][1]


def test_resubscribe_after_stop(monkeypatch):
    """A muted tracker still counts as tracked, so re-subscribing has to revive it."""
    _stub(monkeypatch, _items((1, "pending", "Alpha")))
    asyncio.run(handlers._abo("g1", {"action": "subscribe", "all": True, "positions": []}))
    asyncio.run(handlers._abo("g1", {"action": "unsubscribe", "all": False, "positions": [1]}))
    assert store.queued_jobs_with_item() == []

    asyncio.run(handlers._abo("g1", {"action": "subscribe", "all": True, "positions": []}))
    assert {j["queue_item_id"] for j in store.queued_jobs_with_item()} == {1}
