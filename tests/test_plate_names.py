"""Plate-aware naming for queue items.

A Bambu-Studio "Send All" queues one item per plate, every one of them carrying
the *3mf's* name — so !liste showed N identical lines. The plate's real name
lives behind one API call, keyed by the item's ``plate_id``.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config  # noqa: E402
import handlers  # noqa: E402
import store  # noqa: E402

MODEL = "Montessori Number Sticks - 5-color optimized"


@pytest.fixture(autouse=True)
def _clear_plate_cache():
    """The plate cache is module-level and TTL'd — without this it leaks between
    tests and a later test silently reuses an earlier one's stub."""
    handlers._PLATE_CACHE.clear()
    yield
    handlers._PLATE_CACHE.clear()


def _archive_item(item_id, plate_id, archive_id=108, status="pending"):
    return {"id": item_id, "status": status, "archive_id": archive_id,
            "archive_name": MODEL, "library_file_id": None,
            "library_file_name": None, "plate_id": plate_id}


def _plates(*names, start=1):
    return {"plates": [{"index": i, "name": n}
                       for i, n in enumerate(names, start)]}


def _stub_archive(monkeypatch, payload, counter=None):
    async def fake(archive_id):
        if counter is not None:
            counter.append(archive_id)
        return payload(archive_id) if callable(payload) else payload
    monkeypatch.setattr(handlers.bambuddy, "archive_plates", fake)


# ----- _plate_key -----

def test_plate_key_prefers_archive():
    assert handlers._plate_key({"archive_id": 7, "library_file_id": 9}) == ("a", 7)
    assert handlers._plate_key({"library_file_id": 9}) == ("l", 9)
    assert handlers._plate_key({}) is None


# ----- naming -----

def test_item_name_uses_real_plate_name(monkeypatch):
    """The whole point: two items of one archive must read differently."""
    _stub_archive(monkeypatch, _plates("4 & 9", "2 & 7", "1 & 6", "5 & 10",
                                       "3 & 8", "half height Box2", "half height Box1"))
    items = [_archive_item(113, 6), _archive_item(114, 7)]
    plates = asyncio.run(handlers._plate_names(items))
    assert handlers._item_name(items[0], plates) == f"half height Box2 — {MODEL}"
    assert handlers._item_name(items[1], plates) == f"half height Box1 — {MODEL}"


def test_item_name_falls_back_to_plate_n_when_unnamed(monkeypatch):
    _stub_archive(monkeypatch, {"plates": [{"index": 1, "name": ""},
                                           {"index": 2, "name": "  "}]})
    it = _archive_item(1, 2)
    plates = asyncio.run(handlers._plate_names([it]))
    assert handlers._item_name(it, plates) == f"Plate 2 — {MODEL}"


def test_no_prefix_for_single_plate_file(monkeypatch):
    """'Plate 1 — Benchy' would be noise on the ordinary single-plate case."""
    _stub_archive(monkeypatch, _plates("only one"))
    it = _archive_item(1, 1)
    plates = asyncio.run(handlers._plate_names([it]))
    assert handlers._item_name(it, plates) == MODEL


def test_item_name_degrades_when_lookup_fails(monkeypatch):
    async def boom(archive_id):
        raise RuntimeError("archive gone")
    monkeypatch.setattr(handlers.bambuddy, "archive_plates", boom)

    first, later = _archive_item(1, 1), _archive_item(2, 3)
    plates = asyncio.run(handlers._plate_names([first, later]))
    assert plates == {}
    # plate_id 1 alone proves nothing → plain name; >1 proves multi-plate.
    assert handlers._item_name(first, plates) == MODEL
    assert handlers._item_name(later, plates) == f"Plate 3 — {MODEL}"


def test_item_name_without_plate_id_is_unchanged():
    it = {"id": 5, "library_file_name": "Benchy"}
    assert handlers._item_name(it) == "Benchy"
    assert handlers._item_name(it, {}) == "Benchy"


def test_item_name_falls_back_to_hash_id():
    assert handlers._item_name({"id": 42}) == "#42"


# ----- efficiency contract -----

def test_plate_names_dedupes_http_calls(monkeypatch):
    """65 items sharing 2 archives must cost exactly 2 calls, not 65."""
    calls = []
    _stub_archive(monkeypatch, _plates("a", "b", "c"), counter=calls)
    items = [_archive_item(i, (i % 3) + 1, archive_id=100 + (i % 2)) for i in range(65)]
    asyncio.run(handlers._plate_names(items))
    assert sorted(calls) == [100, 101]


def test_plate_names_cache_hits_across_calls(monkeypatch):
    calls = []
    _stub_archive(monkeypatch, _plates("a", "b"), counter=calls)
    items = [_archive_item(1, 2)]
    asyncio.run(handlers._plate_names(items))
    asyncio.run(handlers._plate_names(items))
    assert len(calls) == 1


def test_failed_lookup_is_cached_too(monkeypatch):
    """A dead archive must not be re-fetched on every 60s poll."""
    calls = []

    async def boom(archive_id):
        calls.append(archive_id)
        raise RuntimeError("nope")
    monkeypatch.setattr(handlers.bambuddy, "archive_plates", boom)
    items = [_archive_item(1, 2)]
    asyncio.run(handlers._plate_names(items))
    asyncio.run(handlers._plate_names(items))
    assert len(calls) == 1


def test_library_items_use_list_plates(monkeypatch):
    async def fake_list_plates(lfid):
        return _plates("Left", "Right")
    monkeypatch.setattr(handlers.bambuddy, "list_plates", fake_list_plates)
    it = {"id": 1, "status": "pending", "library_file_id": 55,
          "library_file_name": "Bracket", "plate_id": 2}
    plates = asyncio.run(handlers._plate_names([it]))
    assert handlers._item_name(it, plates) == "Right — Bracket"


# ----- !liste end to end -----

def test_list_renders_plates_distinguishably(tmp_path, monkeypatch):
    config.DB_PATH = str(tmp_path / "t.db")
    store.init_db()
    sent = []

    async def fake_send(group_id, msg, **k):
        sent.append(msg)

    items = [_archive_item(113, 6, status="printing"), _archive_item(114, 7)]

    async def fake_list_queue():
        return items
    monkeypatch.setattr(handlers.signal_client, "send_to_group", fake_send)
    monkeypatch.setattr(handlers.bambuddy, "list_queue", fake_list_queue)
    _stub_archive(monkeypatch, _plates("4 & 9", "2 & 7", "1 & 6", "5 & 10",
                                       "3 & 8", "half height Box2", "half height Box1"))

    asyncio.run(handlers._list("g1"))
    out = sent[-1]
    assert "half height Box2" in out and "half height Box1" in out
    assert out.count(MODEL) == 2       # model name still there, once per line


# ----- adopted trackers keep their plate identity -----

def test_adopt_item_persists_plate_identity(tmp_path, monkeypatch):
    """Regression: trackers used to store NULL plate_index/library_file_id, so the
    completion poller could never render the per-plate thumbnail."""
    config.DB_PATH = str(tmp_path / "t.db")
    store.init_db()
    _stub_archive(monkeypatch, _plates("A", "B", "C"))
    it = _archive_item(113, 3)
    plates = asyncio.run(handlers._plate_names([it]))

    handlers._adopt_item("g1", it, plates)

    job = store.queued_jobs_with_item()[0]
    assert job["plate_index"] == 3
    assert job["plate_name"] == "C"
    assert job["archive_id"] == 108
    assert job["model_name"] == f"C — {MODEL}"
