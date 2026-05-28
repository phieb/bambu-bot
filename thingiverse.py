"""Download a Thingiverse "thing" via the official API (needs an app token).

Thingiverse has no zip endpoint, so we list a thing's files, keep the printable
ones (.stl/.3mf), download each, and bundle them into an in-memory zip — which
the caller hands to Bambuddy's extract-zip so a multi-file thing behaves exactly
like a multi-STL upload."""
import io
import logging
import zipfile

import httpx

import config

log = logging.getLogger("bambu-bot")

_API = "https://api.thingiverse.com"
_PRINTABLE = (".stl", ".3mf")


def _auth():
    return {"Authorization": f"Bearer {config.THINGIVERSE_TOKEN}"}


async def thing_name(thing_id):
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{_API}/things/{int(thing_id)}", headers=_auth())
            r.raise_for_status()
            return (r.json() or {}).get("name") or f"thing-{thing_id}"
    except Exception:
        return f"thing-{thing_id}"


async def build_zip(thing_id):
    """Bundle a thing's printable files into (zip_bytes, name.zip), or (None, name)
    if the thing has no printable files / the API fails."""
    name = await thing_name(thing_id)
    safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in name).strip() or f"thing-{thing_id}"
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
        r = await c.get(f"{_API}/things/{int(thing_id)}/files", headers=_auth())
        r.raise_for_status()
        files = [f for f in (r.json() or [])
                 if (f.get("name") or "").lower().endswith(_PRINTABLE) and f.get("download_url")]
        if not files:
            return None, safe
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in files:
                d = await c.get(f["download_url"], headers=_auth())
                d.raise_for_status()
                z.writestr(f["name"], d.content)
        return buf.getvalue(), f"{safe}.zip"
