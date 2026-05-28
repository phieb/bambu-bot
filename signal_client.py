"""Async client for a Signal REST API (send messages, create groups)."""
import logging

import httpx

import config

log = logging.getLogger("bambu-bot")


async def _send(recipients, message, attachments=None):
    body = {"number": config.BOT_NUMBER, "recipients": recipients, "message": message}
    if attachments:
        body["base64_attachments"] = attachments
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(config.SIGNAL_URL + "/v2/send", json=body)
        r.raise_for_status()
        return r.json() if r.content else {}


async def send_to_group(group_id, message, attachments=None):
    return await _send([group_id], message, attachments)


async def fetch_attachment(att_id):
    """Download a received Signal attachment's raw bytes from signal-cli
    (``GET /v1/attachments/{id}``), or None on failure."""
    if not att_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(f"{config.SIGNAL_URL}/v1/attachments/{att_id}")
            r.raise_for_status()
            return r.content or None
    except Exception:
        log.warning("attachment fetch failed: %s", att_id, exc_info=True)
        return None


async def fetch_bytes(url):
    """Download a URL → raw bytes (e.g. a model thumbnail), or None on any
    failure so a missing/broken image never blocks the color question."""
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
            return r.content
    except Exception:
        log.warning("image fetch failed: %s", url, exc_info=True)
        return None


async def create_group(name, members):
    """Create a Signal group, returning its ``group.<...>`` id."""
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            f"{config.SIGNAL_URL}/v1/groups/{config.BOT_NUMBER}",
            json={"name": name, "members": members},
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, str):
            return data
        return data.get("id") or data.get("group_id")
