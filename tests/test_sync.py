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


def test_sync_command_parses():
    assert classify.classify({"dataMessage": {"message": "!sync"}})["is_sync"]
    assert classify.classify({"dataMessage": {"message": "!übernehmen"}})["is_sync"]
    assert not classify.classify({"dataMessage": {"message": "sync"}})["is_sync"]


def test_sync_adopts_foreign_open_jobs_only(tmp_path, monkeypatch):
    sent = _setup(tmp_path, monkeypatch, [
        {"id": 10, "status": "pending", "library_file_name": "Foreign A"},
        {"id": 11, "status": "printing", "library_file_name": "Foreign B"},
        {"id": 12, "status": "completed", "library_file_name": "Done C"},
        {"id": 13, "status": "pending", "library_file_name": "Already tracked"},
    ])
    # id 13 is already tracked (e.g. the bot queued it) → must not be re-adopted.
    store.add_queued("g1", "", "Already tracked", None, 13)

    asyncio.run(handlers._sync("g1"))

    tracked = {j["queue_item_id"]: j for j in store.queued_jobs_with_item()}
    assert set(tracked) == {10, 11, 13}          # 12 (completed) skipped
    assert tracked[11]["stage"] == "printing"     # already mid-print
    assert tracked[10]["stage"] == "queued"
    assert "2 Job(s) übernommen" in sent[-1]


def test_sync_adopts_prints_another_group_already_tracks(tmp_path, monkeypatch):
    """Everyone must be able to !sync the same print and get their own alerts —
    a job tracked by g1 must not be skipped when g2 syncs."""
    sent = _setup(tmp_path, monkeypatch, [
        {"id": 10, "status": "pending", "library_file_name": "Shared print"},
    ])
    asyncio.run(handlers._sync("g1"))
    asyncio.run(handlers._sync("g2"))

    groups = {j["group_id"] for j in store.queued_jobs_with_item() if j["queue_item_id"] == 10}
    assert groups == {"g1", "g2"}
    assert "1 Job(s) übernommen" in sent[-1]


def test_sync_is_idempotent(tmp_path, monkeypatch):
    sent = _setup(tmp_path, monkeypatch, [
        {"id": 10, "status": "pending", "library_file_name": "Foreign A"},
    ])
    asyncio.run(handlers._sync("g1"))
    asyncio.run(handlers._sync("g1"))
    assert len(store.queued_jobs_with_item()) == 1   # not double-added
    assert "synchron" in sent[-1]
