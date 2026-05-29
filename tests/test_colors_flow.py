import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config  # noqa: E402
import handlers  # noqa: E402
import store  # noqa: E402


def test_plate_without_filaments_asks_for_one_slot(tmp_path, monkeypatch):
    """A plate that comes back with an empty filament list must still ask for a
    single slot — 0 required colours would dead-end the reply parser."""
    config.DB_PATH = str(tmp_path / "t.db")
    store.init_db()
    job_id = store.create_dialog("group.x", "+1", 1, "Mini Clip Cat")

    async def _status(_pid):
        return {"ams": [{"tray": [{"id": 0, "tray_type": "PLA"}]}]}

    async def _thumb(_lfid, _idx):
        return None

    sent = []
    monkeypatch.setattr(handlers.bambuddy, "printer_status", _status)
    monkeypatch.setattr(handlers, "_thumbnail", _thumb)
    monkeypatch.setattr(handlers.swatch, "build", lambda *a, **k: None)
    monkeypatch.setattr(handlers.signal_client, "send_to_group",
                        lambda gid, msg, **kw: sent.append(msg) or _none())

    plate = {"index": 1, "name": "", "filaments": []}  # the buggy case
    asyncio.run(handlers._ask_colors("group.x", job_id, 1, "Mini Clip Cat", plate))

    required = json.loads(store.active_job("group.x")["required_colors"])
    assert len(required) == 1  # normalized to one filament, not zero
    assert sent and "1 Farbe" in sent[0]


async def _none():
    return None


def test_completion_watch_announces_start_then_finish(tmp_path, monkeypatch):
    config.DB_PATH = str(tmp_path / "t.db")
    store.init_db()
    tid = store.add_queued("group.x", "+1", "Benchy", 4, 42)

    sent = []
    monkeypatch.setattr(handlers.signal_client, "send_to_group",
                        lambda gid, msg, **kw: sent.append(msg) or _none())

    item = {"status": "pending"}

    async def _get_item(_id):
        return item
    monkeypatch.setattr(handlers.bambuddy, "get_queue_item", _get_item)

    # still pending → no message, still 'queued'
    asyncio.run(handlers._check_completions())
    assert sent == []
    assert store.queued_jobs_with_item()[0]["stage"] == "queued"

    # goes live → exactly one "started" message, stage flips to 'printing'
    item["status"] = "printing"
    asyncio.run(handlers._check_completions())
    assert len(sent) == 1 and "druckt jetzt" in sent[0]
    assert store.queued_jobs_with_item()[0]["stage"] == "printing"

    # still printing on the next poll → no duplicate ping
    asyncio.run(handlers._check_completions())
    assert len(sent) == 1

    # finishes → "fertig" message, tracker leaves the watch set
    item["status"] = "completed"
    asyncio.run(handlers._check_completions())
    assert len(sent) == 2 and "fertig" in sent[1]
    assert store.queued_jobs_with_item() == []
