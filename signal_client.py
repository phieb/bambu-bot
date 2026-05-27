"""Async client for a Signal REST API (send messages, create groups)."""
import httpx

import config


async def _send(recipients, message):
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            config.SIGNAL_URL + "/v2/send",
            json={"number": config.BOT_NUMBER, "recipients": recipients, "message": message},
        )
        r.raise_for_status()
        return r.json() if r.content else {}


async def send_to_group(group_id, message):
    return await _send([group_id], message)


async def send_to_number(number, message):
    return await _send([number], message)


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
