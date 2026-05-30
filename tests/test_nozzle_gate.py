"""Nozzle safety gate: a file sliced for one nozzle must not run through another.
The P1S can't auto-detect its nozzle, so 0.4 gcode would silently jam a 0.2 nozzle.
Covers the pure helpers in handlers that back the gate in _queue_guarded."""
import io
import zipfile

import handlers


def _gcode_3mf(machine_header, plate_name="Metadata/plate_1.gcode"):
    """A minimal .gcode.3mf (zip) carrying one plate gcode with the given header."""
    body = (machine_header + "\nG90\nG1 X10 Y10 Z0.2 E.0234 F1200\n").encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(plate_name, body)
    return buf.getvalue()


P1S_04 = _gcode_3mf(";===== machine: P1S-0.4 ========================")
P1S_02 = _gcode_3mf(";===== machine: P1S-0.2 ========================")


def test_gcode_nozzle_reads_diameter():
    assert handlers._gcode_nozzle(P1S_04) == "0.4"
    assert handlers._gcode_nozzle(P1S_02) == "0.2"


def test_gcode_nozzle_missing():
    # change_filament-style header (";=P1S 20250822=") carries a date, not a nozzle.
    assert handlers._gcode_nozzle(_gcode_3mf(";=P1S 20250822=")) == ""
    assert handlers._gcode_nozzle(b"") == ""


def test_both_p1s_nozzles_still_pass_machine_gate():
    # The nozzle gate is separate from the machine gate: a P1S-0.2 file is still
    # positively P1S (it's only declined if the *mounted* nozzle differs).
    assert handlers._is_p1s_gcode(P1S_04)
    assert handlers._is_p1s_gcode(P1S_02)


def test_nozzle_eq():
    assert handlers._nozzle_eq("0.4", "0.4")
    assert handlers._nozzle_eq("0.4", "0.40")
    assert not handlers._nozzle_eq("0.4", "0.2")
    assert not handlers._nozzle_eq("0.4", "")        # unknown → not equal
    assert handlers._nozzle_eq("0.6", "0.6")
