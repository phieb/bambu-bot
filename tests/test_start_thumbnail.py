"""The print-start notification attaches a thumbnail. It has to be base64 by the
time it reaches Signal — raw PNG bytes raise inside httpx's JSON encoder, and
because that send happens in the completion poller it took down the entire poll
cycle (no start, no finish, no intervention alerts for *any* tracker).

The bug only became reachable once trackers started storing library_file_id /
plate_index (before that _job_thumbnail always returned None → no attachment).
"""
import asyncio
import io
import json

import pytest

import config
import handlers
import signal_client
import store


def _png(size=(40, 30)):
    Image = pytest.importorskip("PIL.Image", reason="Pillow needed for thumbnails")
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 120, 200)).save(buf, format="PNG")
    return buf.getvalue()


def test_job_thumbnail_is_base64_not_raw_bytes(monkeypatch):
    raw = _png()

    async def _plate(_lfid, _idx):
        return raw
    monkeypatch.setattr(handlers.bambuddy, "plate_thumbnail", _plate)

    thumb = asyncio.run(handlers._job_thumbnail(
        {"library_file_id": 7, "plate_index": 2}))
    assert isinstance(thumb, str)
    # and it survives a JSON round-trip, which is what the send actually does
    json.dumps({"base64_attachments": [thumb]})


def test_job_thumbnail_prefers_archive_render(monkeypatch):
    """Adopted Studio prints live in an archive; the library lookup can't see
    them. Falling through to the library must still work when there's no
    archive render."""
    calls = []

    async def _archive(aid, idx):
        calls.append(("archive", aid, idx))
        return _png()

    async def _plate(*_a):
        calls.append(("library",))
        return None
    monkeypatch.setattr(handlers.bambuddy, "archive_plate_thumbnail", _archive)
    monkeypatch.setattr(handlers.bambuddy, "plate_thumbnail", _plate)

    thumb = asyncio.run(handlers._job_thumbnail(
        {"archive_id": 3, "plate_index": 2, "library_file_id": 7}))
    assert isinstance(thumb, str)
    assert calls == [("archive", 3, 2)]


def test_send_encodes_raw_byte_attachments(monkeypatch):
    """Backstop: whatever a caller hands over, the request body stays JSON-safe."""
    bodies = []

    class _FakeResponse:
        content = b""

        def raise_for_status(self):
            pass

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def post(self, _url, json=None):
            bodies.append(json)
            return _FakeResponse()

    monkeypatch.setattr(signal_client.httpx, "AsyncClient", lambda **_kw: _FakeClient())

    asyncio.run(signal_client.send_to_group("group.x", "hi", attachments=[b"\x89PNG"]))
    json.dumps(bodies[0])  # would raise on raw bytes
    assert bodies[0]["base64_attachments"] == ["iVBORw=="]


def test_poll_survives_a_thumbnail_and_still_announces(tmp_path, monkeypatch):
    """End-to-end shape of the regression: a tracker with a resolvable thumbnail
    must produce a start message, not an exception."""
    config.DB_PATH = str(tmp_path / "t.db")
    store.init_db()
    store.add_queued("group.x", "+1", "Benchy", 4, 42, plate_index=1)

    async def _plate(_lfid, _idx):
        return _png()

    async def _get_item(_id):
        return {"status": "printing", "print_time_seconds": 600}

    async def _pstatus(_pid):
        return {"state": "RUNNING", "remaining_time": 10}
    monkeypatch.setattr(handlers.bambuddy, "plate_thumbnail", _plate)
    monkeypatch.setattr(handlers.bambuddy, "get_queue_item", _get_item)
    monkeypatch.setattr(handlers.bambuddy, "printer_status", _pstatus)

    sent = []

    async def _send(gid, msg, attachments=None):
        # mirrors signal_client's contract: attachments must be JSON-encodable
        json.dumps({"m": msg, "a": attachments})
        sent.append((msg, attachments))
    monkeypatch.setattr(handlers.signal_client, "send_to_group", _send)

    asyncio.run(handlers._check_completions())
    assert len(sent) == 1 and "druckt jetzt" in sent[0][0]
    assert sent[0][1] and isinstance(sent[0][1][0], str)
