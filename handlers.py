"""Routing + business logic. `claims` is a side-effect-free predicate the
dispatcher asks before forwarding; `handle` does the real work."""
import json
import logging

import bambuddy
import classify
import colors
import config
import signal_client
import store
import swatch

log = logging.getLogger("bambu-bot")


def _route(parsed):
    """dm_link | group_link | group_config | ignore.

    Group routes only fire for groups we created (registered in the store), so
    stray groups the bot's number sits in are ignored, not mishandled.
    """
    if parsed["is_dm"]:
        return "dm_link" if parsed["has_link"] else "ignore"
    gid = parsed["group_send_id"]
    if not gid or not store.get_group_by_group_id(gid):
        return "ignore"
    if parsed["has_link"]:
        return "group_link"
    if parsed["is_help"]:
        return "group_help"
    if parsed["is_cancel"]:
        return "group_cancel"
    if parsed["is_list"]:
        return "group_list"
    if parsed["is_numbered"]:
        return "group_config"
    return "ignore"


def claims(envelope):
    return _route(classify.classify(envelope)) != "ignore"


async def handle(envelope):
    parsed = classify.classify(envelope)
    route = _route(parsed)
    try:
        if route == "dm_link":
            group_id = await _ensure_group(parsed["sender"])
            await _intake(group_id, parsed["sender"], parsed["url"])
        elif route == "group_link":
            await _intake(parsed["group_send_id"], parsed["sender"], parsed["url"])
        elif route == "group_config":
            await _config(parsed["group_send_id"], parsed["message"])
        elif route == "group_cancel":
            await _cancel(parsed["group_send_id"])
        elif route == "group_list":
            await _list(parsed["group_send_id"])
        elif route == "group_help":
            await signal_client.send_to_group(parsed["group_send_id"], colors.HELP_TEXT)
    except Exception:
        log.exception("handle failed (route=%s)", route)


async def _ensure_group(sender):
    existing = store.get_group_by_sender(sender)
    if existing:
        return existing["group_id"]
    group_id = await signal_client.create_group(config.GROUP_NAME, [sender])
    store.save_group(sender, group_id, config.GROUP_NAME)
    return group_id


async def _intake(group_id, sender, url):
    if store.active_job(group_id):
        await signal_client.send_to_group(
            group_id,
            "⏳ Du hast noch einen offenen Job — bitte konfigurier den zuerst "
            "(Farben zuweisen), dann schick den nächsten Link.",
        )
        return
    resolved = await bambuddy.resolve(url)
    required = colors.required_colors(resolved)
    profile_id = colors.chosen_profile_id(resolved)
    imported = await bambuddy.import_model(resolved["model_id"], profile_id)
    library_file_id = imported["library_file_id"]
    status = await bambuddy.printer_status(config.PRINTER_ID)
    ams = colors.ams_snapshot(status)
    name = colors.model_name(resolved)
    store.create_job(group_id, sender, resolved["model_id"], library_file_id, name, required, ams)
    thumb = await signal_client.fetch_attachment(colors.cover_url(resolved))
    chart = swatch.build(name, required, ams)
    attachments = [a for a in (thumb, chart) if a]
    await signal_client.send_to_group(
        group_id,
        colors.build_question(name, required, ams),
        attachments=attachments or None,
    )


async def _config(group_id, message):
    job = store.active_job(group_id)
    if not job:
        await signal_client.send_to_group(
            group_id, "Es gibt gerade keinen offenen Job zum Konfigurieren. Schick mir einen MakerWorld-Link."
        )
        return
    required = json.loads(job["required_colors"])
    ams = json.loads(job["ams_snapshot"])
    ok, mapping, error = colors.parse_reply(message, required, ams)
    if not ok:
        await signal_client.send_to_group(group_id, error)
        return
    if not store.claim_job_for_queue(group_id):
        return  # a duplicate/concurrent reply already queued it
    resp = await bambuddy.queue(job["library_file_id"], mapping)
    if isinstance(resp, dict) and resp.get("id"):
        store.set_queue_item_id(job["id"], resp["id"])
    nums = " ".join(str(m + 1) for m in mapping)
    await signal_client.send_to_group(
        group_id,
        f'✅ "{job["model_name"]}" ist in der Queue! Farben: {nums}\n'
        f'(!abbrechen zum Entfernen · !liste für die Queue · !help für alle Befehle)',
    )


_STATUS_EMOJI = {
    "pending": "⏳", "printing": "🖨️", "completed": "✅",
    "failed": "❌", "skipped": "⏭️", "cancelled": "🚫",
}


async def _cancel(group_id):
    """Drop an open color dialog, else remove the last still-pending queue item.
    A print that is already running is never stopped."""
    dialog = store.active_job(group_id)
    if dialog:
        store.discard_dialog(dialog["id"])
        await signal_client.send_to_group(
            group_id,
            f'🗑️ Abgebrochen: „{dialog["model_name"]}" verworfen. '
            "Schick mir einen neuen MakerWorld-Link, wenn du willst.",
        )
        return
    job = store.last_queued_job(group_id)
    if not job:
        await signal_client.send_to_group(group_id, "Da ist gerade nichts zum Abbrechen.")
        return
    item = await bambuddy.get_queue_item(job["queue_item_id"])
    status = (item or {}).get("status")
    name = job["model_name"]
    if status == "pending":
        await bambuddy.delete_queue_item(job["queue_item_id"])
        store.mark_cancelled(job["id"])
        await signal_client.send_to_group(group_id, f'🗑️ „{name}" aus der Queue entfernt.')
    elif status == "printing":
        await signal_client.send_to_group(
            group_id, f'🖨️ „{name}" druckt schon — laufende Drucke breche ich nicht ab.'
        )
    else:
        store.mark_cancelled(job["id"])
        await signal_client.send_to_group(
            group_id, f'„{name}" ist nicht mehr abbrechbar (Status: {status or "unbekannt"}).'
        )


async def _list(group_id):
    items = await bambuddy.list_queue()
    if not items:
        await signal_client.send_to_group(group_id, "📋 Queue ist leer.")
        return
    lines = []
    for i, it in enumerate(items, 1):
        nm = (it.get("library_file_name") or it.get("archive_name")
              or it.get("target_model") or f'#{it.get("id")}')
        st = it.get("status") or "?"
        lines.append(f'{i}. {_STATUS_EMOJI.get(st, "")} {nm} ({st})'.replace("  ", " "))
    await signal_client.send_to_group(group_id, "📋 Queue:\n" + "\n".join(lines))
