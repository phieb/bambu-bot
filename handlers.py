"""Routing + business logic. `claims` is a side-effect-free predicate the
dispatcher asks before forwarding; `handle` does the real work."""
import asyncio
import json
import logging
import time

import bambuddy
import classify
import colors
import config
import signal_client
import slicing
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
    if parsed["is_progress"]:
        return "group_progress"
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
        elif route == "group_progress":
            await _progress(parsed["group_send_id"])
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
    # Download the cover and render the swatch concurrently; the network fetch
    # dominates, so overlapping it with the (CPU) render hides the render cost.
    raw, chart = await asyncio.gather(
        signal_client.fetch_bytes(colors.cover_url(resolved)),
        asyncio.to_thread(swatch.build, name, required, ams),
    )
    thumb = await asyncio.to_thread(swatch.shrink_image, raw) if raw else None
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
    await signal_client.send_to_group(
        group_id, f'🔧 Slice „{job["model_name"]}" für {config.PRINTER_MODEL} … (kurz Geduld)'
    )
    file_id, note = await _reslice(job["library_file_id"], required, ams, mapping)
    resp = await bambuddy.queue(file_id, mapping)
    if isinstance(resp, dict) and resp.get("id"):
        store.set_queue_item_id(job["id"], resp["id"])
    nums = " ".join(str(m + 1) for m in mapping)
    await signal_client.send_to_group(
        group_id,
        f'✅ "{job["model_name"]}" ist in der Queue! Farben: {nums}{note}\n'
        f'Ich sag dir Bescheid, wenn er fertig ist. (!progress für den Stand · !liste · !abbrechen · !help)',
    )


async def _reslice(library_file_id, required, ams, mapping):
    """Re-slice the imported file for the target printer so it doesn't print
    with the MakerWorld profile's machine slice (e.g. X1C on a P1S). Returns
    (file_id_to_queue, note). On any miss, falls back to the original file so a
    print is never lost — the note flags it."""
    try:
        presets = await bambuddy.get_presets()
        try:
            idmap = await bambuddy.filament_id_map() or {}
        except Exception:
            log.warning("filament-id-map fetch failed; falling back to heuristic", exc_info=True)
            idmap = {}
        printer_p = slicing.printer_preset(presets, config.PRINTER_MODEL, config.NOZZLE_DIAMETER)
        process_p = slicing.process_preset(presets, config.PRINTER_MODEL)
        filament_ps = []
        for i in range(len(required)):
            tray = next((a for a in ams if a["tray_id"] == mapping[i]), None) or {}
            real_name = idmap.get(tray.get("info_idx") or "") or ""
            fp = slicing.filament_preset(
                presets, config.PRINTER_MODEL, config.NOZZLE_DIAMETER,
                tray.get("type") or "", tray.get("sub") or "", real_name,
            )
            if fp:
                filament_ps.append(fp)
        if not (printer_p and process_p and len(filament_ps) == len(required)):
            log.warning("reslice: presets incomplete (printer=%s process=%s filament=%d/%d)",
                        bool(printer_p), bool(process_p), len(filament_ps), len(required))
            return library_file_id, " · ⚠️ Re-Slice übersprungen (Presets fehlen), drucke Original"
        started = await bambuddy.slice_file(library_file_id, printer_p, process_p, filament_ps)
        new_id = await _await_slice((started or {}).get("job_id"))
        if new_id:
            return new_id, f" · ♻️ für {config.PRINTER_MODEL} neu geslict"
        return library_file_id, " · ⚠️ Re-Slice fehlgeschlagen, drucke Original"
    except Exception:
        log.exception("reslice failed")
        return library_file_id, " · ⚠️ Re-Slice fehlgeschlagen, drucke Original"


async def _await_slice(job_id, timeout=300, interval=4):
    """Poll a slice job to completion → new library_file_id, or None."""
    if not job_id:
        return None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(interval)
        job = await bambuddy.slice_job(job_id)
        status = (job or {}).get("status")
        if status in ("completed", "succeeded"):
            return (job.get("result") or {}).get("library_file_id")
        if status in ("failed", "error", "cancelled"):
            log.warning("slice job %s %s: %s", job_id, status, (job or {}).get("error_detail"))
            return None
    log.warning("slice job %s timed out", job_id)
    return None


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


_ACTIVE_STATES = {"RUNNING", "PRINTING", "PREPARE", "PAUSE", "PAUSED", "SLICING"}


async def _progress(group_id):
    """Current print on the printer (any source), or idle — with a live cam shot."""
    # Status + camera frame overlap; the snapshot is best-effort (None if no cam).
    s, raw = await asyncio.gather(
        bambuddy.printer_status(config.PRINTER_ID),
        bambuddy.camera_snapshot(config.PRINTER_ID),
    )
    s = s or {}
    cam = await asyncio.to_thread(swatch.shrink_image, raw) if raw else None
    attachments = [cam] if cam else None
    state = s.get("state") or "?"
    name = s.get("current_print") or s.get("subtask_name") or ""
    prog = s.get("progress")
    ln, tl = s.get("layer_num"), s.get("total_layers")
    rem = s.get("remaining_time")
    active = state.upper() in _ACTIVE_STATES or (isinstance(prog, (int, float)) and 0 < prog < 100)
    if not (name and active):
        await signal_client.send_to_group(
            group_id, f"🖨️ Drucker ist {state.lower()} — gerade kein Druck.", attachments=attachments
        )
        return
    parts = [f'🖨️ „{name}" — {state}']
    if isinstance(prog, (int, float)):
        parts.append(f"{round(prog)}%")
    if ln and tl:
        parts.append(f"Layer {ln}/{tl}")
    if rem:
        parts.append(f"noch ca. {rem} min")
    await signal_client.send_to_group(group_id, " · ".join(parts), attachments=attachments)


async def poll_completions(interval=60):
    """Watch bot-queued jobs and message the group when each finishes/fails.
    Prints started via other channels aren't tracked here, so they're skipped."""
    while True:
        try:
            await _check_completions()
        except Exception:
            log.exception("completion poll failed")
        await asyncio.sleep(interval)


async def _check_completions():
    for job in store.queued_jobs_with_item():
        item = await bambuddy.get_queue_item(job["queue_item_id"])
        status = (item or {}).get("status")
        if status == "completed":
            await signal_client.send_to_group(
                job["group_id"], f'✅ „{job["model_name"]}" ist fertig gedruckt! 🎉'
            )
            store.set_stage(job["id"], "done")
        elif status == "failed":
            await signal_client.send_to_group(
                job["group_id"],
                f'❌ „{job["model_name"]}" ist fehlgeschlagen' +
                (f": {item.get('error_message')}" if item.get("error_message") else ".") +
                "\n(!liste zeigt die Queue)",
            )
            store.set_stage(job["id"], "failed")
        elif status == "cancelled":
            store.set_stage(job["id"], "cancelled")
        # pending / printing / missing → keep watching
