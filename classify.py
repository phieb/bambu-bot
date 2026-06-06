"""Pure parsing/classification of an incoming Signal envelope. No I/O."""
import base64
import re

_MW_FULL = re.compile(r"https?://(?:[\w.-]+\.)?makerworld\.com/\S+", re.I)
_MW_BARE = re.compile(r"(?:^|\s)(makerworld\.com/\S+)", re.I)
# Other 3D-model sources Bambuddy can't resolve from a URL. We recognize them
# only to reply helpfully instead of silently ignoring the link. Keep this list
# in sync with the dispatcher's hasLink regex so such links reach this bot.
_OTHER_MODEL_HOSTS = (
    "printables.com", "thingiverse.com", "cults3d.com",
    "myminifactory.com", "thangs.com",
)
_OTHER_MODEL = re.compile(
    r"(?:https?://)?(?:[\w.-]+\.)?(?:"
    + "|".join(h.replace(".", r"\.") for h in _OTHER_MODEL_HOSTS)
    + r")/\S+",
    re.I,
)
_NUMBERED = re.compile(r"^\s*\d+(?:[\s,]+\d+)*\s*$")
# Commands are prefixed with "!" so they never collide with color replies
# ("3 1 2") or chatter. MakerWorld links and numbered replies stay prefix-free.
_CANCEL = re.compile(r"^\s*!\s*(abbrechen|abbruch|abbrich|cancel|verwerfen|stopp?)\s*$", re.I)
_LIST = re.compile(r"^\s*!\s*(liste?|queue|warteschlange)\s*$", re.I)
_PROGRESS = re.compile(r"^\s*!\s*(progress|fortschritt|status|druck)\s*$", re.I)
_HELP = re.compile(r"^\s*!\s*(hilfe|help|befehle|commands|\?)\s*$", re.I)
# Acknowledge the build plate is clear so Bambuddy starts the next queued print.
_GO = re.compile(r"^\s*!\s*(go|los|weiter|frei|clear)\s*$", re.I)
# Skip the current plate's color question, keep the already-configured ones.
_SKIP = re.compile(r"^\s*!\s*(skip|überspringen|ueberspringen|weglassen|auslassen)\s*$", re.I)
# Toggle Farmloop auto-eject; optional arg on|off (no arg → show status).
_EJECT = re.compile(r"^\s*!\s*(eject|auswurf|auswerfen)(?:\s+(on|an|ein|off|aus|status))?\s*$", re.I)
# Adopt queue jobs not sent through the bot (Studio Send / VP / web UI) so they
# also get finished/failed notifications.
_SYNC = re.compile(r"^\s*!\s*(sync|synchronisieren|scan|übernehmen|uebernehmen)\s*$", re.I)
# Switch the group's reply language (default stays German). Either a direct
# shortcut (!english / !deutsch) or !lang/!sprache with an optional argument
# (no argument → show the current language).
_LANG = re.compile(
    r"^\s*!\s*(lang|language|sprache|en|eng|english|englisch|de|deutsch|german|ger)"
    r"(?:\s+(en|eng|english|englisch|de|deutsch|german|ger))?\s*$",
    re.I,
)
_EN_WORDS = ("en", "eng", "english", "englisch")
_DE_WORDS = ("de", "deutsch", "german", "ger")


def lang_command(message):
    """('en'|'de'|'show') if the message is a language command, else None.
    'show' means !lang/!sprache without an argument (report the current one)."""
    m = _LANG.match(message or "")
    if not m:
        return None
    head, arg = m.group(1).lower(), (m.group(2) or "").lower()
    if head in _EN_WORDS:
        return "en"
    if head in _DE_WORDS:
        return "de"
    # head is lang/language/sprache → decide from the (optional) argument
    if arg in _EN_WORDS:
        return "en"
    if arg in _DE_WORDS:
        return "de"
    return "show"


def eject_command(message):
    """('on'|'off'|'status') if the message is an !eject command, else None."""
    m = _EJECT.match(message)
    if not m:
        return None
    arg = (m.group(2) or "").lower()
    if arg in ("on", "an", "ein"):
        return "on"
    if arg in ("off", "aus"):
        return "off"
    return "status"


# Set the build plate on the printer; optional arg (a short alias below) → its
# canonical slicer curr_bed_type. No arg → show the current plate.
_PLATE = re.compile(r"^\s*!\s*(?:platte|druckbett|bett|plate|bed)(?:\s+(.+?))?\s*$", re.I)
# Short names the user can type → canonical BambuStudio/OrcaSlicer plate name.
# Matched against the space-collapsed, lower-cased argument.
BED_ALIASES = {
    "cool": "Cool Plate", "cool plate": "Cool Plate", "kühl": "Cool Plate", "kuehl": "Cool Plate",
    "textured": "Textured PEI Plate", "textur": "Textured PEI Plate", "pei": "Textured PEI Plate",
    "textured pei": "Textured PEI Plate", "textured pei plate": "Textured PEI Plate",
    "smooth": "Smooth PEI Plate", "glatt": "Smooth PEI Plate",
    "smooth pei": "Smooth PEI Plate", "smooth pei plate": "Smooth PEI Plate",
    "engineering": "Engineering Plate", "eng": "Engineering Plate", "engineering plate": "Engineering Plate",
    "hot": "High Temp Plate", "high temp": "High Temp Plate", "hightemp": "High Temp Plate",
    "high temp plate": "High Temp Plate",
    "supertack": "Cool Plate (SuperTack)", "tack": "Cool Plate (SuperTack)",
    "cool plate (supertack)": "Cool Plate (SuperTack)",
}


def plate_command(message):
    """Parse a !platte command. None if it isn't one; else a dict:
    {'action':'status'} (no arg), {'action':'set','bed_type':<canonical>}, or
    {'action':'unknown','arg':<raw>} when the plate name isn't recognized."""
    m = _PLATE.match(message)
    if not m:
        return None
    arg = (m.group(1) or "").strip()
    if not arg:
        return {"action": "status"}
    bed = BED_ALIASES.get(" ".join(arg.lower().split()))
    return {"action": "set", "bed_type": bed} if bed else {"action": "unknown", "arg": arg}


def normalize_group(raw):
    """Return (internal_id, send_id) for either incoming form.

    Signal may deliver the group id as the raw base64 ``internal_id`` or as the
    ``group.<base64(internal_id)>`` form. We store/send the ``group.`` form.
    """
    if not raw:
        return "", ""
    if raw.startswith("group."):
        send_id = raw
        try:
            internal = base64.b64decode(raw[6:]).decode("utf-8")
        except Exception:
            internal = raw
    else:
        internal = raw
        send_id = "group." + base64.b64encode(raw.encode("utf-8")).decode("ascii")
    return internal, send_id


def find_url(message):
    m = _MW_FULL.search(message) or _MW_BARE.search(message)
    if not m:
        return ""
    url = m.group(0).strip()
    if not url.startswith("http"):
        url = "https://" + url
    return url


# Model file types we can take into Bambuddy. Longest suffix first so
# ".gcode.3mf" is classified as gcode (already sliced) rather than 3mf.
_MODEL_EXTS = (".gcode.3mf", ".gcode", ".3mf", ".stl", ".zip")
# A bare http(s) URL whose path ends in a model extension (query string ignored).
_FILE_URL = re.compile(
    r"https?://\S+?(?:" + "|".join(e.replace(".", r"\.") for e in _MODEL_EXTS) + r")(?=$|[?#\s])",
    re.I,
)


def file_kind(filename):
    """Map a filename/path to a kind ('gcode'|'3mf'|'stl'|'zip') or '' if not a
    model file. '.gcode.3mf' counts as gcode (already sliced)."""
    low = (filename or "").lower()
    ext = next((e for e in _MODEL_EXTS if low.endswith(e)), "")
    if not ext:
        return ""
    return "gcode" if ext.startswith(".gcode") else ext.lstrip(".")


_THINGIVERSE = re.compile(r"thingiverse\.com/(?:thing:)?(\d{3,})", re.I)


def thingiverse_id(message):
    """The Thingiverse thing id from a link, or '' (e.g. .../thing:763622)."""
    m = _THINGIVERSE.search(message or "")
    return m.group(1) if m else ""


def find_file_url(message):
    """First http(s) URL in the message that points directly at a model file,
    or '' (e.g. https://host/x/benchy.zip?dl=1)."""
    m = _FILE_URL.search(message or "")
    return m.group(0) if m else ""


def filename_from_url(url):
    """Last path segment of a URL (query/fragment stripped), e.g. 'benchy.zip'."""
    tail = re.split(r"[?#]", url, 1)[0].rstrip("/").rsplit("/", 1)[-1]
    return tail or "modell"


def model_files(dm):
    """Model-file attachments of a dataMessage as
    [{id, filename, kind}] where kind is 'gcode' | '3mf' | 'stl' | 'zip'. Non-
    model attachments (photos, …) are ignored so stray group media never
    triggers."""
    out = []
    for att in dm.get("attachments") or []:
        if not isinstance(att, dict):
            continue
        name = (att.get("filename") or "").strip()
        kind = file_kind(name)
        if not kind:
            continue
        att_id = att.get("id") or att.get("attachment") or att.get("contentType")
        if att_id:
            out.append({"id": att_id, "filename": name, "kind": kind})
    return out


def classify(envelope):
    env = envelope or {}
    sender = env.get("sourceNumber") or env.get("source") or env.get("sourceUuid") or ""
    dm = env.get("dataMessage") or {}
    message = (dm.get("message") or "").strip()
    group_info = dm.get("groupInfo")
    raw_group = (group_info.get("groupId") or group_info.get("id") or "") if group_info else ""
    internal, send_id = normalize_group(raw_group)
    url = find_url(message)
    file_url = find_file_url(message)
    files = model_files(dm)
    # An "other" model link only counts when it isn't a MakerWorld link nor a
    # direct file URL (those take a real flow); presence triggers the help reply.
    is_other_model = bool(_OTHER_MODEL.search(message)) and not url and not file_url
    return {
        "sender": sender,
        "message": message,
        "is_dm": group_info is None,
        "group_internal_id": internal,
        "group_send_id": send_id,
        "url": url,
        "has_link": bool(url),
        "file_url": file_url,
        "has_file_url": bool(file_url),
        "thingiverse_id": thingiverse_id(message),
        "model_files": files,
        "has_model_file": bool(files),
        "is_other_model": is_other_model,
        "is_numbered": bool(_NUMBERED.match(message)),
        "is_cancel": bool(_CANCEL.match(message)),
        "is_list": bool(_LIST.match(message)),
        "is_progress": bool(_PROGRESS.match(message)),
        "is_help": bool(_HELP.match(message)),
        "is_go": bool(_GO.match(message)),
        "is_skip": bool(_SKIP.match(message)),
        "eject_command": eject_command(message),
        "plate_command": plate_command(message),
        "is_sync": bool(_SYNC.match(message)),
        "lang_command": lang_command(message),
    }


def to_envelopes(payload):
    """Normalize any dispatcher payload into a list of inner Signal envelopes
    (each with ``dataMessage``/``source`` at the top level).

    Accepts a single envelope or a list, in any wrap shape: bare, ``{envelope:…}``,
    ``{body:{envelope:…}}``, ``{body:[…]}``, or a raw list. Mirrors the ttrpg-bot
    parser so both tools behind the shared dispatcher parse identically.
    """
    if isinstance(payload, dict) and isinstance(payload.get("body"), list):
        payload = payload["body"]
    items = payload if isinstance(payload, list) else [payload]
    envelopes = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if "envelope" in item:
            envelopes.append(item["envelope"] or {})
        elif isinstance(item.get("body"), dict) and "envelope" in item["body"]:
            envelopes.append(item["body"]["envelope"] or {})
        else:
            envelopes.append(item)
    return envelopes
