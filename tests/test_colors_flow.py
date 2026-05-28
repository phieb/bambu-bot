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
