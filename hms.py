"""Translate the printer's error numbers into sentences a human can act on.

Bambuddy hands us ``hms_errors`` entries that carry **no description at all**
(schema: ``code``/``attr``/``module``/``severity``/``actions``/``full_code``) —
so the bot used to relay something like „⚠️ Fehler: 0x4003", which tells nobody
anything. The sentence behind a code lives in Bambu's public catalogue (the one
Bambu Studio queries); we bundle it under ``hms_data/`` and look it up offline,
see ``scripts/refresh_hms_codes.py``.

Two code namespaces, both delivered in the same ``hms_errors`` list:

* **HMS** — 16 hex chars, ``f"{attr:08X}{code:08X}"``, printed as
  ``0700-8004-0002-0001``. Encodes the module *and* which AMS/slot is meant, so
  the catalogue text names the actual slot.
* **print_error** — 8 hex chars, printed as ``0500-8061``. Same list, shorter
  key, its own catalogue table.

Everything here is defensive on purpose: this runs inside the completion poller,
where one exception kills that cycle for *every* tracker, not just the entry
that was malformed.
"""
import functools
import gzip
import json
import logging
import os
import re

import i18n

log = logging.getLogger("hms")

_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hms_data")
_HEX = re.compile(r"[^0-9A-Fa-f]")

# Severity as Bambuddy reports it ((attr >> 8) & 0xF). Bambu's own scale:
# 1 fatal, 2 serious, 3 common, 4 info.
_SEVERITY_EMOJI = {1: "🛑", 2: "⚠️", 3: "⚠️", 4: "ℹ️"}
# Fallback when severity isn't reported: the error code's top nibble carries it
# (0x4xxx fatal, 0x8xxx serious/warning, 0xCxxx prompt) — see Bambuddy's parser.
_NIBBLE_SEVERITY = {"4": 1, "8": 2, "C": 4}


@functools.lru_cache(maxsize=4)
def _table(lang):
    """{'hms': {code: text}, 'err': {code: text}} for a language, or empty dicts.

    Cached: the file is ~4500 entries and gets hit once per poll per error."""
    path = os.path.join(_DATA, f"hms_{i18n.normalize(lang)}.json.gz")
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            data = json.load(fh)
        return {"hms": data.get("hms") or {}, "err": data.get("err") or {}}
    except Exception:
        log.warning("HMS catalogue %s unavailable — codes stay unexplained", path,
                    exc_info=True)
        return {"hms": {}, "err": {}}


def _hexkey(value):
    """'0x4003' / '0500-4003' / 4003 → bare uppercase hex, or '' if nothing usable."""
    if isinstance(value, int):
        return f"{value:X}"
    text = str(value or "").strip()
    if text[:2].lower() == "0x":
        text = text[2:]
    text = _HEX.sub("", text)
    return text.upper()


def _fields(entry):
    """(full_code, severity, actions) from one ``hms_errors`` element.

    Accepts every shape seen in the wild: Bambuddy's dict, a dict carrying only
    the legacy ``code``/``hms_code``, or a bare string. ``full_code`` is the
    catalogue key — reconstructed from ``attr`` + ``code`` when Bambuddy omits
    it, since ``code`` alone is just the low 32 bits."""
    if not isinstance(entry, dict):
        return _hexkey(entry), None, []
    full = _hexkey(entry.get("full_code"))
    code, attr = entry.get("code"), entry.get("attr")
    if len(full) not in (8, 16):
        if isinstance(attr, int) and code is not None and _hexkey(code):
            full = f"{attr:08X}{int(_hexkey(code), 16):08X}"
        else:
            full = _hexkey(code or entry.get("hms_code") or entry.get("attr"))
    severity = entry.get("severity")
    actions = entry.get("actions")
    return full, (severity if isinstance(severity, int) else None), \
        [str(a) for a in actions] if isinstance(actions, list) else []


def _display(full_code):
    """'07008004' → '0700-8004'; 16-char codes get the four-group form."""
    if len(full_code) in (8, 16):
        return "-".join(full_code[i:i + 4] for i in range(0, len(full_code), 4))
    return full_code or "?"


def _text(full_code, lang):
    """The catalogue sentence for a code, or '' when it isn't listed."""
    table = _table(lang)
    if len(full_code) == 16:
        return table["hms"].get(full_code, "")
    if len(full_code) == 8:
        # print_error lives in the short table; a few short codes only exist on
        # the HMS side, so try both before giving up.
        return table["err"].get(full_code) or table["hms"].get(full_code, "")
    return ""


def _severity(full_code, reported):
    if reported in _SEVERITY_EMOJI:
        return reported
    # The error half of the code (low 16 bits) starts with the severity nibble.
    nibble = full_code[8:9] if len(full_code) == 16 else full_code[4:5]
    return _NIBBLE_SEVERITY.get(nibble.upper(), 2)


def describe(entry, lang="de"):
    """One printable line: '⚠️ 0700-8004 — AMS A Slot 1: Filament ist alle …'.

    Falls back to a bare code line when the catalogue doesn't know it — the code
    is still worth showing, it's what the Bambu wiki and support ask for."""
    lang = i18n.normalize(lang)
    try:
        full, reported, _ = _fields(entry)
        # An explicit description (older Bambuddy builds, tests) wins over the
        # catalogue: it's what this specific installation decided to say.
        text = ""
        if isinstance(entry, dict):
            text = str(entry.get("description") or entry.get("desc")
                       or entry.get("message") or "").strip()
        text = text or _text(full, lang)
        emoji = _SEVERITY_EMOJI.get(_severity(full, reported), "⚠️")
        code = _display(full)
        if not text:
            return f"{emoji} {code} — {i18n.t(lang, 'hms_unknown')}"
        return f"{emoji} {code} — {text}"
    except Exception:
        log.warning("could not describe HMS entry %r", entry, exc_info=True)
        return f"⚠️ {i18n.t(lang, 'hms_unknown')}"


def entries(pstatus):
    """The raw ``hms_errors`` list, tolerating any junk in that field."""
    try:
        raw = (pstatus or {}).get("hms_errors")
    except Exception:
        return []
    return list(raw) if isinstance(raw, (list, tuple)) else []


def codes(pstatus):
    """Sorted canonical codes — the identity used to decide 'is this the same
    incident as last poll?'. Empty when the printer is healthy."""
    try:
        found = {_fields(e)[0] for e in entries(pstatus)}
        return sorted(c for c in found if c)
    except Exception:
        log.warning("could not read hms_errors", exc_info=True)
        return []


def detail(pstatus, lang="de", limit=3):
    """':\\n<line>\\n<line>' for the ``{detail}`` slot of the alert templates, or
    '' when nothing is wrong. Capped so a cascade of follow-up codes doesn't
    bury the message."""
    found = entries(pstatus)
    if not found:
        return ""
    lines = [describe(e, lang) for e in found[:limit]]
    if len(found) > limit:
        lines.append(i18n.t(i18n.normalize(lang), "hms_more", n=len(found) - limit))
    return ":\n" + "\n".join(lines)
