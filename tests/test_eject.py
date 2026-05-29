import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import classify  # noqa: E402
import config  # noqa: E402
import eject  # noqa: E402
import handlers  # noqa: E402
import store  # noqa: E402

_GCODE_H = ("; total layer number: 25\n; max_z_height: {h:.2f}\n"
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


def _capture_upload(uploads):
    async def fake_ensure(_name):
        return 2
    async def fake_upload(content, filename, folder_id=None):
        uploads.append({"content": content, "filename": filename, "folder_id": folder_id})
        return {"id": 555}
    return fake_ensure, fake_upload


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


def test_eject_filename_readable_and_unique():
    a = handlers._eject_filename("Flexy+Luci.3mf — Body.stl", b"content-A")
    b = handlers._eject_filename("Flexy+Luci.3mf — Body.stl", b"content-B")
    # readable model base kept, source extension stripped, ends in .gcode.3mf
    assert a.startswith("Flexy+Luci.3mf — Body_") and a.endswith(".gcode.3mf")
    # same label but different content → different name (no stale-file collision)
    assert a != b
    # identical content → stable name
    assert a == handlers._eject_filename("Flexy+Luci.3mf — Body.stl", b"content-A")
    # empty/garbage label still yields a valid unique name
    junk = handlers._eject_filename("   .stl", b"x")
    assert junk.startswith("eject_") and junk.endswith(".gcode.3mf")


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
    # the incompatible set catches the A-series bed-slingers
    assert "N1" in handlers._INCOMPATIBLE_MODELS and "N2S" in handlers._INCOMPATIBLE_MODELS
    assert "C12" not in handlers._INCOMPATIBLE_MODELS  # P1S itself is fine


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

    async def boom(_):
        raise AssertionError("should not fetch the container when eject off")
    monkeypatch.setattr(handlers.bambuddy, "download_file", boom)

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


def test_gate_on_short_print_injects(tmp_path, monkeypatch):
    _setup(tmp_path, enabled=True)
    calls, uploads = [], []
    monkeypatch.setattr(handlers.bambuddy, "queue", _fake_queue(calls))
    # realistic: /download gives the .gcode.3mf container (here keeping a non-1
    # plate index, like a re-sliced *_plate_3.gcode.3mf)
    monkeypatch.setattr(handlers.bambuddy, "download_file", _gcode(_gcode_zip(42.0, plate=3)))
    fake_ensure, fake_upload = _capture_upload(uploads)
    monkeypatch.setattr(handlers.bambuddy, "ensure_folder", fake_ensure)
    monkeypatch.setattr(handlers.bambuddy, "upload_library_file", fake_upload)

    # a successful multi-plate re-slice passes plate_id (here 3); eject must still
    # inject (not refuse — that's only the is_fallback case)
    queued, _ = asyncio.run(
        handlers._queue_guarded("group.x", "+1", "Teil", 7, [0], plate_id=3))
    assert queued
    # the eject is injected into a re-uploaded file; that new file is queued with
    # Bambuddy's own injection OFF (we did it) and the plate index forwarded
    assert calls == [{"file_id": 555, "inject": False, "plate_id": 3}]
    assert len(uploads) == 1
    assert uploads[0]["filename"].endswith(".gcode.3mf")
    # the re-uploaded container actually carries the height-tailored eject + valid md5
    import hashlib
    import io
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(uploads[0]["content"]))
    g = z.read("Metadata/plate_3.gcode")
    assert b"Farmloop Auto-Eject" in g
    assert b"H=42.0mm" in g
    assert z.read("Metadata/plate_3.gcode.md5").decode() == hashlib.md5(g).hexdigest().upper()


def test_eject_fallback_multiplate_blocked(tmp_path, monkeypatch):
    _setup(tmp_path, enabled=True)
    calls = []
    monkeypatch.setattr(handlers.bambuddy, "queue", _fake_queue(calls))

    async def boom(_):
        raise AssertionError("should not fetch the container for the blocked fallback path")
    monkeypatch.setattr(handlers.bambuddy, "download_file", boom)

    # is_fallback = re-slice failed, we'd queue the original multi-plate file →
    # auto-eject refuses (can't inject one plate's eject into a multi-plate file)
    queued, note = asyncio.run(
        handlers._queue_guarded("group.x", "+1", "Teil", 7, [0], plate_id=3, is_fallback=True))
    assert not queued and "🚫" in note
    assert calls == []


def test_build_end_gcode_height_scales():
    tall = eject.build_end_gcode(150.0)
    short = eject.build_end_gcode(5.0)
    # sweep height tracks the print: 150 - OVERSHOOT(4) = 146.0
    assert "Z146.0 F" in tall
    # a print shorter than the overshoot clamps to MIN_SWEEP_Z, never below the bed
    assert f"Z{eject.MIN_SWEEP_Z:.1f} F" in short
    # bender always flexes deep regardless of height
    assert f"Z{eject.BENDER_DEEP_Z:.1f} F" in tall
    # one sweep pass per lane
    for x in eject.LANES_X:
        assert f"X{x:.2f} Y{eject.Y_BACK:.1f}" in tall


def test_build_end_gcode_parks_neutral_and_downward():
    """The bed ends at a neutral rest height — not slammed to the bottom, but
    above the tallest possible sweep so the final move is always downward (a
    still-stuck tall part can't be rammed into the nozzle)."""
    import re
    zlines = [float(re.search(r"Z([0-9.]+)", l).group(1))
              for l in eject.build_end_gcode(5.0).splitlines() if l.startswith("G0 Z")]
    assert zlines[-1] == eject.PARK_Z              # ends parked at PARK_Z
    assert eject.PARK_Z < eject.BENDER_DEEP_Z      # not bottomed out
    # above the tallest allowed sweep (180mm print − overshoot) → always downward
    assert eject.PARK_Z > 180 - eject.OVERSHOOT_MM


def test_sweep_lanes_cover_centre_continuously():
    """With the ~55mm sweeper, adjacent lanes must overlap so there are no gaps,
    and the central band gets the dense (overlapping) coverage."""
    h = eject.SWEEPER_WIDTH_MM / 2
    lanes = sorted(eject.LANES_X)
    # no gaps between adjacent lanes (centres closer than the sweeper width)
    for a, b in zip(lanes, lanes[1:]):
        assert (b - a) <= eject.SWEEPER_WIDTH_MM
    # the bed centre is within the covered span
    assert lanes[0] - h <= 128 <= lanes[-1] + h


def test_build_end_gcode_only_p1s_safe_commands():
    """The P1S firmware rejects the whole file (HMS 0500-4003 'unable to parse')
    on a foreign command. Its own gcode never disables steppers — no M84/M18.
    Guard against reintroducing one. Every command must be P1S-native."""
    import re
    gc = eject.build_end_gcode(40.0)
    used = {re.match(r"^([GMT]\d+(?:\.\d+)?)", l.split(";", 1)[0].strip()).group(1)
            for l in gc.splitlines()
            if re.match(r"^([GMT]\d+(?:\.\d+)?)", l.split(";", 1)[0].strip())}
    assert "M84" not in used and "M18" not in used
    # whitelist of commands Bambu's own P1S slices emit (incl. G28 for homing)
    assert used <= {"G0", "G1", "G28", "G90", "M17", "M104", "M140"}


def test_inject_3mf_roundtrip():
    import hashlib
    import io
    import zipfile
    data = _gcode_zip(80.0)
    out = eject.inject_3mf(data, 80.0)
    z = zipfile.ZipFile(io.BytesIO(out))
    g = z.read("Metadata/plate_1.gcode")
    # eject is appended *before* the executable block end marker
    assert g.index(b"Farmloop Auto-Eject") < g.index(b"; EXECUTABLE_BLOCK_END")
    # md5 sidecar matches the rewritten gcode, uppercase, no trailing newline
    side = z.read("Metadata/plate_1.gcode.md5")
    assert side == hashlib.md5(g).hexdigest().upper().encode()
    # other members are preserved
    assert "Metadata/project_settings.config" in z.namelist()


def test_inject_3mf_non_plate1_index():
    """A re-sliced single-plate file keeps the source plate index (e.g. plate 3
    of a multi-plate MakerWorld model). Inject into whatever plate gcode is
    present, not a hardcoded plate_1."""
    import hashlib
    import io
    import zipfile
    out = eject.inject_3mf(_gcode_zip(80.0, plate=3), 80.0)
    z = zipfile.ZipFile(io.BytesIO(out))
    assert "Metadata/plate_3.gcode" in z.namelist()
    g = z.read("Metadata/plate_3.gcode")
    assert b"Farmloop Auto-Eject" in g
    assert z.read("Metadata/plate_3.gcode.md5") == hashlib.md5(g).hexdigest().upper().encode()


def test_inject_3mf_preserves_compression():
    """The slicer STOREs embedded PNGs/thumbnails; the P1S preview parser rejects
    the file if we re-DEFLATE them. Each member must keep its source compress_type
    (only the gcode + md5 change)."""
    import hashlib
    import io
    import zipfile
    g = _GCODE_H.format(h=20.0).encode()
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 500  # incompressible-ish dummy
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Metadata/plate_1.gcode", g, zipfile.ZIP_DEFLATED)
        z.writestr("Metadata/plate_1.gcode.md5", hashlib.md5(g).hexdigest().upper(), zipfile.ZIP_DEFLATED)
        z.writestr("Metadata/plate_1.png", png, zipfile.ZIP_STORED)
    out = eject.inject_3mf(buf.getvalue(), 20.0)
    zo = zipfile.ZipFile(io.BytesIO(out))
    info = {i.filename: i for i in zo.infolist()}
    assert info["Metadata/plate_1.png"].compress_type == zipfile.ZIP_STORED
    assert info["Metadata/plate_1.gcode"].compress_type == zipfile.ZIP_DEFLATED
    assert zo.read("Metadata/plate_1.png") == png  # bytes untouched


def test_inject_3mf_rejects_non_zip():
    import pytest
    with pytest.raises(ValueError):
        eject.inject_3mf(b"not a zip", 50.0)


def test_inject_3mf_rejects_multiplate_container():
    """More than one plate gcode = a still-multi-plate container; the eject path
    can't tailor one sweep height to several plates, so refuse it."""
    import io
    import pytest
    import zipfile
    one = _gcode_zip(80.0, plate=1)
    z1 = zipfile.ZipFile(io.BytesIO(one))
    g = z1.read("Metadata/plate_1.gcode")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Metadata/plate_1.gcode", g)
        z.writestr("Metadata/plate_2.gcode", g)
    with pytest.raises(ValueError):
        eject.inject_3mf(buf.getvalue(), 80.0)


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
