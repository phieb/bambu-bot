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
