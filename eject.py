"""Per-print Farmloop auto-eject end-gcode, injected into the sliced file.

Why per-print: Bambu's own machine-end sequence parks the bed at ``max_layer_z
+ 98`` and moves the toolhead to the wipe/poop area, so by the time any appended
end-gcode runs the "bed at print height" reference is gone. Bambuddy's global
``gcode_snippets`` snippet is one static block for every print, so it can only
sweep at a fixed absolute Z — which crashes any part taller than that Z into the
gantry. Instead the bot, which already knows the print height ``H`` per job,
emits ABSOLUTE moves tailored to ``H``:

  1. Bender flex at deep Z (bed all the way down, away from the gantry → safe for
     any height) to crack the part loose.
  2. Foam sweep at ``Z = H - OVERSHOOT`` (bed a touch higher than the print top so
     the foam grabs the part body, not just glides over the top edge), pushing
     front toward the bin while the tilted bed + gravity help.

The injected block replaces Bambuddy's global injection: the job is queued with
``gcode_injection=False``. The ``.gcode.3mf`` is a zip; the printer validates the
plate gcode against its ``.md5`` sidecar, so that is recomputed.
"""
import hashlib
import io
import zipfile

# --- tunables (measure/verify at the machine) ---
OVERSHOOT_MM = 4.0      # >>> TUNE: bed sits this much higher than the print top so
                        # the foam grabs the part body. Bigger = more bite + more
                        # of the part pokes above the nozzle plane (toward gantry).
MIN_SWEEP_Z = 1.5       # never bring the bed closer than this to the nozzle
BENDER_DEEP_Z = 250.0   # >>> TUNE: bed fully down to flex against the bender clip
BENDER_REL_Z = 200.0    # >>> TUNE: partial lift between flexes
BENDER_CYCLES = 3
LANES_X = (20.0, 98.33, 176.66, 250.0)  # >>> TUNE: X lanes to cover the bed width
Y_BACK = 250.0          # sweep start (rear)
Y_FRONT = 0.0           # sweep end (front, toward the bin)
CLEARANCE_DROP = 30.0   # bed drop (down) between lanes to reposition w/o dragging
SWEEP_F = 3500
Z_F = 3000

_PLATE_PREFIX = "Metadata/plate_"
_GCODE_SUFFIX = ".gcode"
_BLOCK_END = "; EXECUTABLE_BLOCK_END"


def _find_plate_gcode(names):
    """The single ``Metadata/plate_N.gcode`` member to inject into. A re-sliced
    single-plate file keeps the source plate's index (e.g. plate 3 of a
    multi-plate MakerWorld model → ``plate_3.gcode``), so we don't assume
    ``plate_1``. More than one plate gcode means a still-multi-plate container,
    which the eject path doesn't support."""
    plates = [n for n in names
              if n.startswith(_PLATE_PREFIX) and n.endswith(_GCODE_SUFFIX)]
    if not plates:
        raise ValueError("no Metadata/plate_*.gcode in container")
    if len(plates) != 1:
        raise ValueError(f"expected one plate gcode, found {len(plates)}: {plates}")
    return plates[0]


def build_end_gcode(height_mm):
    """Farmloop eject sequence (absolute coords) tailored to a print height."""
    z_sweep = max(height_mm - OVERSHOOT_MM, MIN_SWEEP_Z)
    z_clear = z_sweep + CLEARANCE_DROP
    out = [
        f"; ===== Farmloop Auto-Eject (bot, H={height_mm:.1f}mm, Z_sweep={z_sweep:.1f}) =====",
        "M17 X0.8 Y0.8 Z0.5 ; 45% Motorstrom (sanft, verliert bei Widerstand lieber Schritte)",
        "G90 ; absolute Koordinaten",
        "M104 S0 ; Hotend aus",
        "M140 S0 ; Bett aus",
        "; --- 1. Bender: Bett tief biegen, Haftung loesen (Bett weg von Gantry = hoehensicher) ---",
        f"G0 Z{BENDER_DEEP_Z:.1f} F{Z_F}",
        f"G0 X20 Y240 F{SWEEP_F} ; Toolhead aus dem Weg bei tiefem Bett",
    ]
    for _ in range(BENDER_CYCLES):
        out.append(f"G0 Z{BENDER_DEEP_Z:.1f} F{Z_F}")
        out.append(f"G0 Z{BENDER_REL_Z:.1f} F{Z_F}")
    out.append(f"G0 Z{BENDER_DEEP_Z:.1f} F{Z_F}")
    out.append("; --- 2. Sweep: Moosgummi schiebt auf Greifhoehe von hinten nach vorn ---")
    for x in LANES_X:
        out.append(f"G0 Z{z_clear:.1f} F{Z_F} ; Bett runter = Freiraum zum Repositionieren")
        out.append(f"G0 X{x:.2f} Y{Y_BACK:.1f} F{SWEEP_F} ; hinter das Teil")
        out.append(f"G0 Z{z_sweep:.1f} F{Z_F} ; Bett hoch auf Greifhoehe (H-Ueberhub)")
        out.append(f"G1 Y{Y_FRONT:.1f} F{SWEEP_F} ; nach vorne schieben (+ Schwerkraft, schraeges Bett)")
    out += [
        "; --- Park ---",
        f"G0 Z{BENDER_DEEP_Z:.1f} F{Z_F} ; Bett runter, sicher",
        # No M84 here: Bambu's P1S firmware never emits M84/M18 — its own end
        # gcode only lowers motor current with M17 and leaves the steppers
        # energised. An M84 is foreign to the P1S vocabulary and makes the
        # printer reject the whole file at load (HMS 0500-4003 "unable to parse").
        "; ===== Eject Ende (Motoren bleiben an, wie bei Bambus P1S-End) =====",
    ]
    return "\n".join(out) + "\n"


def inject_3mf(data, height_mm):
    """Return new ``.gcode.3mf`` bytes with the height-tailored eject appended to
    the plate gcode (before EXECUTABLE_BLOCK_END) and the ``.md5`` recomputed.
    Raises ValueError if the container isn't the expected single-plate gcode 3mf."""
    if data[:2] != b"PK":
        raise ValueError("not a .gcode.3mf zip container")
    zin = zipfile.ZipFile(io.BytesIO(data))
    infos = zin.infolist()
    plate_gcode = _find_plate_gcode([i.filename for i in infos])
    plate_md5 = plate_gcode + ".md5"
    # Work on raw bytes, not decoded text: the gcode is normally ASCII but a
    # lossy decode/encode round-trip must never silently alter it.
    gbytes = zin.read(plate_gcode)
    eject = build_end_gcode(height_mm).encode("utf-8")
    marker = _BLOCK_END.encode("ascii")
    idx = gbytes.find(marker)
    if idx != -1:
        gbytes = gbytes[:idx] + eject + gbytes[idx:]
    else:
        gbytes = gbytes.rstrip(b"\n") + b"\n" + eject
    md5 = hashlib.md5(gbytes).hexdigest().upper().encode("ascii")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zout:
        for zi in infos:
            if zi.filename == plate_gcode:
                payload = gbytes
            elif zi.filename == plate_md5:
                payload = md5
            else:
                payload = zin.read(zi.filename)
            # Reuse the source ZipInfo so each member keeps its original
            # compression (the slicer STOREs the embedded PNGs/thumbnails; the
            # P1S preview parser chokes if we re-DEFLATE them) and metadata. Only
            # the gcode + its md5 change.
            zout.writestr(zi, payload)
    return buf.getvalue()
