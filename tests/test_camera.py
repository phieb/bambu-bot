import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import bambuddy  # noqa: E402


class _Resp:
    def __init__(self, content=b"", ct="image/jpeg", status=200):
        self.content = content
        self.headers = {"content-type": ct}
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Client:
    """Stands in for httpx.AsyncClient, replaying a queued list of responses
    (an Exception instance is raised instead of returned)."""

    def __init__(self, responses):
        self._responses = responses
        self.calls = 0

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        r = self._responses[self.calls]
        self.calls += 1
        if isinstance(r, Exception):
            raise r
        return r


def _run(monkeypatch, responses):
    client = _Client(responses)
    monkeypatch.setattr(bambuddy.httpx, "AsyncClient", client)
    return asyncio.run(bambuddy.camera_snapshot(1)), client


def test_snapshot_returns_image_bytes(monkeypatch):
    got, client = _run(monkeypatch, [_Resp(b"\xff\xd8jpeg")])
    assert got == b"\xff\xd8jpeg"
    assert client.calls == 1  # no needless retry on success


def test_snapshot_retries_after_transient_failure(monkeypatch):
    """A stalled first snapshot must not silently cost the photo."""
    got, client = _run(monkeypatch, [TimeoutError("read timeout"), _Resp(b"\xff\xd8ok")])
    assert got == b"\xff\xd8ok"
    assert client.calls == 2


def test_snapshot_retries_when_response_is_not_an_image(monkeypatch):
    got, client = _run(monkeypatch, [_Resp(b"nope", ct="text/html"), _Resp(b"\xff\xd8ok")])
    assert got == b"\xff\xd8ok"
    assert client.calls == 2


def test_snapshot_gives_up_and_logs(monkeypatch, caplog):
    """Exhausted retries → None, but the reason is in the log (used to vanish)."""
    with caplog.at_level("WARNING"):
        got, client = _run(monkeypatch, [TimeoutError("boom"), _Resp(b"", ct="")])
    assert got is None
    assert client.calls == 2
    assert "camera snapshot" in caplog.text.lower()
