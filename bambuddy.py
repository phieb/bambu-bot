"""Async client for the Bambuddy REST API."""
import logging

import httpx

import config

log = logging.getLogger("bambu-bot")


async def _post(path, body):
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(config.BAMBUDDY_URL + path, json=body)
        r.raise_for_status()
        return r.json()


async def _get(path):
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(config.BAMBUDDY_URL + path)
        r.raise_for_status()
        return r.json()


async def _patch(path, body):
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.patch(config.BAMBUDDY_URL + path, json=body)
        r.raise_for_status()
        return r.json() if r.content else {}


async def _delete(path):
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.delete(config.BAMBUDDY_URL + path)
        r.raise_for_status()
        return r.json() if r.content else {}


async def resolve(url):
    return await _post("/api/v1/makerworld/resolve", {"url": url})


async def import_model(model_id, profile_id):
    return await _post("/api/v1/makerworld/import", {"model_id": model_id, "profile_id": profile_id})


async def printer_status(printer_id):
    return await _get(f"/api/v1/printers/{printer_id}/status")


async def mounted_nozzle(printer_id):
    """The diameter (str, e.g. '0.4') of the nozzle currently fitted to the printer
    as the machine reports it (``status.nozzles[].nozzle_diameter``), or None if it
    can't be read. The P1S has no nozzle auto-detect, so this reflects what the user
    set on the device. Used to refuse a print sliced for a different nozzle (0.4
    gcode through a 0.2 nozzle under-extrudes / jams). Never raises."""
    try:
        st = await printer_status(printer_id)
    except Exception:
        return None
    for n in (st.get("nozzles") or []) if isinstance(st, dict) else []:
        d = (n.get("nozzle_diameter") or "").strip()
        if d:
            return d
    return None


async def camera_snapshot(printer_id, attempts=2):
    """Single live JPEG frame from the printer camera as raw bytes, or None.
    Works without starting a stream; never raises (a missing cam shouldn't
    break a progress reply).

    A snapshot normally takes ~2s but occasionally stalls or comes back
    non-image (camera busy / printer just woke up), which silently costs the
    photo on a !progress reply. So retry once and — crucially — LOG why it
    failed: a returned None used to be indistinguishable from "no camera",
    which made a missing photo undiagnosable after the fact."""
    url = config.BAMBUDDY_URL + f"/api/v1/printers/{printer_id}/camera/snapshot"
    for attempt in range(1, attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get(url)
                r.raise_for_status()
                ct = r.headers.get("content-type", "")
                if "image" in ct and r.content:
                    return r.content
                log.warning("camera snapshot not an image (attempt %d/%d): "
                            "status=%s content-type=%r bytes=%d",
                            attempt, attempts, r.status_code, ct, len(r.content or b""))
        except Exception as e:
            log.warning("camera snapshot failed (attempt %d/%d): %s: %s",
                        attempt, attempts, type(e).__name__, e)
    return None


async def queue(library_file_id, ams_mapping, plate_id=None, gcode_injection=True):
    body = {
        "library_file_id": int(library_file_id),
        "ams_mapping": ams_mapping,
        "gcode_injection": gcode_injection,  # apply Bambuddy's global per-model
        # snippet (the eject end-gcode) — off for tall prints / when tools are away.
        "use_ams": True,
        "printer_id": None,            # no specific printer …
        "target_model": config.PRINTER_MODEL,  # … but dispatch to any P1S, so the
        # scheduler starts it without relying on Bambuddy's default_printer_id.
    }
    if plate_id is not None:
        body["plate_id"] = int(plate_id)  # which plate of a multi-plate 3MF
    return await _post("/api/v1/queue/", body)


async def list_folders():
    """Library folders: [{id, name, ...}]."""
    return await _get("/api/v1/library/folders/")


async def create_folder(name):
    return await _post("/api/v1/library/folders/", {"name": name})


async def ensure_folder(name):
    """Return the id of the library folder named ``name``, creating it if absent."""
    folders = await list_folders()
    for f in folders if isinstance(folders, list) else []:
        if (f.get("name") or "").lower() == name.lower():
            return f.get("id")
    created = await create_folder(name)
    return created.get("id") if isinstance(created, dict) else None


async def upload_library_file(content, filename, folder_id=None):
    """Upload a file into the library → {id, filename, file_type, ...}. The
    returned ``id`` is the library_file_id usable for plates/slice/queue."""
    params = {} if folder_id is None else {"folder_id": int(folder_id)}
    files = {"file": (filename, content, "application/octet-stream")}
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(config.BAMBUDDY_URL + "/api/v1/library/files/", params=params, files=files)
        r.raise_for_status()
        return r.json()


async def extract_zip(content, filename, folder_id=None):
    """Upload a .zip and extract its printable files into the library →
    {extracted, files:[{filename, file_id}], errors}. Each extracted STL/3MF
    becomes its own library file."""
    params = {} if folder_id is None else {"folder_id": int(folder_id)}
    files = {"file": (filename, content, "application/zip")}
    async with httpx.AsyncClient(timeout=180) as c:
        r = await c.post(config.BAMBUDDY_URL + "/api/v1/library/files/extract-zip", params=params, files=files)
        r.raise_for_status()
        return r.json()


async def list_plates(library_file_id):
    """Plates of a (possibly multi-plate) library file:
    {is_multi_plate, plates:[{index, name, filaments:[{type,color,...}], ...}]}."""
    return await _get(f"/api/v1/library/files/{int(library_file_id)}/plates")


async def archive_plates(archive_id):
    """Plates of an *archive* — what a Bambu-Studio "Send All" upload creates:
    {archive_id, filename, plates:[{index, name, print_time_seconds, ...}]}.

    A queue item's ``plate_id`` maps 1:1 to ``index`` here. Studio-sent items
    carry ``archive_id`` and a null ``library_file_id``, so this is the only way
    to learn what their plates are actually called."""
    return await _get(f"/api/v1/archives/{int(archive_id)}/plates")


async def archive_plate_thumbnail(archive_id, plate_index):
    """Rendered PNG of one archive plate as raw bytes, or None (best-effort)."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                config.BAMBUDDY_URL
                + f"/api/v1/archives/{int(archive_id)}/plate-thumbnail/{int(plate_index)}"
            )
            r.raise_for_status()
            ct = r.headers.get("content-type", "")
            return r.content if "image" in ct and r.content else None
    except Exception:
        return None


async def plate_thumbnail(library_file_id, plate_index):
    """Rendered PNG of a single plate as raw bytes, or None (best-effort)."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                config.BAMBUDDY_URL
                + f"/api/v1/library/files/{int(library_file_id)}/plate-thumbnail/{int(plate_index)}"
            )
            r.raise_for_status()
            ct = r.headers.get("content-type", "")
            return r.content if "image" in ct and r.content else None
    except Exception:
        return None


async def file_thumbnail(library_file_id):
    """Model thumbnail PNG of a library file as raw bytes, or None. Raw STLs have
    no per-plate render but do get a model thumbnail (best-effort)."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(config.BAMBUDDY_URL + f"/api/v1/library/files/{int(library_file_id)}/thumbnail")
            r.raise_for_status()
            ct = r.headers.get("content-type", "")
            return r.content if "image" in ct and r.content else None
    except Exception:
        return None


async def get_gcode(library_file_id):
    """Bytes of a sliced library file's gcode, or None (best-effort). NOTE the
    ``/gcode`` endpoint is *not* a stable container: for a file Bambuddy typed as
    ``gcode`` it returns the ``.gcode.3mf`` zip, but for one typed ``3mf`` (e.g. a
    re-sliced ``*_plate_N.gcode.3mf`` that kept its model + plate metadata) it
    returns the *extracted* plate gcode text. Good enough for reading the height
    (``_max_z_height`` handles both shapes); use ``download_file`` when you need
    the real container to modify (see eject injection)."""
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(config.BAMBUDDY_URL + f"/api/v1/library/files/{int(library_file_id)}/gcode")
            r.raise_for_status()
            return r.content
    except Exception:
        return None


async def download_file(library_file_id):
    """Raw bytes of a library file as stored, or None (best-effort). Unlike
    ``/gcode`` this always returns the actual container — for a ``.gcode.3mf``
    that's the zip with ``Metadata/plate_*.gcode`` + its ``.md5`` inside,
    regardless of how Bambuddy typed the file. Used by the eject injection,
    which must rewrite the plate gcode and recompute its md5."""
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(config.BAMBUDDY_URL + f"/api/v1/library/files/{int(library_file_id)}/download")
            r.raise_for_status()
            return r.content
    except Exception:
        return None


async def get_settings():
    """Bambuddy's global settings (``require_plate_clear``, ``default_printer_id``,
    Spoolman wiring, …)."""
    return await _get("/api/v1/settings")


async def slot_assignments():
    """[{printer_id, ams_id, tray_id, spoolman_spool_id}] — which Spoolman spool the
    user has assigned to each AMS slot in Bambuddy. Empty when Spoolman is off."""
    return await _get("/api/v1/spoolman/inventory/slot-assignments/all")


async def spoolman_spools():
    """{"spools": [{id, filament:{name, material, color_hex, vendor:{name}}}]} —
    the Spoolman inventory as Bambuddy proxies it. Paired with
    :func:`slot_assignments` this turns an AMS tray into its real product name."""
    return await _get("/api/v1/spoolman/spools")


async def set_require_plate_clear(value):
    """Toggle Bambuddy's global 'wait for manual plate-clear between jobs' setting.
    Off while auto-eject is active so the queue flows without manual !go."""
    return await _patch("/api/v1/settings", {"require_plate_clear": bool(value)})


async def clear_plate(printer_id):
    """Acknowledge the build plate is cleared so the scheduler starts the next
    queued print. Sends no MQTT command — just sets Bambuddy's plate-cleared flag."""
    return await _post(f"/api/v1/printers/{printer_id}/clear-plate", {})


async def list_queue():
    """All queue items: [{id, status, library_file_name, position, ...}]."""
    return await _get("/api/v1/queue/")


async def get_queue_item(item_id):
    """A queue item by id, or None if it no longer exists (404). Bambuddy drops
    items once they age out, so a missing item just means 'not trackable anymore'."""
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(config.BAMBUDDY_URL + f"/api/v1/queue/{int(item_id)}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()


async def delete_queue_item(item_id):
    """Remove a (pending) item from the queue. Does not stop a running print."""
    return await _delete(f"/api/v1/queue/{int(item_id)}")


# ----- re-slicing (needs the slicer sidecar on :3001) -----

async def get_presets():
    """{cloud,standard,local}.{printer,process,filament} preset lists."""
    return await _get("/api/v1/slicer/presets")


async def filament_id_map():
    """AMS ``tray_info_idx`` → real filament name (e.g. 'eSUN PETG Basic'), so a
    non-Bambu spool resolves to the user's actual preset instead of a guess."""
    return await _get("/api/v1/cloud/filament-id-map")


async def slice_file(library_file_id, printer_preset, process_preset, filament_presets, plate=None, bed_type=None):
    """Enqueue a slice job → {job_id}. filament_presets is mandatory (one ref
    per model filament). Each *_preset is a {source, id} PresetRef. ``plate`` is
    the 1-based plate to slice (None → plate 1); the result is a single-plate file.
    ``bed_type`` overrides the process preset's ``curr_bed_type`` (a canonical
    plate name like 'Cool Plate') so the gcode's bed temp + first-layer Z match
    the plate actually fitted; None inherits the preset's default plate."""
    body = {
        "printer_preset": printer_preset,
        "process_preset": process_preset,
        "filament_presets": filament_presets,
    }
    if plate is not None:
        body["plate"] = int(plate)
    if bed_type:
        body["bed_type"] = bed_type
    return await _post(f"/api/v1/library/files/{int(library_file_id)}/slice", body)


async def slice_job(job_id):
    """Slice job status; on completion ``result.library_file_id`` is the new file."""
    return await _get(f"/api/v1/slice-jobs/{int(job_id)}")
