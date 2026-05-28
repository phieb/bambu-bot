"""Async client for the Bambuddy REST API."""
import httpx

import config


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


async def camera_snapshot(printer_id):
    """Single live JPEG frame from the printer camera as raw bytes, or None.
    Works without starting a stream; never raises (a missing cam shouldn't
    break a progress reply)."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(config.BAMBUDDY_URL + f"/api/v1/printers/{printer_id}/camera/snapshot")
            r.raise_for_status()
            ct = r.headers.get("content-type", "")
            return r.content if "image" in ct and r.content else None
    except Exception:
        return None


async def queue(library_file_id, ams_mapping, plate_id=None):
    body = {
        "library_file_id": int(library_file_id),
        "ams_mapping": ams_mapping,
        "gcode_injection": True,
        "use_ams": True,
        "printer_id": None,  # auto-dispatch
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


async def slice_file(library_file_id, printer_preset, process_preset, filament_presets, plate=None):
    """Enqueue a slice job → {job_id}. filament_presets is mandatory (one ref
    per model filament). Each *_preset is a {source, id} PresetRef. ``plate`` is
    the 1-based plate to slice (None → plate 1); the result is a single-plate file."""
    body = {
        "printer_preset": printer_preset,
        "process_preset": process_preset,
        "filament_presets": filament_presets,
    }
    if plate is not None:
        body["plate"] = int(plate)
    return await _post(f"/api/v1/library/files/{int(library_file_id)}/slice", body)


async def slice_job(job_id):
    """Slice job status; on completion ``result.library_file_id`` is the new file."""
    return await _get(f"/api/v1/slice-jobs/{int(job_id)}")
