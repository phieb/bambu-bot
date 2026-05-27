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


async def queue(library_file_id, ams_mapping):
    return await _post(
        "/api/v1/queue/",
        {
            "library_file_id": int(library_file_id),
            "ams_mapping": ams_mapping,
            "gcode_injection": True,
            "use_ams": True,
            "printer_id": None,  # auto-dispatch
        },
    )


async def list_queue():
    """All queue items: [{id, status, library_file_name, position, ...}]."""
    return await _get("/api/v1/queue/")


async def get_queue_item(item_id):
    return await _get(f"/api/v1/queue/{int(item_id)}")


async def delete_queue_item(item_id):
    """Remove a (pending) item from the queue. Does not stop a running print."""
    return await _delete(f"/api/v1/queue/{int(item_id)}")


# ----- re-slicing (needs the slicer sidecar on :3001) -----

async def get_presets():
    """{cloud,standard,local}.{printer,process,filament} preset lists."""
    return await _get("/api/v1/slicer/presets")


async def slice_file(library_file_id, printer_preset, process_preset, filament_presets):
    """Enqueue a slice job → {job_id}. filament_presets is mandatory (one ref
    per model filament). Each *_preset is a {source, id} PresetRef."""
    return await _post(
        f"/api/v1/library/files/{int(library_file_id)}/slice",
        {
            "printer_preset": printer_preset,
            "process_preset": process_preset,
            "filament_presets": filament_presets,
        },
    )


async def slice_job(job_id):
    """Slice job status; on completion ``result.library_file_id`` is the new file."""
    return await _get(f"/api/v1/slice-jobs/{int(job_id)}")
