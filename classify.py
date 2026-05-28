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


# Attachment file types we can take into Bambuddy. Longest suffix first so
# ".gcode.3mf" is classified as gcode (already sliced) rather than 3mf.
_MODEL_EXTS = (".gcode.3mf", ".gcode", ".3mf", ".stl", ".zip")


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
        low = name.lower()
        ext = next((e for e in _MODEL_EXTS if low.endswith(e)), "")
        if not ext:
            continue
        kind = "gcode" if ext.startswith(".gcode") else ext.lstrip(".")
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
    files = model_files(dm)
    # An "other" model link only counts when it isn't a MakerWorld link (those
    # take the full flow); presence is enough to trigger the helpful reply.
    is_other_model = bool(_OTHER_MODEL.search(message)) and not url
    return {
        "sender": sender,
        "message": message,
        "is_dm": group_info is None,
        "group_internal_id": internal,
        "group_send_id": send_id,
        "url": url,
        "has_link": bool(url),
        "model_files": files,
        "has_model_file": bool(files),
        "is_other_model": is_other_model,
        "is_numbered": bool(_NUMBERED.match(message)),
        "is_cancel": bool(_CANCEL.match(message)),
        "is_list": bool(_LIST.match(message)),
        "is_progress": bool(_PROGRESS.match(message)),
        "is_help": bool(_HELP.match(message)),
        "is_go": bool(_GO.match(message)),
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
