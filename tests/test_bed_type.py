import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import bambuddy  # noqa: E402
import classify  # noqa: E402
import config  # noqa: E402
import handlers  # noqa: E402
import store  # noqa: E402

_REF = {"source": "cloud", "id": "x"}


def _capture(monkeypatch):
    calls = []

    async def fake_post(path, body):
        calls.append({"path": path, "body": body})
        return {"job_id": "j1"}

    monkeypatch.setattr(bambuddy, "_post", fake_post)
    return calls


def test_slice_file_includes_bed_type(monkeypatch):
    calls = _capture(monkeypatch)
    asyncio.run(bambuddy.slice_file(5, _REF, _REF, [_REF], plate=2, bed_type="Cool Plate"))
    body = calls[0]["body"]
    assert body["bed_type"] == "Cool Plate"
    assert body["plate"] == 2


def test_slice_file_omits_bed_type_when_none(monkeypatch):
    calls = _capture(monkeypatch)
    asyncio.run(bambuddy.slice_file(5, _REF, _REF, [_REF]))
    assert "bed_type" not in calls[0]["body"]


def test_plate_command_parses():
    assert classify.plate_command("hello") is None
    assert classify.plate_command("!platte")["action"] == "status"
    assert classify.plate_command("!platte cool") == {"action": "set", "bed_type": "Cool Plate"}
    assert classify.plate_command("!bett textured pei")["bed_type"] == "Textured PEI Plate"
    assert classify.plate_command("!plate SUPERTACK")["bed_type"] == "Supertack Plate"


def test_every_bed_alias_is_a_name_the_slicer_honours():
    """An unhonoured plate name is worse than a rejected one: the API takes it, the
    user gets a confirmation, and the slice quietly comes out as Cool Plate. That
    is exactly how '!platte supertack' produced a 55 °C Cool Plate first layer on a
    SuperTack plate. Keep the alias targets pinned to what was verified against the
    real slicer."""
    unhonoured = {v for v in classify.BED_ALIASES.values()
                  if v not in classify.VERIFIED_BED_TYPES}
    assert not unhonoured
    assert classify.plate_command("!platte banana") == {"action": "unknown", "arg": "banana"}


def test_plate_set_persists_and_reslice_reads_it(tmp_path, monkeypatch):
    config.DB_PATH = str(tmp_path / "t.db")
    store.init_db()
    monkeypatch.setattr(handlers.signal_client, "send_to_group",
                        lambda *a, **k: asyncio.sleep(0))
    # default falls back to config.BED_TYPE before anything is set
    assert handlers._bed_type() == config.BED_TYPE
    asyncio.run(handlers._plate("g1", classify.plate_command("!platte textured")))
    assert store.get_setting(handlers.BED_TYPE_FLAG) == "Textured PEI Plate"
    assert handlers._bed_type() == "Textured PEI Plate"
