"""Pure parsing/classification of an incoming Signal envelope. No I/O."""
import base64
import re

_MW_FULL = re.compile(r"https?://(?:[\w.-]+\.)?makerworld\.com/\S+", re.I)
_MW_BARE = re.compile(r"(?:^|\s)(makerworld\.com/\S+)", re.I)
_NUMBERED = re.compile(r"^\s*\d+(?:[\s,]+\d+)*\s*$")


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


def classify(envelope):
    env = envelope or {}
    sender = env.get("sourceNumber") or env.get("source") or env.get("sourceUuid") or ""
    dm = env.get("dataMessage") or {}
    message = (dm.get("message") or "").strip()
    group_info = dm.get("groupInfo")
    raw_group = (group_info.get("groupId") or group_info.get("id") or "") if group_info else ""
    internal, send_id = normalize_group(raw_group)
    url = find_url(message)
    return {
        "sender": sender,
        "message": message,
        "is_dm": group_info is None,
        "group_internal_id": internal,
        "group_send_id": send_id,
        "url": url,
        "has_link": bool(url),
        "is_numbered": bool(_NUMBERED.match(message)),
    }
