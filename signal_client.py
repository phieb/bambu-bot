"""Async client for a Signal REST API (send messages, create groups)."""
import base64
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


async def send_to_number(number, message):
    return await _send([number], message)


async def fetch_attachment(url):
    """Download an image URL → base64 string for Signal's ``base64_attachments``.

    Returns None on any failure so a missing/broken thumbnail never blocks the
    color question from going out.
    """
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
            return base64.b64encode(r.content).decode()
    except Exception:
        log.warning("thumbnail fetch failed: %s", url, exc_info=True)
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
