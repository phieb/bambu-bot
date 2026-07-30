import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import classify  # noqa: E402
import config  # noqa: E402
import handlers  # noqa: E402
import store  # noqa: E402


def _setup(tmp_path, monkeypatch, queue_items):
    config.DB_PATH = str(tmp_path / "t.db")
    store.init_db()
    sent = []

    async def fake_send(group_id, msg, **k):
        sent.append(msg)

    async def fake_list_queue():
        return queue_items

    monkeypatch.setattr(handlers.signal_client, "send_to_group", fake_send)
    monkeypatch.setattr(handlers.bambuddy, "list_queue", fake_list_queue)
    return sent


# ----- classify -----

def test_abo_parsing():
    c = classify.classify
    assert c({"dataMessage": {"message": "!abo"}})["abo_command"] == {"action": "help"}
    # "all" covers future prints too — a snapshot of exactly the currently-open
    # set was a distinction nobody wanted.
    assert c({"dataMessage": {"message": "!abo all"}})["abo_command"] == {
        "action": "subscribe", "all": True, "positions": [], "standing": True}
    assert c({"dataMessage": {"message": "!abo alle"}})["abo_command"]["all"] is True
    assert c({"dataMessage": {"message": "!abo 2 3"}})["abo_command"] == {
        "action": "subscribe", "all": False, "positions": [2, 3]}   # one-off, no standing
    assert c({"dataMessage": {"message": "!abo 2,4,1"}})["abo_command"]["positions"] == [2, 4, 1]
    assert c({"dataMessage": {"message": "!abo stop"}})["abo_command"] == {
        "action": "unsubscribe", "all": True, "positions": [], "standing": True}
    assert c({"dataMessage": {"message": "!abo stop 2"}})["abo_command"] == {
        "action": "unsubscribe", "all": False, "positions": [2]}
    assert c({"dataMessage": {"message": "!deabo"}})["abo_command"] == {
        "action": "unsubscribe", "all": True, "positions": [], "standing": True}
    assert c({"dataMessage": {"message": "!deabo 3"}})["abo_command"]["positions"] == [3]
    # garbage arg → help, not a no-op
    assert c({"dataMessage": {"message": "!abo wat"}})["abo_command"] == {"action": "help"}
    # not an abo command
    assert c({"dataMessage": {"message": "abo"}})["abo_command"] is None
    assert c({"dataMessage": {"message": "!liste"}})["abo_command"] is None


# ----- subscribe -----

def test_abo_all_subscribes_open_items_only(tmp_path, monkeypatch):
    sent = _setup(tmp_path, monkeypatch, [
        {"id": 10, "status": "pending", "library_file_name": "A"},
        {"id": 11, "status": "printing", "library_file_name": "B"},
        {"id": 12, "status": "completed", "library_file_name": "Done"},
    ])
    asyncio.run(handlers._abo("g1", {"action": "subscribe", "all": True, "positions": []}))
    tracked = {j["queue_item_id"]: j for j in store.queued_jobs_with_item()}
    assert set(tracked) == {10, 11}              # completed one skipped
    assert tracked[11]["stage"] == "printing"     # mid-print → no false "starts now"
    assert "2 Druck(e) abonniert" in sent[-1]


def test_abo_by_position(tmp_path, monkeypatch):
    sent = _setup(tmp_path, monkeypatch, [
        {"id": 10, "status": "pending", "library_file_name": "A"},
        {"id": 11, "status": "pending", "library_file_name": "B"},
        {"id": 12, "status": "pending", "library_file_name": "C"},
    ])
    asyncio.run(handlers._abo("g1", {"action": "subscribe", "all": False, "positions": [1, 3]}))
    assert {j["queue_item_id"] for j in store.queued_jobs_with_item()} == {10, 12}
    assert "B" not in sent[-1] and "A" in sent[-1] and "C" in sent[-1]


def test_abo_is_idempotent_per_group(tmp_path, monkeypatch):
    sent = _setup(tmp_path, monkeypatch, [
        {"id": 10, "status": "pending", "library_file_name": "A"},
    ])
    cmd = {"action": "subscribe", "all": True, "positions": []}
    asyncio.run(handlers._abo("g1", cmd))
    asyncio.run(handlers._abo("g1", cmd))
    assert len(store.queued_jobs_with_item()) == 1   # not double-added
    assert "Schon abonniert" in sent[-1]


def test_abo_multiple_people_get_notified_for_same_print(tmp_path, monkeypatch):
    # The whole point: two groups subscribing the same print both get trackers,
    # so both are notified about its start and end.
    _setup(tmp_path, monkeypatch, [
        {"id": 10, "status": "pending", "library_file_name": "Shared"},
    ])
    cmd = {"action": "subscribe", "all": True, "positions": []}
    asyncio.run(handlers._abo("g1", cmd))
    asyncio.run(handlers._abo("g2", cmd))
    groups = {j["group_id"] for j in store.queued_jobs_with_item()
              if j["queue_item_id"] == 10}
    assert groups == {"g1", "g2"}


def test_abo_bad_position(tmp_path, monkeypatch):
    sent = _setup(tmp_path, monkeypatch, [
        {"id": 10, "status": "pending", "library_file_name": "A"},
    ])
    asyncio.run(handlers._abo("g1", {"action": "subscribe", "all": False, "positions": [5]}))
    assert store.queued_jobs_with_item() == []
    assert "5" in sent[-1] and "1 offene" in sent[-1]


# ----- unsubscribe -----

def test_abo_stop_all(tmp_path, monkeypatch):
    sent = _setup(tmp_path, monkeypatch, [
        {"id": 10, "status": "pending", "library_file_name": "A"},
        {"id": 11, "status": "pending", "library_file_name": "B"},
    ])
    asyncio.run(handlers._abo("g1", {"action": "subscribe", "all": True, "positions": []}))
    asyncio.run(handlers._abo("g1", {"action": "unsubscribe", "all": True, "positions": []}))
    assert store.queued_jobs_with_item() == []
    assert "2 Abo(s) beendet" in sent[-1]


def test_abo_stop_all_only_affects_own_group(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, [
        {"id": 10, "status": "pending", "library_file_name": "Shared"},
    ])
    cmd = {"action": "subscribe", "all": True, "positions": []}
    asyncio.run(handlers._abo("g1", cmd))
    asyncio.run(handlers._abo("g2", cmd))
    asyncio.run(handlers._abo("g1", {"action": "unsubscribe", "all": True, "positions": []}))
    remaining = {j["group_id"] for j in store.queued_jobs_with_item()}
    assert remaining == {"g2"}                    # g2 still subscribed


def test_abo_stop_by_position(tmp_path, monkeypatch):
    sent = _setup(tmp_path, monkeypatch, [
        {"id": 10, "status": "pending", "library_file_name": "A"},
        {"id": 11, "status": "pending", "library_file_name": "B"},
    ])
    asyncio.run(handlers._abo("g1", {"action": "subscribe", "all": True, "positions": []}))
    asyncio.run(handlers._abo("g1", {"action": "unsubscribe", "all": False, "positions": [1]}))
    assert {j["queue_item_id"] for j in store.queued_jobs_with_item()} == {11}
    assert "1 Abo(s) beendet" in sent[-1]


def test_abo_stop_nothing(tmp_path, monkeypatch):
    sent = _setup(tmp_path, monkeypatch, [])
    asyncio.run(handlers._abo("g1", {"action": "unsubscribe", "all": True, "positions": []}))
    assert "kein Abo" in sent[-1]


# ----- help / status -----

def test_abo_help_shows_subscription_marks(tmp_path, monkeypatch):
    sent = _setup(tmp_path, monkeypatch, [
        {"id": 10, "status": "pending", "library_file_name": "A"},
        {"id": 11, "status": "pending", "library_file_name": "B"},
    ])
    asyncio.run(handlers._abo("g1", {"action": "subscribe", "all": False, "positions": [1]}))
    asyncio.run(handlers._abo("g1", {"action": "help"}))
    msg = sent[-1]
    assert "1. 🔔 A" in msg          # subscribed
    assert "2. 🔕 B" in msg          # not subscribed


def test_abo_help_empty_queue(tmp_path, monkeypatch):
    sent = _setup(tmp_path, monkeypatch, [])
    asyncio.run(handlers._abo("g1", {"action": "help"}))
    assert "Keine offenen Drucke" in sent[-1]
