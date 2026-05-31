import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import classify  # noqa: E402
import config  # noqa: E402
import handlers  # noqa: E402
import store  # noqa: E402

_GCODE_H = ("; total layer number: 25\n; max_z_height: {h:.2f}\n"
            "; change_filament_gcode = ;=P1S 20250822=\n"  # machine marker the safety gate needs
            "G1 X1 Y1 Z250\n; EXECUTABLE_BLOCK_END\n")


def _setup(tmp_path, enabled):
    config.DB_PATH = str(tmp_path / "t.db")
    config.EJECT_MAX_HEIGHT_MM = 180
    store.init_db()
    store.set_flag("eject_enabled", enabled)


def _fake_queue(calls):
    async def fake(file_id, mapping, plate_id=None, gcode_injection=True):
        calls.append({"file_id": file_id, "inject": gcode_injection, "plate_id": plate_id})
        return {"id": 999}
    return fake


def _gcode(text):
    data = text.encode() if isinstance(text, str) else text
    async def fake(_file_id):
        return data
    return fake


def _gcode_zip(height, plate=1):
    """A .gcode.3mf-style zip blob with the plate gcode + md5 inside, like
    Bambuddy returns for sliced library files. ``plate`` lets a re-sliced file
    keep a non-1 plate index (e.g. plate 3 of a multi-plate MakerWorld model)."""
    import hashlib
    import io
    import zipfile
    g = _GCODE_H.format(h=height).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"Metadata/plate_{plate}.gcode", g)
        z.writestr(f"Metadata/plate_{plate}.gcode.md5", hashlib.md5(g).hexdigest().upper())
        z.writestr("Metadata/project_settings.config", "{}")
    return buf.getvalue()


def _slice_info_zip(model_id):
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Metadata/plate_1.gcode", b"; gcode")
        z.writestr("Metadata/slice_info.config",
                   f'<config><plate><metadata key="printer_model_id" value="{model_id}"/>'
                   '</plate></config>')
    return buf.getvalue()


def test_sliced_printer_model():
    assert handlers._sliced_printer_model(_slice_info_zip("N1")) == "N1"   # A1 mini
    assert handlers._sliced_printer_model(_slice_info_zip("")) == ""        # sidecar P1S
    assert handlers._sliced_printer_model(b"plain gcode") == ""
    # this printer's id resolves from config (P1S -> C12)
    assert handlers._TARGET_MODEL_ID == "C12"

    # the queue gate (strict allow-list): accept ONLY gcode identified as this
    # printer; reject foreign, unknown, AND unverifiable (raw .gcode / blank id)
    def rejected(model):
        return model != handlers._TARGET_MODEL_ID
    assert rejected("N1") and rejected("N2S")  # A1 mini / A1
    assert rejected("ZZ-NEW")                  # unknown non-blank
    assert rejected("")                        # raw .gcode / blank id → blocked too
    assert not rejected("C12")                 # the P1S itself → accepted


def test_gcode_plate_index():
    # a single-plate .gcode.3mf under a non-1 index → that index must be queued
    assert handlers._gcode_plate_index(_gcode_zip(20.0, plate=2)) == 2
    assert handlers._gcode_plate_index(_gcode_zip(20.0, plate=1)) == 1
    # plain .gcode text / junk → no plate concept
    assert handlers._gcode_plate_index(b"; plain gcode\nG1 X1\n") is None
    assert handlers._gcode_plate_index(b"") is None


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
    # The container is always fetched now (the safety gate reads its machine
    # marker); a P1S-marked file passes and, with eject off, is queued as-is.
    monkeypatch.setattr(handlers.bambuddy, "download_file", _gcode(_gcode_zip(20.0, plate=2)))

    queued, note = asyncio.run(handlers._queue_guarded("group.x", "+1", "Teil", 7, [0], plate_id=2))
    assert queued and note == ""
    # eject off: queue the file as-is, passing the plate index straight through
    assert calls == [{"file_id": 7, "inject": False, "plate_id": 2}]


def test_max_z_height_reads_zip_and_plain():
    # plain .gcode text
    assert handlers._max_z_height(_GCODE_H.format(h=12.5).encode()) == 12.5
    # .gcode.3mf zip container (the real library-file shape)
    assert handlers._max_z_height(_gcode_zip(33.0)) == 33.0
    # unreadable / missing header
    assert handlers._max_z_height(b"no header here") is None


def test_gate_on_short_print_queues_with_injection_flag(tmp_path, monkeypatch):
    _setup(tmp_path, enabled=True)
    calls = []
    monkeypatch.setattr(handlers.bambuddy, "queue", _fake_queue(calls))
    # realistic: /download gives the .gcode.3mf container (here keeping a non-1
    # plate index, like a re-sliced *_plate_3.gcode.3mf)
    monkeypatch.setattr(handlers.bambuddy, "download_file", _gcode(_gcode_zip(42.0, plate=3)))

    # a successful multi-plate re-slice passes plate_id (here 3); a P1S-marked
    # container under the height limit passes the safety gate
    queued, _ = asyncio.run(
        handlers._queue_guarded("group.x", "+1", "Teil", 7, [0], plate_id=3))
    assert queued
    # the bot no longer builds/uploads the eject itself: the ORIGINAL file is
    # queued with Bambuddy's per-model injection ON (the snippet runs at dispatch)
    # and the plate index forwarded
    assert calls == [{"file_id": 7, "inject": True, "plate_id": 3}]


def _foreign_zip(machine="A1mini", height=10.0, plate=1):
    """A .gcode.3mf whose plate gcode carries a *foreign* machine marker."""
    import hashlib
    import io
    import zipfile
    g = (f"; max_z_height: {height:.2f}\n"
         f"; change_filament_gcode = ;===== {machine} 20250206 =====\n"
         "G1 X1 Y1\n; EXECUTABLE_BLOCK_END\n").encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"Metadata/plate_{plate}.gcode", g)
        z.writestr(f"Metadata/plate_{plate}.gcode.md5", hashlib.md5(g).hexdigest().upper())
    return buf.getvalue()


def test_is_p1s_gcode_detection():
    # positively P1S → accepted
    assert handlers._is_p1s_gcode(_gcode_zip(20.0)) is True
    assert handlers._is_p1s_gcode(_GCODE_H.format(h=12.0).encode()) is True
    # foreign machine → declined, and named for the message
    assert handlers._is_p1s_gcode(_foreign_zip("A1mini")) is False
    assert handlers._gcode_machine_hint(_foreign_zip("A1mini")) == "A1mini"
    # unknown / no marker → declined (never assume P1S)
    assert handlers._is_p1s_gcode(b"G1 X1 Y1\nG1 X2 Y2\n") is False


def test_foreign_gcode_declined(tmp_path, monkeypatch):
    """Universal safety gate: a file not sliced for this printer is declined and
    never queued — the bot doesn't convert foreign-machine gcode."""
    _setup(tmp_path, enabled=False)
    calls = []
    monkeypatch.setattr(handlers.bambuddy, "queue", _fake_queue(calls))
    monkeypatch.setattr(handlers.bambuddy, "download_file", _gcode(_foreign_zip("A1mini")))

    queued, note = asyncio.run(
        handlers._queue_guarded("group.x", "+1", "Teil", 7, [0], plate_id=3))
    assert not queued and "🚫" in note
    assert "A1mini" in note  # names the foreign machine
    assert calls == []  # nothing reached the queue


def test_gate_on_tall_print_blocked(tmp_path, monkeypatch):
    _setup(tmp_path, enabled=True)
    calls = []
    monkeypatch.setattr(handlers.bambuddy, "queue", _fake_queue(calls))
    monkeypatch.setattr(handlers.bambuddy, "download_file", _gcode(_gcode_zip(210.0)))

    queued, note = asyncio.run(handlers._queue_guarded("group.x", "+1", "Turm", 7, [0]))
    assert not queued and "🚫" in note
    assert calls == []  # never queued


def test_gate_on_unknown_height_blocked(tmp_path, monkeypatch):
    _setup(tmp_path, enabled=True)
    calls = []
    monkeypatch.setattr(handlers.bambuddy, "queue", _fake_queue(calls))
    monkeypatch.setattr(handlers.bambuddy, "download_file", _gcode(None))

    queued, note = asyncio.run(handlers._queue_guarded("group.x", "+1", "Teil", 7, [0]))
    assert not queued and "🚫" in note
    assert calls == []
