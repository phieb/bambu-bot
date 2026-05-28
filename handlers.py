"""Routing + business logic. `claims` is a side-effect-free predicate the
dispatcher asks before forwarding; `handle` does the real work.

Intake is a small state machine carried by the open dialog row (see store):
``resolving → [awaiting_profile] → [awaiting_plate] → awaiting_colors → queued``.
The profile and plate steps auto-skip when there's only one of each. A numbered
reply means whatever the current stage expects, so it's dispatched on stage.
"""
import asyncio
import json
import logging
import re
import time

import bambuddy
import classify
import colors
import config
import signal_client
import slicing
import stl
import store
import swatch
import thingiverse

log = logging.getLogger("bambu-bot")


def _route(parsed):
    """dm_link | group_link | group_reply | group_* command | ignore.

    Group routes only fire for groups we created (registered in the store), so
    stray groups the bot's number sits in are ignored, not mishandled.
    """
    tv = parsed["thingiverse_id"] and config.THINGIVERSE_TOKEN
    if parsed["is_dm"]:
        if parsed["has_link"]:
            return "dm_link"
        if parsed["has_file_url"]:
            return "dm_file_url"
        if tv:
            return "dm_thingiverse"
        if parsed["has_model_file"]:
            return "dm_file"
        if parsed["is_other_model"]:
            return "dm_other_model"
        return "ignore"
    gid = parsed["group_send_id"]
    if not gid or not store.get_group_by_group_id(gid):
        return "ignore"
    if parsed["has_link"]:
        return "group_link"
    if parsed["has_file_url"]:
        return "group_file_url"
    if tv:
        return "group_thingiverse"
    if parsed["has_model_file"]:
        return "group_file"
    if parsed["is_other_model"]:
        return "group_other_model"
    if parsed["is_help"]:
        return "group_help"
    if parsed["is_cancel"]:
        return "group_cancel"
    if parsed["is_list"]:
        return "group_list"
    if parsed["is_progress"]:
        return "group_progress"
    if parsed["is_go"]:
        return "group_go"
    if parsed["is_skip"]:
        return "group_skip"
    if parsed["is_numbered"]:
        return "group_reply"
    # Unrecognized *text* in our own group → friendly "didn't get that" + help.
    # Contentless events (photos, reactions) stay claimed but get no reply.
    if parsed["message"]:
        return "group_unknown"
    return "ignore"


def claims(envelope):
    """Whether this bot owns the message. In one of our registered groups we own
    *every* message — even plain chatter — so the dispatcher routes the whole
    group to us and nothing leaks to other tools; handle() just no-ops on
    non-actionable messages. DMs are only claimed when actually actionable."""
    parsed = classify.classify(envelope)
    if not parsed["is_dm"]:
        gid = parsed["group_send_id"]
        if gid and store.get_group_by_group_id(gid):
            return True
    return _route(parsed) != "ignore"


async def handle(envelope):
    parsed = classify.classify(envelope)
    route = _route(parsed)
    try:
        if route == "dm_link":
            group_id = await _ensure_group(parsed["sender"])
            await _intake(group_id, parsed["sender"], parsed["url"])
        elif route == "group_link":
            await _intake(parsed["group_send_id"], parsed["sender"], parsed["url"])
        elif route == "dm_other_model":
            # Reply in the person's group, never in the DM itself.
            group_id = await _ensure_group(parsed["sender"])
            await signal_client.send_to_group(group_id, colors.OTHER_MODEL_TEXT)
        elif route == "group_other_model":
            await signal_client.send_to_group(parsed["group_send_id"], colors.OTHER_MODEL_TEXT)
        elif route == "dm_file":
            group_id = await _ensure_group(parsed["sender"])
            await _intake_file(group_id, parsed["sender"], parsed["model_files"][0])
        elif route == "group_file":
            await _intake_file(parsed["group_send_id"], parsed["sender"], parsed["model_files"][0])
        elif route == "dm_file_url":
            group_id = await _ensure_group(parsed["sender"])
            await _intake_url(group_id, parsed["sender"], parsed["file_url"])
        elif route == "group_file_url":
            await _intake_url(parsed["group_send_id"], parsed["sender"], parsed["file_url"])
        elif route == "dm_thingiverse":
            group_id = await _ensure_group(parsed["sender"])
            await _intake_thingiverse(group_id, parsed["sender"], parsed["thingiverse_id"])
        elif route == "group_thingiverse":
            await _intake_thingiverse(parsed["group_send_id"], parsed["sender"], parsed["thingiverse_id"])
        elif route == "group_reply":
            await _reply(parsed["group_send_id"], parsed["message"])
        elif route == "group_cancel":
            await _cancel(parsed["group_send_id"])
        elif route == "group_list":
            await _list(parsed["group_send_id"])
        elif route == "group_progress":
            await _progress(parsed["group_send_id"])
        elif route == "group_go":
            await _go(parsed["group_send_id"])
        elif route == "group_skip":
            await _skip(parsed["group_send_id"])
        elif route == "group_help":
            await signal_client.send_to_group(parsed["group_send_id"], colors.HELP_TEXT)
        elif route == "group_unknown":
            await signal_client.send_to_group(parsed["group_send_id"], colors.UNKNOWN_TEXT)
    except Exception:
        log.exception("handle failed (route=%s)", route)


async def _ensure_group(sender):
    existing = store.get_group_by_sender(sender)
    if existing:
        return existing["group_id"]
    group_id = await signal_client.create_group(config.GROUP_NAME, [sender])
    store.save_group(sender, group_id, config.GROUP_NAME)
    return group_id


# ----- intake & the profile → plate → colors state machine -----

async def _intake(group_id, sender, url):
    if store.active_job(group_id):
        await signal_client.send_to_group(
            group_id,
            "⏳ Du hast noch einen offenen Job — bitte konfigurier den zuerst "
            "(Profil/Plate/Farben), dann schick den nächsten Link.",
        )
        return
    try:
        resolved = await bambuddy.resolve(url)
    except Exception:
        log.exception("resolve failed")
        await signal_client.send_to_group(group_id, "❌ Konnte den Link nicht auflösen.")
        return
    model_id = resolved.get("model_id")
    name = colors.model_name(resolved)
    profiles = colors.profiles_list(resolved, config.PRINTER_MODEL)
    job_id = store.create_dialog(group_id, sender, model_id, name)
    if len(profiles) > 1:
        store.update_dialog(job_id, profiles=json.dumps(profiles), stage="awaiting_profile")
        await signal_client.send_to_group(
            group_id, colors.build_profile_question(name, profiles, config.PRINTER_MODEL)
        )
        return
    profile_id = profiles[0]["profile_id"] if profiles else colors.chosen_profile_id(resolved)
    store.update_dialog(job_id, stage="configuring")
    try:
        await _after_profile(group_id, job_id, model_id, name, profile_id)
    except Exception:
        log.exception("import/plates failed")
        store.discard_dialog(job_id)
        await signal_client.send_to_group(group_id, "❌ Konnte das Modell nicht importieren.")


async def _after_profile(group_id, job_id, model_id, name, profile_id):
    """Import the chosen profile, then hand off to the shared plate/color tail."""
    imported = await bambuddy.import_model(model_id, profile_id)
    store.update_dialog(job_id, profile_id=profile_id)
    await _from_library_file(group_id, job_id, name, imported["library_file_id"])


async def _from_library_file(group_id, job_id, name, lfid):
    """Given a library file (MakerWorld import or Signal upload), branch on plate
    count and start the plate/color dialog. A file with no plates (e.g. a raw
    STL) is treated as a single one-filament plate so the user picks one slot."""
    data = await bambuddy.list_plates(lfid)
    plates = (data or {}).get("plates") or []
    store.update_dialog(job_id, library_file_id=lfid, plates=json.dumps(plates))
    if len(plates) > 1:
        store.update_dialog(job_id, stage="awaiting_plate")
        await _send_plate_question(group_id, lfid, name, plates)
        return
    plate = plates[0] if plates else {"index": 1, "name": "", "filaments": [{"type": "", "color": ""}]}
    store.update_dialog(job_id, pending_plates=json.dumps([]))
    await _ask_colors(group_id, job_id, lfid, name, plate)


async def _busy(group_id):
    """True (and tells the group) if a dialog is already open — one job at a time."""
    if store.active_job(group_id):
        await signal_client.send_to_group(
            group_id,
            "⏳ Du hast noch einen offenen Job — bitte konfigurier den zuerst, "
            "dann schick das nächste Modell.",
        )
        return True
    return False


async def _intake_file(group_id, sender, file_meta):
    """A Signal-uploaded model file: fetch its bytes, then run the shared intake."""
    if await _busy(group_id):
        return
    name = file_meta["filename"]
    await signal_client.send_to_group(group_id, f'⬆️ „{name}" wird verarbeitet …')
    content = await signal_client.fetch_attachment(file_meta["id"])
    if not content:
        await signal_client.send_to_group(group_id, "❌ Konnte die Datei nicht von Signal laden.")
        return
    await _process_model_bytes(group_id, sender, content, name, file_meta["kind"])


async def _intake_url(group_id, sender, url):
    """A direct link to a model file: download it, then run the shared intake."""
    if await _busy(group_id):
        return
    name = classify.filename_from_url(url)
    kind = classify.file_kind(name)
    await signal_client.send_to_group(group_id, f'🌐 „{name}" wird vom Link geladen …')
    content = await signal_client.fetch_bytes(url)
    if not content:
        await signal_client.send_to_group(group_id, "❌ Konnte die Datei vom Link nicht laden.")
        return
    await _process_model_bytes(group_id, sender, content, name, kind)


async def _intake_thingiverse(group_id, sender, thing_id):
    """A Thingiverse link: pull the thing's printable files via the API, bundle
    them into a zip, then run the shared zip intake (multi-file → selection)."""
    if await _busy(group_id):
        return
    await signal_client.send_to_group(group_id, f"🌐 Thingiverse-Modell {thing_id} wird geladen …")
    try:
        zip_bytes, name = await thingiverse.build_zip(thing_id)
    except Exception:
        log.exception("thingiverse fetch failed")
        await signal_client.send_to_group(group_id, "❌ Konnte das Thingiverse-Modell nicht laden.")
        return
    if not zip_bytes:
        await signal_client.send_to_group(
            group_id, "❌ Das Thingiverse-Modell hat keine druckbaren Dateien (.stl/.3mf)."
        )
        return
    await _process_model_bytes(group_id, sender, zip_bytes, name, "zip")


async def _process_model_bytes(group_id, sender, content, name, kind):
    """Upload the bytes into the Signal library folder and start the right flow:
    .zip → extract into selectable items; .gcode → queue as-is; else → plates."""
    job_id = store.create_dialog(group_id, sender, None, name)
    store.update_dialog(job_id, stage="configuring")
    try:
        folder_id = await bambuddy.ensure_folder(config.SIGNAL_FOLDER_NAME)
        if kind == "zip":
            # Arrange every STL inside the zip onto the bed before extraction.
            content = await asyncio.to_thread(stl.arrange_zip, content)
            result = await bambuddy.extract_zip(content, name, folder_id)
            items = [
                {"filename": f.get("filename") or f"Datei {i}", "file_id": f["file_id"]}
                for i, f in enumerate(result.get("files") or [], 1)
            ]
            if not items:
                store.discard_dialog(job_id)
                await signal_client.send_to_group(group_id, "❌ Im ZIP waren keine druckbaren Dateien.")
                return
            await _start_zip(group_id, job_id, name, items)
            return
        if kind == "stl":
            # Center on the bed + drop to Z=0 so the slicer doesn't drop an
            # off-bed object (→ empty print).
            content = await asyncio.to_thread(stl.arrange, content)
        uploaded = await bambuddy.upload_library_file(content, name, folder_id)
        lfid = uploaded["id"]
        if kind == "gcode":
            # Already sliced for a machine — queue as-is, no color dialog.
            resp = await bambuddy.queue(lfid, [], plate_id=None)
            item_id = resp.get("id") if isinstance(resp, dict) else None
            store.add_queued(group_id, sender, name, lfid, item_id)
            store.delete_job(job_id)
            await signal_client.send_to_group(
                group_id,
                f'✅ „{name}" ist in der Queue (vorgeslict)! Ich sag Bescheid, wenn er fertig ist.',
            )
        else:
            await _from_library_file(group_id, job_id, name, lfid)
    except Exception:
        log.exception("file intake failed")
        store.discard_dialog(job_id)
        await signal_client.send_to_group(group_id, "❌ Da ist beim Einreihen was schiefgelaufen.")


async def _start_zip(group_id, job_id, name, items):
    """Each extracted file becomes one selectable single-plate item carrying its
    own library_file_id — so a multi-STL zip behaves like a multi-plate 3MF."""
    plates = [
        {"index": i, "name": it["filename"], "filaments": [{"type": "", "color": ""}],
         "library_file_id": it["file_id"], "src_plate": 1}
        for i, it in enumerate(items, 1)
    ]
    store.update_dialog(job_id, plates=json.dumps(plates))
    if len(plates) > 1:
        store.update_dialog(job_id, stage="awaiting_plate")
        await _send_plate_question(group_id, None, name, plates)
        return
    store.update_dialog(job_id, pending_plates=json.dumps([]))
    p = plates[0]
    await _ask_colors(group_id, job_id, p["library_file_id"], _plate_label(name, p, multi=True), p)


def _plate_source(plate, job_lfid):
    """(library_file_id, in-file plate index) for a selectable item. A zip item
    carries its own single-plate library_file_id (src_plate 1); a normal plate
    lives inside the job's library file at its own index."""
    return plate.get("library_file_id") or job_lfid, plate.get("src_plate", plate.get("index"))


async def _thumbnail(lfid, plate_index):
    """Best-effort preview: a rendered plate thumbnail, or the file's model
    thumbnail (raw STLs have no plate render but do have a model thumbnail)."""
    return await bambuddy.plate_thumbnail(lfid, plate_index) or await bambuddy.file_thumbnail(lfid)


async def _send_plate_question(group_id, lfid, name, plates):
    text = colors.build_plate_question(name, plates)
    srcs = [_plate_source(p, lfid) for p in plates]
    raws = await asyncio.gather(*(_thumbnail(l, idx) for l, idx in srcs))
    # Stamp each thumbnail with its list position (what the text lists and a reply
    # selects) so a multi-image gallery isn't ambiguous.
    shrunk = await asyncio.gather(*(
        asyncio.to_thread(swatch.numbered_thumbnail, r, i)
        for i, r in enumerate(raws, 1) if r
    ))
    attachments = [s for s in shrunk if s]
    await signal_client.send_to_group(group_id, text, attachments=attachments or None)


async def _ask_colors(group_id, job_id, lfid, label, plate):
    """Set the dialog to await colors for one plate and send the color question
    (plate thumbnail + swatch). ``label`` is the display title (incl. plate name
    when relevant)."""
    required = colors.plate_required(plate)
    status = await bambuddy.printer_status(config.PRINTER_ID)
    ams = colors.ams_snapshot(status)
    store.update_dialog(
        job_id, stage="awaiting_colors",
        plate_index=plate.get("index"), plate_name=plate.get("name") or "",
        required_colors=json.dumps(required), ams_snapshot=json.dumps(ams),
    )
    src_lfid, src_idx = _plate_source(plate, lfid)
    raw, chart = await asyncio.gather(
        _thumbnail(src_lfid, src_idx),
        asyncio.to_thread(swatch.build, label, required, ams),
    )
    thumb = await asyncio.to_thread(swatch.shrink_image, raw) if raw else None
    attachments = [a for a in (thumb, chart) if a]
    await signal_client.send_to_group(
        group_id, colors.build_question(label, required, ams), attachments=attachments or None
    )


def _parse_nums(message):
    return [int(x) for x in re.findall(r"\d+", message or "")]


async def _reply(group_id, message):
    """A numbered reply means whatever the open dialog's stage expects."""
    job = store.active_job(group_id)
    if not job:
        await signal_client.send_to_group(
            group_id, "Es gibt gerade keinen offenen Job. Schick mir ein Modell — "
            "MakerWorld-Link oder eine Datei (.3mf/.gcode/.stl/.zip)."
        )
        return
    stage = job["stage"]
    if stage == "awaiting_profile":
        await _pick_profile(group_id, job, message)
    elif stage == "awaiting_plate":
        await _pick_plates(group_id, job, message)
    elif stage == "awaiting_colors":
        await _config(group_id, job, message)
    # resolving / configuring: a reply arrived mid-processing → ignore


async def _pick_profile(group_id, job, message):
    profiles = json.loads(job["profiles"] or "[]")
    nums = _parse_nums(message)
    if len(nums) != 1 or not (1 <= nums[0] <= len(profiles)):
        await signal_client.send_to_group(
            group_id, f"⚠️ Bitte eine Zahl zwischen 1 und {len(profiles)} schicken (welches Profil)."
        )
        return
    if not store.claim_stage(group_id, "awaiting_profile", "configuring"):
        return  # duplicate/concurrent reply
    profile_id = profiles[nums[0] - 1]["profile_id"]
    try:
        await _after_profile(group_id, job["id"], job["model_id"], job["model_name"], profile_id)
    except Exception:
        log.exception("after_profile failed")
        store.discard_dialog(job["id"])
        await signal_client.send_to_group(group_id, "❌ Konnte das Profil nicht laden.")


async def _pick_plates(group_id, job, message):
    plates = json.loads(job["plates"] or "[]")
    nums = _parse_nums(message)
    # dedupe preserving order; each must be a valid 1-based position in the list
    chosen, seen = [], set()
    for n in nums:
        if 1 <= n <= len(plates) and n not in seen:
            seen.add(n)
            chosen.append(n)
    if not chosen or len(chosen) != len(nums):
        await signal_client.send_to_group(
            group_id,
            f"⚠️ Bitte eine oder mehrere Zahlen zwischen 1 und {len(plates)} schicken "
            "(welche Plates), z.B. „1“ oder „1 3“.",
        )
        return
    if not store.claim_stage(group_id, "awaiting_plate", "configuring"):
        return
    selected = [plates[n - 1] for n in chosen]
    first, rest = selected[0], selected[1:]
    store.update_dialog(job["id"], pending_plates=json.dumps([p["index"] for p in rest]))
    label = _plate_label(job["model_name"], first, multi=True)
    try:
        await _ask_colors(group_id, job["id"], job["library_file_id"], label, first)
    except Exception:
        log.exception("ask_colors failed")
        store.discard_dialog(job["id"])
        await signal_client.send_to_group(group_id, "❌ Da ist was schiefgelaufen.")


def _plate_label(model_name, plate, multi):
    if multi:
        return f'{model_name} — {plate.get("name") or f"Plate {plate.get('index')}"}'
    return model_name


async def _config(group_id, job, message):
    """Record the color choice for the current plate. Ask the next plate's colors
    if any are still pending; once every selected plate has its colors, slice +
    queue them all at once (collect-then-slice for nicer UX)."""
    required = json.loads(job["required_colors"] or "[]")
    ams = json.loads(job["ams_snapshot"] or "[]")
    ok, mapping, error = colors.parse_reply(message, required, ams)
    if not ok:
        await signal_client.send_to_group(group_id, error)
        return
    if not store.claim_stage(group_id, "awaiting_colors", "configuring"):
        return  # a duplicate/concurrent reply already handled it
    job_id = job["id"]
    try:
        plates = json.loads(job["plates"] or "[]")
        pending = json.loads(job["pending_plates"] or "[]")
        decisions = json.loads(job["decisions"] or "[]")
        plate_index = job["plate_index"]
        plate = next((p for p in plates if p.get("index") == plate_index), {"index": plate_index})
        label = _plate_label(job["model_name"], plate, len(plates) > 1)
        decisions.append({"index": plate_index, "mapping": mapping, "required": required, "ams": ams})
        nums = " ".join(str(m + 1) for m in mapping)
        if pending:
            store.update_dialog(job_id, decisions=json.dumps(decisions),
                                pending_plates=json.dumps(pending[1:]))
            await signal_client.send_to_group(
                group_id, f'👍 Farben für „{label}" notiert: {nums}\n➡️ Weiter mit dem nächsten Plate:'
            )
            next_idx = pending[0]
            next_plate = next((p for p in plates if p.get("index") == next_idx), {"index": next_idx, "filaments": []})
            await _ask_colors(group_id, job_id, job["library_file_id"],
                              _plate_label(job["model_name"], next_plate, True), next_plate)
        else:
            store.update_dialog(job_id, decisions=json.dumps(decisions))
            await _slice_all(group_id, job, decisions, plates)
    except Exception:
        log.exception("config failed")
        store.discard_dialog(job_id)
        await signal_client.send_to_group(group_id, "❌ Da ist was schiefgelaufen.")


_EMPTY_SLICE_G = 0.5  # purge line alone is ~0.4g → below this means no object sliced


async def _slice_weight(file_id):
    """Total filament grams of a sliced file's plates, or None if unknown."""
    try:
        data = await bambuddy.list_plates(file_id)
        plates = (data or {}).get("plates") or []
        return sum((p.get("filament_used_grams") or 0) for p in plates)
    except Exception:
        return None


async def _slice_all(group_id, job, decisions, plates):
    """Slice + queue every collected plate decision in order, then report once.
    Per-plate errors are isolated so one bad plate doesn't lose the others."""
    multi = len(plates) > 1
    n = len(decisions)
    if n > 1:
        await signal_client.send_to_group(
            group_id, f'🔧 Alle Farben da — ich slice & reihe jetzt {n} Plates ein … (kurz Geduld)'
        )
    lines = []
    for d in decisions:
        plate = next((p for p in plates if p.get("index") == d["index"]), {"index": d["index"]})
        label = _plate_label(job["model_name"], plate, multi)
        item_lfid, src_plate = _plate_source(plate, job["library_file_id"])
        # A zip item is its own single-plate file; only a real multi-plate file
        # (no per-item lfid) needs plate_id on the fallback path.
        within_file_multi = plate.get("library_file_id") is None and multi
        nums = " ".join(str(m + 1) for m in d["mapping"])
        try:
            if n == 1:
                await signal_client.send_to_group(
                    group_id, f'🔧 Slice „{label}" für {config.PRINTER_MODEL} … (kurz Geduld)'
                )
            file_id, note = await _reslice(item_lfid, d["required"], d["ams"], d["mapping"], src_plate)
            # Safety net: a slice with no object (off-bed / degenerate mesh) yields
            # only the purge line (~0.4g) → don't queue a blank "successful" print.
            weight = await _slice_weight(file_id)
            if weight is not None and weight < _EMPTY_SLICE_G:
                log.warning("empty slice for %s (%.2fg)", label, weight)
                lines.append(f'⚠️ „{label}" kam leer raus (Objekt nicht aufs Bett platzierbar) — übersprungen.')
                continue
            # A successful re-slice yields a single-plate file; only the fallback
            # to the original multi-plate file still needs plate_id.
            plate_id = src_plate if (file_id == item_lfid and within_file_multi) else None
            resp = await bambuddy.queue(file_id, d["mapping"], plate_id=plate_id)
            item_id = resp.get("id") if isinstance(resp, dict) else None
            store.add_queued(group_id, job["sender"], label, file_id, item_id)
            lines.append(f'✅ „{label}" — Farben {nums}{note}')
        except Exception:
            log.exception("slice/queue failed for plate %s", d.get("index"))
            lines.append(f'❌ „{label}" fehlgeschlagen')
    store.delete_job(job["id"])
    head = "Alles in der Queue! " if n > 1 else ""
    await signal_client.send_to_group(
        group_id,
        "\n".join(lines) + f'\n{head}Ich sag dir Bescheid, wenn er fertig ist. '
        "(!progress · !liste · !abbrechen · !help)",
    )


async def _reslice(library_file_id, required, ams, mapping, plate=None):
    """Re-slice the imported file (one plate) for the target printer so it doesn't
    print with the MakerWorld profile's machine slice (e.g. X1C on a P1S). Returns
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
        started = await bambuddy.slice_file(library_file_id, printer_p, process_p, filament_ps, plate=plate)
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


async def _skip(group_id):
    """Skip the current plate's color question, keeping the already-configured
    plates. Continues with the next pending plate, or slices what's collected."""
    job = store.active_job(group_id)
    if not job or job["stage"] != "awaiting_colors":
        await signal_client.send_to_group(group_id, "Gerade ist keine Farbfrage offen zum Überspringen.")
        return
    if not store.claim_stage(group_id, "awaiting_colors", "configuring"):
        return
    plates = json.loads(job["plates"] or "[]")
    pending = json.loads(job["pending_plates"] or "[]")
    decisions = json.loads(job["decisions"] or "[]")
    plate = next((p for p in plates if p.get("index") == job["plate_index"]), {"index": job["plate_index"]})
    label = _plate_label(job["model_name"], plate, len(plates) > 1)
    if pending:
        store.update_dialog(job["id"], pending_plates=json.dumps(pending[1:]))
        await signal_client.send_to_group(group_id, f'⏭️ „{label}" übersprungen. ➡️ Nächstes Plate:')
        next_idx = pending[0]
        next_plate = next((p for p in plates if p.get("index") == next_idx), {"index": next_idx, "filaments": []})
        await _ask_colors(group_id, job["id"], job["library_file_id"],
                          _plate_label(job["model_name"], next_plate, True), next_plate)
    elif decisions:
        await signal_client.send_to_group(
            group_id, f'⏭️ „{label}" übersprungen. Ich reihe die {len(decisions)} konfigurierten ein:'
        )
        await _slice_all(group_id, job, decisions, plates)
    else:
        store.discard_dialog(job["id"])
        await signal_client.send_to_group(group_id, "⏭️ Übersprungen — nichts mehr zu drucken übrig.")


async def _cancel(group_id):
    """Drop an open dialog, else remove the last still-pending queue item.
    A print that is already running is never stopped."""
    dialog = store.active_job(group_id)
    if dialog:
        # Don't throw away already-configured plates: queue those, drop the rest.
        decisions = json.loads(dialog["decisions"] or "[]")
        if decisions and store.claim_stage(group_id, dialog["stage"], "configuring"):
            plates = json.loads(dialog["plates"] or "[]")
            await signal_client.send_to_group(
                group_id,
                f'🗑️ Aktuelles/restliche Plates verworfen. Die {len(decisions)} schon '
                "konfigurierten reihe ich ein:",
            )
            await _slice_all(group_id, dialog, decisions, plates)
            return
        store.discard_dialog(dialog["id"])
        await signal_client.send_to_group(
            group_id,
            f'🗑️ Abgebrochen: „{dialog["model_name"]}" verworfen. '
            "Schick mir ein neues Modell — Link oder Datei —, wenn du willst.",
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


async def _go(group_id):
    """Confirm the build plate is clear so Bambuddy releases the next queued print
    (Bambuddy is set to wait for manual plate-clear confirmation between jobs)."""
    try:
        await bambuddy.clear_plate(config.PRINTER_ID)
    except Exception:
        log.exception("clear-plate failed")
        await signal_client.send_to_group(
            group_id, "❌ Konnte die Platte nicht freigeben — probier's gleich nochmal."
        )
        return
    await signal_client.send_to_group(
        group_id, "✅ Platte als frei bestätigt — der nächste Druck kann starten. 🚀"
    )


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
        if item is None:
            # Item aged out of Bambuddy — stop tracking, don't poll a 404 forever.
            store.set_stage(job["id"], "done")
            continue
        status = item.get("status")
        if status == "completed":
            await signal_client.send_to_group(
                job["group_id"],
                f'✅ „{job["model_name"]}" ist fertig gedruckt! 🎉\n'
                "Wenn die Platte frei ist: !go → nächster Druck startet.",
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
