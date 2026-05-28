import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import classify  # noqa: E402
import config  # noqa: E402
import handlers  # noqa: E402
import store  # noqa: E402

_GCODE_H = "; total layer number: 25\n; max_z_height: {h:.2f}\nG1 X1 Y1 Z250\n"


def _setup(tmp_path, enabled):
    config.DB_PATH = str(tmp_path / "t.db")
    config.EJECT_MAX_HEIGHT_MM = 180
    store.init_db()
    store.set_flag("eject_enabled", enabled)


def _fake_queue(calls):
    async def fake(file_id, mapping, plate_id=None, gcode_injection=True):
        calls.append({"file_id": file_id, "inject": gcode_injection})
        return {"id": 999}
    return fake


def _gcode(text):
    async def fake(_file_id):
        return text
    return fake


def test_eject_command_parsing():
    assert classify.eject_command("!eject on") == "on"
    assert classify.eject_command("!eject an") == "on"
    assert classify.eject_command("!auswurf aus") == "off"
    assert classify.eject_command("!eject") == "status"
    assert classify.eject_command("!eject status") == "status"
    assert classify.eject_command("eject on") is None  # needs the ! prefix
    assert classify.eject_command("3 1 2") is None


def test_flag_roundtrip(tmp_path):
    config.DB_PATH = str(tmp_path / "t.db")
    store.init_db()
    assert store.get_flag("eject_enabled", False) is False  # default when unset
    store.set_flag("eject_enabled", True)
    assert store.get_flag("eject_enabled", False) is True
    store.set_flag("eject_enabled", False)
    assert store.get_flag("eject_enabled", False) is False


def test_gate_off_queues_without_injection(tmp_path, monkeypatch):
    _setup(tmp_path, enabled=False)
    calls = []
    monkeypatch.setattr(handlers.bambuddy, "queue", _fake_queue(calls))

    async def boom(_):
        raise AssertionError("should not fetch gcode when eject off")
    monkeypatch.setattr(handlers.bambuddy, "get_gcode", boom)

    queued, note = asyncio.run(handlers._queue_guarded("group.x", "+1", "Teil", 7, [0]))
    assert queued and note == ""
    assert calls == [{"file_id": 7, "inject": False}]


def test_gate_on_short_print_injects(tmp_path, monkeypatch):
    _setup(tmp_path, enabled=True)
    calls = []
    monkeypatch.setattr(handlers.bambuddy, "queue", _fake_queue(calls))
    monkeypatch.setattr(handlers.bambuddy, "get_gcode", _gcode(_GCODE_H.format(h=42.0)))

    queued, _ = asyncio.run(handlers._queue_guarded("group.x", "+1", "Teil", 7, [0]))
    assert queued
    assert calls == [{"file_id": 7, "inject": True}]


def test_gate_on_tall_print_blocked(tmp_path, monkeypatch):
    _setup(tmp_path, enabled=True)
    calls = []
    monkeypatch.setattr(handlers.bambuddy, "queue", _fake_queue(calls))
    monkeypatch.setattr(handlers.bambuddy, "get_gcode", _gcode(_GCODE_H.format(h=210.0)))

    queued, note = asyncio.run(handlers._queue_guarded("group.x", "+1", "Turm", 7, [0]))
    assert not queued and "🚫" in note
    assert calls == []  # never queued


def test_gate_on_unknown_height_blocked(tmp_path, monkeypatch):
    _setup(tmp_path, enabled=True)
    calls = []
    monkeypatch.setattr(handlers.bambuddy, "queue", _fake_queue(calls))
    monkeypatch.setattr(handlers.bambuddy, "get_gcode", _gcode(None))

    queued, note = asyncio.run(handlers._queue_guarded("group.x", "+1", "Teil", 7, [0]))
    assert not queued and "🚫" in note
    assert calls == []
