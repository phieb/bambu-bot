"""Direct client for the OrcaSlicer/Bambu-Studio sidecar (the real slicer).

Bambuddy's slice goes through cloud presets and chokes on some files (multi-plate
3mf whose objects sit off-bed → "object conflicts"). The sidecar slices a 3mf
straight, resolves a profile's ``inherits`` chain from its own bundle, and can
auto-arrange — so we call it directly with bundled P1S profiles when Bambuddy's
slice fails. Verified end-to-end against the P1S sidecar.

Key sidecar facts (POST /slice, multipart):
- ``file`` MUST be sent with MIME ``model/3mf`` or it's rejected ("Invalid file
  type") even with a .3mf name.
- pass profiles as JSON *files* (``printerProfile``/``filamentProfile``) — the
  ``printer``/``filament`` name fields only look in /app/data and miss system
  profiles; as files the sidecar resolves ``inherits`` from its bundle.
- ``plate`` is 1-based (0 = all); ``arrange``/``orient`` are bools. Arrange on a
  *single* plate errors — only use it with plate=0.
"""
import logging
import os

import httpx

import config

log = logging.getLogger("bambu-bot")

_PROFILES = os.path.join(os.path.dirname(__file__), "profiles")
PRINTER = os.path.join(_PROFILES, "printer_p1s_0.4.json")
# The nozzle the bundled printer profile is for. Callers must check this against
# the nozzle actually fitted — there is no profile for any other size, so this
# path can only ever produce 0.4 gcode.
PROFILE_NOZZLE = "0.4"
_FILAMENTS = {
    "pla": os.path.join(_PROFILES, "filament_pla_basic.json"),
    "petg": os.path.join(_PROFILES, "filament_petg.json"),
    "sunlu_pla_meta": os.path.join(_PROFILES, "filament_sunlu_pla_meta.json"),
}


def filament_profile(tray_type, sub_brand=""):
    """Pick a bundled filament profile path for an AMS slot. SUNLU PLA Meta gets
    its tuned profile; otherwise the generic Bambu PLA/PETG. Falls back to PLA."""
    t = (tray_type or "").upper()
    sub = (sub_brand or "").lower()
    if "sunlu" in sub and "meta" in sub:
        return _FILAMENTS["sunlu_pla_meta"]
    if "PETG" in t:
        return _FILAMENTS["petg"]
    return _FILAMENTS["pla"]


async def slice_3mf(data, plate=1, filament=None, arrange=False, orient=False):
    """Slice ``data`` (a .3mf) for the P1S via the sidecar → the sliced
    ``.gcode.3mf`` bytes, or None. ``filament`` is a bundled profile path
    (defaults to generic PLA). ``plate`` 1-based (0 = all plates, needs arrange)."""
    filament = filament or _FILAMENTS["pla"]
    try:
        files = {
            "file": ("model.3mf", data, "model/3mf"),
            "printerProfile": ("printer.json", open(PRINTER, "rb").read(), "application/json"),
            "filamentProfile": ("filament.json", open(filament, "rb").read(), "application/json"),
        }
        # Only send arrange/orient when enabled — the Node sidecar treats the
        # string "false" as truthy (Boolean("false") === true), so passing them
        # off would wrongly switch them ON.
        form = {"plate": str(int(plate)), "exportType": "3mf"}
        if arrange:
            form["arrange"] = "true"
        if orient:
            form["orient"] = "true"
        async with httpx.AsyncClient(timeout=300) as c:
            r = await c.post(config.SLICER_URL + "/slice", files=files, data=form)
        if r.status_code == 200 and r.content[:2] == b"PK":
            return r.content
        log.warning("sidecar slice failed: HTTP %s %s", r.status_code, r.text[:200])
        return None
    except Exception:
        log.exception("sidecar slice errored")
        return None
