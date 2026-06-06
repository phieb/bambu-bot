"""Routing + business logic. `claims` is a side-effect-free predicate the
dispatcher asks before forwarding; `handle` does the real work.

Intake is a small state machine carried by the open dialog row (see store):
``resolving → [awaiting_profile] → [awaiting_plate] → awaiting_colors → queued``.
The profile and plate steps auto-skip when there's only one of each. A numbered
reply means whatever the current stage expects, so it's dispatched on stage.
"""
import asyncio
import datetime
import io
import json
import logging
import re
import time
import zipfile

import bambuddy
import classify
import colors
import config
import i18n
import signal_client
import slicing
import slicer
import stl
import store
import swatch
import thingiverse

log = logging.getLogger("bambu-bot")


def _lang(group_id):
    """The display language ('de'|'en') for a group — default German."""
    return store.get_lang(group_id)


def t(group_id, key, **kw):
    """Localized message for ``key`` in ``group_id``'s language."""
    return i18n.t(_lang(group_id), key, **kw)


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
        if parsed["lang_command"]:
            return "dm_lang"
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
    if parsed["eject_command"]:
        return "group_eject"
    if parsed["plate_command"]:
        return "group_plate"
    if parsed["is_sync"]:
        return "group_sync"
    if parsed["lang_command"]:
        return "group_lang"
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
            await signal_client.send_to_group(group_id, colors.other_model_text(_lang(group_id)))
        elif route == "group_other_model":
            gid = parsed["group_send_id"]
            await signal_client.send_to_group(gid, colors.other_model_text(_lang(gid)))
        elif route == "dm_lang":
            group_id = await _ensure_group(parsed["sender"])
            await _set_lang(group_id, parsed["lang_command"])
        elif route == "group_lang":
            await _set_lang(parsed["group_send_id"], parsed["lang_command"])
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
        elif route == "group_eject":
            await _eject(parsed["group_send_id"], parsed["eject_command"])
        elif route == "group_plate":
            await _plate(parsed["group_send_id"], parsed["plate_command"])
        elif route == "group_sync":
            await _sync(parsed["group_send_id"])
        elif route == "group_help":
            gid = parsed["group_send_id"]
            await signal_client.send_to_group(gid, colors.help_text(_lang(gid)))
        elif route == "group_unknown":
            gid = parsed["group_send_id"]
            await signal_client.send_to_group(gid, colors.unknown_text(_lang(gid)))
    except Exception:
        log.exception("handle failed (route=%s)", route)


async def _ensure_group(sender):
    existing = store.get_group_by_sender(sender)
    if existing:
        return existing["group_id"]
    group_id = await signal_client.create_group(config.GROUP_NAME, [sender])
    store.save_group(sender, group_id, config.GROUP_NAME)
    return group_id


async def _set_lang(group_id, cmd):
    """Switch a group's reply language, or (cmd == 'show') report the current one.
    The confirmation is sent in the *target* language so the switch is obvious."""
    if cmd in ("de", "en"):
        store.set_lang(group_id, cmd)
        await signal_client.send_to_group(group_id, i18n.t(cmd, "lang_set"))
    else:
        await signal_client.send_to_group(group_id, t(group_id, "lang_status"))


# ----- intake & the profile → plate → colors state machine -----

async def _intake(group_id, sender, url):
    lang = _lang(group_id)
    if store.active_job(group_id):
        await signal_client.send_to_group(group_id, i18n.t(lang, "busy_link"))
        return
    try:
        resolved = await bambuddy.resolve(url)
    except Exception:
        log.exception("resolve failed")
        await signal_client.send_to_group(group_id, i18n.t(lang, "resolve_failed"))
        return
    model_id = resolved.get("model_id")
    name = colors.model_name(resolved, lang)
    profiles = colors.profiles_list(resolved, config.PRINTER_MODEL)
    job_id = store.create_dialog(group_id, sender, model_id, name)
    if len(profiles) > 1:
        store.update_dialog(job_id, profiles=json.dumps(profiles), stage="awaiting_profile")
        await signal_client.send_to_group(
            group_id, colors.build_profile_question(name, profiles, config.PRINTER_MODEL, lang)
        )
        return
    profile_id = profiles[0]["profile_id"] if profiles else colors.chosen_profile_id(resolved)
    store.update_dialog(job_id, stage="configuring")
    try:
        await _after_profile(group_id, job_id, model_id, name, profile_id)
    except Exception:
        log.exception("import/plates failed")
        store.discard_dialog(job_id)
        await signal_client.send_to_group(group_id, i18n.t(lang, "import_failed"))


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
        await signal_client.send_to_group(group_id, t(group_id, "busy_model"))
        return True
    return False


async def _intake_file(group_id, sender, file_meta):
    """A Signal-uploaded model file: fetch its bytes, then run the shared intake."""
    if await _busy(group_id):
        return
    lang = _lang(group_id)
    name = file_meta["filename"]
    await signal_client.send_to_group(group_id, i18n.t(lang, "file_processing", name=name))
    content = await signal_client.fetch_attachment(file_meta["id"])
    if not content:
        await signal_client.send_to_group(group_id, i18n.t(lang, "file_load_failed_signal"))
        return
    await _process_model_bytes(group_id, sender, content, name, file_meta["kind"])


async def _intake_url(group_id, sender, url):
    """A direct link to a model file: download it, then run the shared intake."""
    if await _busy(group_id):
        return
    lang = _lang(group_id)
    name = classify.filename_from_url(url)
    kind = classify.file_kind(name)
    await signal_client.send_to_group(group_id, i18n.t(lang, "url_loading", name=name))
    content = await signal_client.fetch_bytes(url)
    if not content:
        await signal_client.send_to_group(group_id, i18n.t(lang, "url_load_failed"))
        return
    await _process_model_bytes(group_id, sender, content, name, kind)


async def _intake_thingiverse(group_id, sender, thing_id):
    """A Thingiverse link: pull the thing's printable files via the API, bundle
    them into a zip, then run the shared zip intake (multi-file → selection)."""
    if await _busy(group_id):
        return
    lang = _lang(group_id)
    await signal_client.send_to_group(group_id, i18n.t(lang, "thingiverse_loading", thing_id=thing_id))
    try:
        zip_bytes, name = await thingiverse.build_zip(thing_id)
    except Exception:
        log.exception("thingiverse fetch failed")
        await signal_client.send_to_group(group_id, i18n.t(lang, "thingiverse_failed"))
        return
    if not zip_bytes:
        await signal_client.send_to_group(group_id, i18n.t(lang, "thingiverse_no_files"))
        return
    await _process_model_bytes(group_id, sender, zip_bytes, name, "zip")


async def _process_model_bytes(group_id, sender, content, name, kind):
    """Upload the bytes into the Signal library folder and start the right flow:
    .zip → extract into selectable items; .gcode → queue as-is; else → plates."""
    lang = _lang(group_id)
    file_word = "File" if lang == "en" else "Datei"
    job_id = store.create_dialog(group_id, sender, None, name)
    store.update_dialog(job_id, stage="configuring")
    try:
        folder_id = await bambuddy.ensure_folder(config.SIGNAL_FOLDER_NAME)
        if kind == "zip":
            # Arrange every STL inside the zip onto the bed before extraction.
            content = await asyncio.to_thread(stl.arrange_zip, content)
            result = await bambuddy.extract_zip(content, name, folder_id)
            items = [
                {"filename": f.get("filename") or f"{file_word} {i}", "file_id": f["file_id"]}
                for i, f in enumerate(result.get("files") or [], 1)
            ]
            if not items:
                store.discard_dialog(job_id)
                await signal_client.send_to_group(group_id, i18n.t(lang, "zip_no_files"))
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
            store.delete_job(job_id)
            # Accept gcode ONLY if it's positively identified as sliced for this
            # printer (allow-list). Wrong-printer gcode can misbehave badly on the
            # P1S, and raw .gcode / blank-id files carry no printer info to check
            # — so reject anything we can't confirm is for the P1S. A P1S-sliced
            # .gcode.3mf carries the matching id and passes.
            model = _sliced_printer_model(content)
            if model != _TARGET_MODEL_ID:
                if model:
                    for_what = _PRINTER_NAMES.get(model) or i18n.t(lang, "gcode_other_device", model=model)
                    why = i18n.t(lang, "gcode_for_other_named", for_what=for_what, model=config.PRINTER_MODEL)
                else:
                    why = i18n.t(lang, "gcode_raw_unverified", model=config.PRINTER_MODEL)
                await signal_client.send_to_group(
                    group_id,
                    i18n.t(lang, "gcode_rejected", name=name, why=why, model=config.PRINTER_MODEL))
                return
            # A .gcode.3mf can hold its gcode under a non-1 plate index (e.g. a
            # single plate from a multi-plate project), so tell the printer which
            # plate to print or it defaults to 1, finds no gcode, and rejects the
            # file (HMS 0500-4003).
            plate_id = _gcode_plate_index(content)
            queued, note = await _queue_guarded(group_id, sender, name, lfid, [], plate_id=plate_id)
            await signal_client.send_to_group(
                group_id,
                i18n.t(lang, "queued_presliced", name=name)
                if queued else i18n.t(lang, "label_note", name=name, note=note),
            )
        else:
            await _from_library_file(group_id, job_id, name, lfid)
    except Exception:
        log.exception("file intake failed")
        store.discard_dialog(job_id)
        await signal_client.send_to_group(group_id, i18n.t(lang, "file_intake_failed"))


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
    text = colors.build_plate_question(name, plates, _lang(group_id))
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
    if not required:
        # Some single-colour models / raw STLs report a plate with no filament
        # list — treat it as one filament so the user picks a single slot. (0
        # colours would dead-end parse_reply and leave re-slice without a preset.)
        required = [{"index": 0, "type": "", "color": "", "name": ""}]
    status = await bambuddy.printer_status(config.PRINTER_ID)
    ams = colors.ams_snapshot(status)
    store.update_dialog(
        job_id, stage="awaiting_colors",
        plate_index=plate.get("index"), plate_name=plate.get("name") or "",
        required_colors=json.dumps(required), ams_snapshot=json.dumps(ams),
    )
    lang = _lang(group_id)
    src_lfid, src_idx = _plate_source(plate, lfid)
    raw, chart = await asyncio.gather(
        _thumbnail(src_lfid, src_idx),
        asyncio.to_thread(swatch.build, label, required, ams, lang),
    )
    thumb = await asyncio.to_thread(swatch.shrink_image, raw) if raw else None
    attachments = [a for a in (thumb, chart) if a]
    await signal_client.send_to_group(
        group_id, colors.build_question(label, required, ams, lang), attachments=attachments or None
    )


def _parse_nums(message):
    return [int(x) for x in re.findall(r"\d+", message or "")]


async def _reply(group_id, message):
    """A numbered reply means whatever the open dialog's stage expects."""
    job = store.active_job(group_id)
    if not job:
        await signal_client.send_to_group(group_id, t(group_id, "no_open_job"))
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
            group_id, t(group_id, "profile_pick_invalid", n=len(profiles))
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
        await signal_client.send_to_group(group_id, t(group_id, "profile_load_failed"))


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
            group_id, t(group_id, "plate_pick_invalid", n=len(plates))
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
        await signal_client.send_to_group(group_id, t(group_id, "generic_failed"))


def _plate_label(model_name, plate, multi):
    if multi:
        return f'{model_name} — {plate.get("name") or f"Plate {plate.get('index')}"}'
    return model_name


async def _config(group_id, job, message):
    """Record the color choice for the current plate. Ask the next plate's colors
    if any are still pending; once every selected plate has its colors, slice +
    queue them all at once (collect-then-slice for nicer UX)."""
    lang = _lang(group_id)
    required = json.loads(job["required_colors"] or "[]")
    ams = json.loads(job["ams_snapshot"] or "[]")
    ok, mapping, error = colors.parse_reply(message, required, ams, lang)
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
                group_id, i18n.t(lang, "colors_noted", label=label, nums=nums)
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
        await signal_client.send_to_group(group_id, i18n.t(lang, "generic_failed"))


EJECT_FLAG = "eject_enabled"
_MAX_Z_RE = re.compile(r"^;\s*max_z_height:\s*([0-9.]+)", re.M)


def _max_z_height(data):
    """Highest ``; max_z_height:`` across a sliced file's plate gcode(s), or None.
    A library ``.gcode.3mf`` is a zip (gcode under ``Metadata/plate_*.gcode``); a
    plain ``.gcode`` upload is the text itself. (Parsing G1 Z moves is unreliable
    — the end-gcode parks the bed at Z250.)"""
    heights = []
    if data[:2] == b"PK":  # zip container
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
            for n in z.namelist():
                if n.startswith("Metadata/plate_") and n.endswith(".gcode"):
                    m = _MAX_Z_RE.search(z.read(n).decode("utf-8", "replace"))
                    if m:
                        heights.append(float(m.group(1)))
        except (zipfile.BadZipFile, KeyError):
            return None
    else:
        m = _MAX_Z_RE.search(data.decode("utf-8", "replace"))
        if m:
            heights.append(float(m.group(1)))
    return max(heights) if heights else None


_PLATE_IDX_RE = re.compile(r"plate_(\d+)\.gcode$")


def _gcode_plate_index(data):
    """Plate index N of a single-plate ``.gcode.3mf`` (gcode under
    ``Metadata/plate_N.gcode``), so the queue can tell the printer which plate to
    print. None for a plain ``.gcode`` (no container) or if it isn't a single,
    clearly-numbered plate."""
    if not data or data[:2] != b"PK":
        return None
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return None
    plates = [n for n in z.namelist()
              if n.startswith("Metadata/plate_") and n.endswith(".gcode")]
    if len(plates) != 1:
        return None
    m = _PLATE_IDX_RE.search(plates[0])
    return int(m.group(1)) if m else None


# Bambu printer model ids as written to slice_info.config (printer_model_id).
_PRINTER_NAMES = {"N1": "A1 mini", "N2S": "A1", "C11": "P1P", "C12": "P1S",
                  "BL-P001": "X1C", "BL-P002": "X1", "C13": "X1E"}
_MODEL_IDS = {name: mid for mid, name in _PRINTER_NAMES.items()}  # name -> id
_TARGET_MODEL_ID = _MODEL_IDS.get(config.PRINTER_MODEL)  # e.g. "P1S" -> "C12"
_MODEL_ID_RE = re.compile(r'printer_model_id"\s*value="([^"]*)"')


def _sliced_printer_model(data):
    """The printer_model_id a pre-sliced ``.gcode.3mf`` was sliced for (from
    slice_info.config), or '' if unknown (our own sidecar slice leaves it blank).
    Used to catch a file sliced for the wrong machine before it hits the bed."""
    if not data or data[:2] != b"PK":
        return ""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        for n in z.namelist():
            if n.endswith("slice_info.config"):
                m = _MODEL_ID_RE.search(z.read(n).decode("utf-8", "replace"))
                return (m.group(1) if m else "").strip()
    except zipfile.BadZipFile:
        pass
    return ""


# Positively identify a sliced file's machine from its plate gcode: the
# machine_start / change_filament headers carry ";===== machine: P1S-0.4 ===" and
# ";=P1S ...". The bot never converts foreign gcode — it queues a file only if it
# is provably this printer, and otherwise declines it (foreign A1/X1 gcode on the
# P1S drives the head into the frame / jams the cutter).
_P1S_GCODE_RE = re.compile(rb";\s*=+\s*(?:machine:\s*)?P1S\b", re.I)
_MACHINE_HINT_RE = re.compile(rb";\s*=+\s*(?:machine:\s*)?([A-Za-z][\w.\-]*)")


def _plate_gcode_heads(data, limit=65536):
    """Leading bytes of each plate gcode in a sliced file (the machine headers sit
    up top). A library ``.gcode.3mf`` is a zip; a plain ``.gcode`` is the text."""
    if not data:
        return []
    if data[:2] == b"PK":
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            return []
        return [z.read(n)[:limit] for n in z.namelist()
                if n.startswith("Metadata/plate_") and n.endswith(".gcode")]
    return [data[:limit]]


def _is_p1s_gcode(data):
    """True iff every plate gcode is positively identified as sliced for this
    printer. Foreign or unrecognized machine gcode → False (declined, never
    converted)."""
    heads = _plate_gcode_heads(data)
    return bool(heads) and all(_P1S_GCODE_RE.search(h) for h in heads)


def _gcode_machine_hint(data):
    """Best-effort machine name from a sliced file's gcode header (e.g. "A1mini",
    "X1C") for a clearer 'not for this printer' message; "" if unknown."""
    for h in _plate_gcode_heads(data, 8192):
        m = _MACHINE_HINT_RE.search(h)
        if m:
            return m.group(1).decode("ascii", "replace")
    return ""


# The nozzle diameter a file was sliced for sits in the same machine header, e.g.
# ";===== machine: P1S-0.4 ===" → "0.4". The P1S has no nozzle auto-detect, so we
# compare this against the diameter the printer reports as fitted and refuse a
# mismatch: 0.4 gcode pushed through a 0.2 nozzle under-extrudes / jams, and a
# coarse slice on a fine nozzle won't bond. TODO: actually *support* other nozzles
# — slice with the matching preset/profile (config.NOZZLE_DIAMETER drives the
# Bambuddy slice; the sidecar fallback still needs a profiles/printer_p1s_0.2.json)
# instead of only gating. For now the whole pipeline assumes 0.4 and we only guard.
_GCODE_NOZZLE_RE = re.compile(rb";\s*=+\s*machine:\s*\S+?-([0-9.]+)\b")


def _gcode_nozzle(data):
    """The nozzle diameter (str, e.g. '0.4') a sliced file was sliced for, read from
    its machine header, or '' if not found."""
    for h in _plate_gcode_heads(data, 8192):
        m = _GCODE_NOZZLE_RE.search(h)
        if m:
            return m.group(1).decode("ascii", "replace")
    return ""


def _nozzle_eq(a, b):
    """Whether two nozzle-diameter strings denote the same size ('0.4' == '0.40')."""
    try:
        return abs(float(a) - float(b)) < 1e-3
    except (TypeError, ValueError):
        return a == b


async def _queue_guarded(group_id, sender, label, file_id, mapping, plate_id=None):
    """Queue one sliced file, but only if it is positively sliced for this printer
    — the universal safety gate. The bot never converts foreign-machine gcode; a
    file that isn't provably P1S (foreign A1/X1/… or unrecognized) is declined with
    a clear message rather than risking a crash. It also refuses a file sliced for a
    different nozzle than the one fitted (the P1S can't auto-detect its nozzle, so
    0.4 gcode would silently jam a 0.2 nozzle). Returns (queued, note).
    ``plate_id`` is which plate the printer should print (a re-sliced single-plate
    file keeps the source plate's index, so it must be passed or the printer
    defaults to plate 1 and can't parse the file). With auto-eject on, the job is
    queued with Bambuddy's per-model G-code injection on (the height-tailored
    Farmloop eject snippet runs there, computed from ``max_z_height`` at dispatch);
    the bot only pre-screens height so a part taller than the bender limit — or one
    whose height can't be read — is refused *before* it reaches the queue. With
    eject off, no injection and no height limit."""
    # Download the container once: it backs the machine-safety check and (with
    # eject) the height pre-screen. /download is reliable (vs /gcode, which
    # returns extracted text for files Bambuddy typed as 3mf).
    lang = _lang(group_id)
    data = await bambuddy.download_file(file_id)
    if not _is_p1s_gcode(data):
        hint = _gcode_machine_hint(data)
        whose = (i18n.t(lang, "queue_foreign_whose_named", hint=hint) if hint
                 else i18n.t(lang, "queue_foreign_whose_other"))
        return False, i18n.t(lang, "queue_foreign", label=label, whose=whose,
                             model=config.PRINTER_MODEL)
    # Nozzle gate: the P1S can't detect its own nozzle, so a 0.4 slice would happily
    # run through a fitted 0.2 nozzle and jam. Refuse only on a *positive* mismatch
    # (both diameters known and different) — if the printer's nozzle can't be read we
    # don't add a failure mode to the common 0.4 case.
    want_nozzle = _gcode_nozzle(data)
    mounted_nozzle = await bambuddy.mounted_nozzle(config.PRINTER_ID)
    if want_nozzle and mounted_nozzle and not _nozzle_eq(want_nozzle, mounted_nozzle):
        return False, i18n.t(lang, "queue_nozzle_mismatch", label=label,
                             want=want_nozzle, mounted=mounted_nozzle)
    eject_on = store.get_flag(EJECT_FLAG, False)
    if eject_on:
        # Pre-screen height only — the eject snippet itself runs via Bambuddy's
        # per-model injection (queued with gcode_injection=True below). Refuse a
        # part too tall for the bender, or one whose height we can't read, before
        # it reaches the queue so the user gets a clear note instead of a
        # scheduler-side failure.
        h = _max_z_height(data) if data else None
        if h is None:
            return False, i18n.t(lang, "eject_height_unknown")
        if h > config.EJECT_MAX_HEIGHT_MM:
            return False, i18n.t(lang, "eject_too_tall", h=h, max=config.EJECT_MAX_HEIGHT_MM)
    resp = await bambuddy.queue(file_id, mapping, plate_id=plate_id, gcode_injection=eject_on)
    item_id = resp.get("id") if isinstance(resp, dict) else None
    store.add_queued(group_id, sender, label, file_id, item_id, eject=eject_on)
    return True, ""


async def _eject(group_id, command):
    """Toggle / show Farmloop auto-eject. On also turns off Bambuddy's manual
    plate-clear wait so the queue flows without !go; off restores it."""
    if command in ("on", "off"):
        enable = command == "on"
        try:
            await bambuddy.set_require_plate_clear(not enable)
        except Exception:
            log.exception("toggling require_plate_clear failed")
        store.set_flag(EJECT_FLAG, enable)
    lang = _lang(group_id)
    enabled = store.get_flag(EJECT_FLAG, False)
    if enabled:
        msg = i18n.t(lang, "eject_on", max=config.EJECT_MAX_HEIGHT_MM)
    else:
        msg = i18n.t(lang, "eject_off")
        # Jobs already in the queue keep their injected eject — flag them so it's
        # no surprise when they still auto-eject despite the switch being off.
        pending_eject = store.queued_eject_jobs()
        if pending_eject:
            names = "\n".join(f"  • {n}" for n in pending_eject)
            msg += i18n.t(lang, "eject_off_warning", n=len(pending_eject), names=names)
    await signal_client.send_to_group(group_id, msg)


BED_TYPE_FLAG = "bed_type"


def _bed_type():
    """The build plate currently set (sqlite, !platte), or the config default."""
    return store.get_setting(BED_TYPE_FLAG, config.BED_TYPE)


async def _plate(group_id, cmd):
    """Show / set which build plate is on the printer. Stored in sqlite and baked
    into every re-slice as the slicer's curr_bed_type (bed temp + first-layer Z).
    The P1S can't report its mounted plate, so the farmer sets it here on a swap."""
    lang = _lang(group_id)
    options = i18n.t(lang, "plate_options")
    if cmd["action"] == "set":
        store.set_setting(BED_TYPE_FLAG, cmd["bed_type"])
        await signal_client.send_to_group(
            group_id, i18n.t(lang, "plate_set", bed=cmd["bed_type"]))
        return
    current = _bed_type()
    if cmd["action"] == "unknown":
        await signal_client.send_to_group(
            group_id, i18n.t(lang, "plate_unknown", arg=cmd["arg"], current=current, options=options))
        return
    await signal_client.send_to_group(
        group_id, i18n.t(lang, "plate_status", current=current, options=options))


async def _slice_all(group_id, job, decisions, plates):
    """Slice + queue every collected plate decision in order, then report once.
    Per-plate errors are isolated so one bad plate doesn't lose the others."""
    lang = _lang(group_id)
    multi = len(plates) > 1
    n = len(decisions)
    if n > 1:
        await signal_client.send_to_group(group_id, i18n.t(lang, "slice_all_multi", n=n))
    lines = []
    queued_n = 0
    for d in decisions:
        plate = next((p for p in plates if p.get("index") == d["index"]), {"index": d["index"]})
        label = _plate_label(job["model_name"], plate, multi)
        item_lfid, src_plate = _plate_source(plate, job["library_file_id"])
        # A zip item is its own single-plate file; only a real multi-plate file
        # (no per-item lfid) needs plate_id to point the printer at plate N.
        within_file_multi = plate.get("library_file_id") is None and multi
        nums = " ".join(str(m + 1) for m in d["mapping"])
        try:
            if n == 1:
                await signal_client.send_to_group(
                    group_id, i18n.t(lang, "slice_one", label=label, model=config.PRINTER_MODEL)
                )
            file_id, note = await _reslice(item_lfid, d["required"], d["ams"], d["mapping"], src_plate, lang)
            # No clean P1S slice → abort this plate (the bot never queues a foreign
            # or unconverted original; the re-slice note says why).
            if not file_id:
                lines.append(i18n.t(lang, "label_note", name=label, note=note))
                continue
            # A re-slice writes the gcode under the *source* plate index (plate_N)
            # → tell the printer to print plate N, else it defaults to 1, finds no
            # gcode and rejects the file (HMS 0500-4003). zip/single stay default.
            plate_id = src_plate if within_file_multi else None
            queued, gate = await _queue_guarded(
                group_id, job["sender"], label, file_id, d["mapping"], plate_id)
            if queued:
                queued_n += 1
                lines.append(i18n.t(lang, "slice_ok_line", label=label, nums=nums, note=note))
            else:
                lines.append(i18n.t(lang, "label_note", name=label, note=gate))
        except Exception:
            log.exception("slice/queue failed for plate %s", d.get("index"))
            lines.append(i18n.t(lang, "slice_fail_line", label=label))
    store.delete_job(job["id"])
    if queued_n:
        head = i18n.t(lang, "slice_tail_head_multi") if queued_n > 1 else ""
        tail = i18n.t(lang, "slice_tail_queued", head=head)
    else:
        tail = i18n.t(lang, "slice_tail_none")
    await signal_client.send_to_group(group_id, "\n".join(lines) + tail)


async def _reslice(library_file_id, required, ams, mapping, plate=None, lang="de"):
    """Re-slice the imported file (one plate) for the target printer so it doesn't
    print with the MakerWorld profile's machine slice (e.g. X1C on a P1S). Returns
    (new_file_id, note), or (None, reason) if no clean target slice could be made.
    Bambuddy's slice is primary; if it fails (e.g. a multi-plate 3mf whose objects
    sit off-bed → "object conflicts"), the slicer sidecar is tried. The bot never
    queues the un-resliced original — a foreign-machine slice would crash the P1S —
    so on any miss it returns None and the caller aborts that plate."""
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
            return None, i18n.t(lang, "reslice_presets_missing")
        started = await bambuddy.slice_file(
            library_file_id, printer_p, process_p, filament_ps, plate=plate, bed_type=_bed_type())
        new_id = await _await_slice((started or {}).get("job_id"))
        if new_id:
            return new_id, i18n.t(lang, "reslice_note_bambu", model=config.PRINTER_MODEL)
        # Bambuddy's slice failed — typically a multi-plate 3mf whose objects sit
        # off-bed ("object conflicts"). Slice the plate straight on the real
        # slicer sidecar with bundled P1S profiles, which handles it.
        side_id = await _reslice_via_sidecar(library_file_id, plate, ams, mapping)
        if side_id:
            return side_id, i18n.t(lang, "reslice_note_sidecar", model=config.PRINTER_MODEL)
        return None, i18n.t(lang, "reslice_failed_both", model=config.PRINTER_MODEL)
    except Exception:
        log.exception("reslice failed")
        return None, i18n.t(lang, "reslice_exception")


async def _reslice_via_sidecar(lfid, plate, ams, mapping):
    """Slice plate ``plate`` of a 3mf directly on the slicer sidecar (bundled P1S
    printer + a filament profile matched to the mapped AMS slot) and upload the
    result to Bambuddy. Returns the new library_file_id, or None. Used when
    Bambuddy's own slice can't handle the file (off-bed multi-plate objects).
    NOTE: this fallback does not yet apply config.BED_TYPE — the sidecar gets only
    printer + filament profiles (no process), so curr_bed_type stays at the
    slicer's default ('Textured PEI Plate'). Rare path (off-bed multi-plate only);
    to honour the plate here we'd patch curr_bed_type into the 3mf's
    project_settings before sending."""
    data = await bambuddy.download_file(lfid)
    if not data:
        return None
    tray = next((a for a in ams if a["tray_id"] == mapping[0]), {}) if mapping else {}
    fil = slicer.filament_profile(tray.get("type"), tray.get("sub"))
    sliced = await slicer.slice_3mf(data, plate=plate or 1, filament=fil)
    if not sliced:
        return None
    folder = await bambuddy.ensure_folder(config.SIGNAL_FOLDER_NAME)
    up = await bambuddy.upload_library_file(sliced, f"sliced_plate{plate or 1}.gcode.3mf", folder_id=folder)
    return up.get("id") if isinstance(up, dict) else None


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
# Finished statuses hidden from !liste — they just pile up and clutter the view.
_DONE_QUEUE_STATUS = {"completed", "cancelled", "skipped", "failed"}


async def _skip(group_id):
    """Skip the current plate's color question, keeping the already-configured
    plates. Continues with the next pending plate, or slices what's collected."""
    lang = _lang(group_id)
    job = store.active_job(group_id)
    if not job or job["stage"] != "awaiting_colors":
        await signal_client.send_to_group(group_id, i18n.t(lang, "skip_nothing"))
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
        await signal_client.send_to_group(group_id, i18n.t(lang, "skip_next", label=label))
        next_idx = pending[0]
        next_plate = next((p for p in plates if p.get("index") == next_idx), {"index": next_idx, "filaments": []})
        await _ask_colors(group_id, job["id"], job["library_file_id"],
                          _plate_label(job["model_name"], next_plate, True), next_plate)
    elif decisions:
        await signal_client.send_to_group(
            group_id, i18n.t(lang, "skip_queue_configured", label=label, n=len(decisions))
        )
        await _slice_all(group_id, job, decisions, plates)
    else:
        store.discard_dialog(job["id"])
        await signal_client.send_to_group(group_id, i18n.t(lang, "skip_nothing_left"))


async def _cancel(group_id):
    """Drop an open dialog, else remove the last still-pending queue item.
    A print that is already running is never stopped."""
    lang = _lang(group_id)
    dialog = store.active_job(group_id)
    if dialog:
        # Don't throw away already-configured plates: queue those, drop the rest.
        decisions = json.loads(dialog["decisions"] or "[]")
        if decisions and store.claim_stage(group_id, dialog["stage"], "configuring"):
            plates = json.loads(dialog["plates"] or "[]")
            await signal_client.send_to_group(
                group_id, i18n.t(lang, "cancel_discard_rest", n=len(decisions))
            )
            await _slice_all(group_id, dialog, decisions, plates)
            return
        store.discard_dialog(dialog["id"])
        await signal_client.send_to_group(
            group_id, i18n.t(lang, "cancel_discarded", name=dialog["model_name"])
        )
        return
    job = store.last_queued_job(group_id)
    if not job:
        await signal_client.send_to_group(group_id, i18n.t(lang, "cancel_nothing"))
        return
    item = await bambuddy.get_queue_item(job["queue_item_id"])
    status = (item or {}).get("status")
    name = job["model_name"]
    if status == "pending":
        await bambuddy.delete_queue_item(job["queue_item_id"])
        store.mark_cancelled(job["id"])
        await signal_client.send_to_group(group_id, i18n.t(lang, "cancel_removed", name=name))
    elif status == "printing":
        await signal_client.send_to_group(group_id, i18n.t(lang, "cancel_printing", name=name))
    else:
        store.mark_cancelled(job["id"])
        await signal_client.send_to_group(
            group_id,
            i18n.t(lang, "cancel_not_cancelable", name=name,
                   status=status or i18n.t(lang, "status_unknown"))
        )


async def _list(group_id):
    lang = _lang(group_id)
    items = await bambuddy.list_queue()
    open_items = [it for it in (items or [])
                  if (it.get("status") or "").lower() not in _DONE_QUEUE_STATUS]
    if not open_items:
        await signal_client.send_to_group(group_id, i18n.t(lang, "list_empty"))
        return
    ejects = store.eject_by_item()  # {queue_item_id: bool} for bot-tracked jobs
    eject_tag = i18n.t(lang, "list_eject_tag")
    noeject_tag = i18n.t(lang, "list_noeject_tag")
    lines = []
    for i, it in enumerate(open_items, 1):
        nm = (it.get("library_file_name") or it.get("archive_name")
              or it.get("target_model") or f'#{it.get("id")}')
        st = it.get("status") or "?"
        # eject tag: 🧹 = with auto-eject, ✋ = without; nothing for jobs the bot
        # didn't queue (e.g. a Bambu Studio print — we don't know).
        e = ejects.get(it.get("id"))
        tag = eject_tag if e else (noeject_tag if e is False else "")
        lines.append(f'{i}. {_STATUS_EMOJI.get(st, "")} {nm} ({st}){tag}'.replace("  ", " "))
    await signal_client.send_to_group(group_id, i18n.t(lang, "list_header") + "\n".join(lines))


async def _sync(group_id):
    """Adopt open queue jobs that weren't sent through the bot (Bambu Studio Send,
    Virtual Printer, web UI) as completion trackers for THIS group, so they get
    the same 'started/finished/failed' notifications. Already-tracked items (incl.
    earlier syncs and the bot's own jobs) and finished ones are skipped, so it's
    safe to run repeatedly."""
    items = await bambuddy.list_queue()
    tracked = store.tracked_item_ids()
    adopted = []
    for it in items or []:
        iid = it.get("id")
        status = (it.get("status") or "").lower()
        if iid is None or iid in tracked or status in _DONE_QUEUE_STATUS:
            continue
        name = (it.get("library_file_name") or it.get("archive_name")
                or it.get("target_model") or f'#{iid}')
        jid = store.add_queued(group_id, "", name, None, iid, eject=False)
        # Already mid-print → track at 'printing' so it only fires the finished
        # message, not a misleading "starts now".
        if status == "printing":
            store.set_stage(jid, "printing")
        adopted.append(name)
    lang = _lang(group_id)
    if not adopted:
        await signal_client.send_to_group(group_id, i18n.t(lang, "sync_in_sync"))
        return
    lines = "\n".join(f"  • {n}" for n in adopted)
    await signal_client.send_to_group(
        group_id, i18n.t(lang, "sync_adopted", n=len(adopted), lines=lines))


_ACTIVE_STATES = {"RUNNING", "PRINTING", "PREPARE", "PAUSE", "PAUSED", "SLICING"}


async def _progress(group_id):
    """Current print on the printer (any source), or idle — with a live cam shot."""
    # Status + camera frame overlap; the snapshot is best-effort (None if no cam).
    lang = _lang(group_id)
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
            group_id, i18n.t(lang, "progress_idle", state=state.lower()), attachments=attachments
        )
        return
    parts = [f'🖨️ „{name}" — {state}']
    if isinstance(prog, (int, float)):
        parts.append(f"{round(prog)}%")
    if ln and tl:
        parts.append(f"Layer {ln}/{tl}")
    if rem and rem > 0:
        r = int(rem)
        h, m = divmod(r, 60)
        dur = f"{h}:{m:02d} h" if h else f"{m} min"
        clock = (datetime.datetime.now() + datetime.timedelta(minutes=r)).strftime("%H:%M")
        parts.append(i18n.t(lang, "progress_remaining", dur=dur))
        parts.append(i18n.t(lang, "progress_done_at", clock=clock))
    await signal_client.send_to_group(group_id, " · ".join(parts), attachments=attachments)


async def _go(group_id):
    """Confirm the build plate is clear so Bambuddy releases the next queued print
    (Bambuddy is set to wait for manual plate-clear confirmation between jobs)."""
    lang = _lang(group_id)
    try:
        await bambuddy.clear_plate(config.PRINTER_ID)
    except Exception:
        log.exception("clear-plate failed")
        await signal_client.send_to_group(group_id, i18n.t(lang, "go_failed"))
        return
    await signal_client.send_to_group(group_id, i18n.t(lang, "go_ok"))


async def poll_completions(interval=60):
    """Watch bot-queued jobs and message the group when each finishes/fails.
    Prints started via other channels aren't tracked here, so they're skipped."""
    while True:
        try:
            await _check_completions()
        except Exception:
            log.exception("completion poll failed")
        await asyncio.sleep(interval)


def _eta_phrase(remaining_min, lang="de"):
    """' Fertig ca. 15:26 Uhr (in ~2 h 15 min).' from the printer's remaining
    minutes, or '' if it isn't known yet. Clock time is the container's local
    time (CEST), which matches the printer's locale."""
    if not remaining_min or remaining_min <= 0:
        return ""
    rem = int(remaining_min)
    h, m = divmod(rem, 60)
    dur = f"{h} h {m} min" if h else f"{m} min"
    clock = (datetime.datetime.now() + datetime.timedelta(minutes=rem)).strftime("%H:%M")
    return i18n.t(lang, "eta_phrase", clock=clock, dur=dur)


async def _check_completions():
    # Bambuddy flips a queue item to 'printing' the moment it *dispatches* it —
    # optimistically, before the machine confirms. If the printer can't actually
    # start (e.g. an HMS error), the item still reads 'printing'. So we only
    # announce a start when the printer itself reports an active state.
    try:
        pstatus = await bambuddy.printer_status(config.PRINTER_ID)
    except Exception:
        log.warning("printer status fetch failed in completion poll", exc_info=True)
        pstatus = None
    printer_active = bool(pstatus) and (pstatus.get("state") or "").upper() in _ACTIVE_STATES
    for job in store.queued_jobs_with_item():
        item = await bambuddy.get_queue_item(job["queue_item_id"])
        if item is None:
            # Item aged out of Bambuddy — stop tracking, don't poll a 404 forever.
            store.set_stage(job["id"], "done")
            continue
        lang = _lang(job["group_id"])
        status = item.get("status")
        if status == "printing" and job["stage"] != "printing":
            # pending → printing, but only believe it once the machine is really
            # running — otherwise re-check next poll (no premature/false start).
            if not printer_active:
                continue
            await signal_client.send_to_group(
                job["group_id"],
                i18n.t(lang, "completion_started", name=job["model_name"],
                       eta=_eta_phrase(pstatus.get("remaining_time"), lang)),
            )
            store.set_stage(job["id"], "printing")
        elif status == "completed":
            tail = (i18n.t(lang, "completion_done_eject")
                    if store.get_flag(EJECT_FLAG, False)
                    else i18n.t(lang, "completion_done_go"))
            await signal_client.send_to_group(
                job["group_id"],
                i18n.t(lang, "completion_done", name=job["model_name"], tail=tail),
            )
            store.set_stage(job["id"], "done")
        elif status == "failed":
            detail = (f": {item.get('error_message')}" if item.get("error_message") else ".")
            await signal_client.send_to_group(
                job["group_id"],
                i18n.t(lang, "completion_failed", name=job["model_name"], detail=detail),
            )
            store.set_stage(job["id"], "failed")
        elif status == "cancelled":
            store.set_stage(job["id"], "cancelled")
        # pending / printing / missing → keep watching
