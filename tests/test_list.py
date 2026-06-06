import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
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


def test_list_shows_print_duration(tmp_path, monkeypatch):
    sent = _setup(tmp_path, monkeypatch, [
        {"id": 1, "status": "pending", "library_file_name": "Quick",
         "print_time_seconds": 1267},                     # 21 min
        {"id": 2, "status": "pending", "library_file_name": "Long",
         "print_time_seconds": 7800},                     # 2h10
        {"id": 3, "status": "pending", "library_file_name": "Unknown"},  # no time
    ])
    asyncio.run(handlers._list("g1"))
    out = sent[-1]
    assert "Quick (pending) · 21 min" in out
    assert "Long (pending) · 2h10" in out
    # No duration field → nothing appended after the status.
    assert "Unknown (pending)\n" in out or out.rstrip().endswith("Unknown (pending)")


def test_list_done_jobs_hidden(tmp_path, monkeypatch):
    sent = _setup(tmp_path, monkeypatch, [
        {"id": 1, "status": "failed", "library_file_name": "Boom"},
        {"id": 2, "status": "completed", "library_file_name": "Done"},
    ])
    asyncio.run(handlers._list("g1"))
    assert "Boom" not in sent[-1]
    assert "Done" not in sent[-1]
