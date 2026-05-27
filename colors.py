"""Color analysis: required colors from a resolved model, AMS snapshot from
printer status, the numbered question text, and parsing the user's reply into
an ``ams_mapping`` array (index = model filament, value = 0-based AMS tray id)."""
import re

# Curated German palette for naming a hex value. Covers tones emoji squares
# can't (Grau/Beige/Rosa/…), so the text stays readable even without the swatch.
_PALETTE = [
    ("Schwarz", "000000"), ("Dunkelgrau", "404040"), ("Grau", "808080"),
    ("Hellgrau", "C0C0C0"), ("Weiß", "FFFFFF"),
    ("Rot", "E0301E"), ("Orange", "F08000"), ("Gelb", "F0D000"),
    ("Grün", "20A020"), ("Dunkelgrün", "0A5A0A"), ("Türkis", "10B0A0"),
    ("Hellblau", "60B0E0"), ("Blau", "1050C0"), ("Dunkelblau", "0A1A6A"),
    ("Lila", "8030C0"), ("Rosa", "F060A0"), ("Pink", "E0108A"),
    ("Braun", "7A4A20"), ("Beige", "D8C8A8"), ("Gold", "C0A030"),
    ("Silber", "C8C8D0"),
]


def _rgb(hex6):
    h = (hex6 or "").lstrip("#")[:6]
    if len(h) != 6:
        return None
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return None


def color_name(hex6):
    """Nearest German color name for a hex value, or "" if unparseable."""
    rgb = _rgb(hex6)
    if rgb is None:
        return ""
    r, g, b = rgb
    best, bestd = "", None
    for name, ph in _PALETTE:
        pr, pg, pb = _rgb(ph)
        d = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
        if bestd is None or d < bestd:
            best, bestd = name, d
    return best


def _choose_index(resolved):
    top = resolved.get("instances") or []
    pid = resolved.get("profile_id")
    if pid:
        for i, inst in enumerate(top):
            if (inst.get("profileId") or inst.get("profile_id")) == pid:
                return i
    return 0


def chosen_profile_id(resolved):
    top = resolved.get("instances") or []
    idx = _choose_index(resolved)
    inst = top[idx] if idx < len(top) else {}
    return inst.get("profileId") or inst.get("profile_id") or resolved.get("profile_id")


def required_colors(resolved):
    """Filaments of the selected instance: [{index, type, color, name}]."""
    design = resolved.get("design") or {}
    top = resolved.get("instances") or []
    design_inst = design.get("instances") or []
    idx = _choose_index(resolved)
    chosen_top = top[idx] if idx < len(top) else {}
    d = next((x for x in design_inst if x.get("id") == chosen_top.get("id")), None)
    if d is None:
        d = design_inst[idx] if idx < len(design_inst) else {}
    out = []
    for i, f in enumerate(d.get("instanceFilaments") or []):
        out.append({
            "index": i,
            "type": f.get("type") or "",
            "color": (f.get("color") or "").lstrip("#").upper(),
            "name": f.get("name") or "",
        })
    return out


def model_name(resolved):
    design = resolved.get("design") or {}
    return design.get("title") or design.get("name") or "Modell"


def cover_url(resolved):
    """Thumbnail URL of the model, if the resolve response carries one."""
    design = resolved.get("design") or {}
    return design.get("coverUrl") or design.get("cover_url") or ""


def ams_snapshot(status):
    """First AMS unit's trays: [{slot, tray_id, type, color, sub}] (slot = tray_id+1)."""
    ams_units = status.get("ams") or []
    trays = (ams_units[0].get("tray") if ams_units else []) or []
    out = []
    for t in trays:
        tray_id = t.get("id") or 0
        out.append({
            "slot": tray_id + 1,
            "tray_id": tray_id,
            "type": t.get("tray_type") or "",
            "color": (t.get("tray_color") or "")[:6],
            "sub": t.get("tray_sub_brands") or "",
            "info_idx": t.get("tray_info_idx") or "",  # → real filament via /cloud/filament-id-map
        })
    return out


HELP_TEXT = (
    "🤖 Befehle (einfach hier reinschreiben):\n"
    "• MakerWorld-Link → neuer Druckauftrag\n"
    "• Zahlen, z.B. „3 1 2“ → Farben den AMS-Slots zuordnen\n"
    "• !liste → zeigt die Druck-Queue\n"
    "• !abbrechen → offene Farbfrage verwerfen, sonst letzten wartenden "
    "Queue-Job entfernen (laufende Drucke bleiben)\n"
    "• !help → diese Übersicht"
)

_HINT = "💡 !liste · !abbrechen · !help"


def build_question(name, required, ams):
    color_lines = "\n".join(
        f"Farbe {c['index'] + 1}: {c['type']} {color_name(c['color'])}".rstrip() for c in required
    )
    ams_lines = "\n".join(
        f"  {a['slot']}) {a['type']} {color_name(a['color'])}".rstrip()
        + (f" {a['sub']}" if a["sub"] else "")
        for a in ams
    )
    maxslot = len(ams) or 1
    example = " ".join(str((i % maxslot) + 1) for i in range(len(required)))
    return (
        f'🎨 "{name}" braucht {len(required)} Farbe(n):\n{color_lines}\n\n'
        f"AMS Slots:\n{ams_lines}\n\n"
        f"Antworte mit einem Slot pro Farbe (in Reihenfolge), z.B: {example}\n\n"
        f"{_HINT}"
    )


def parse_reply(message, required, ams):
    """Return (ok, ams_mapping|None, error_text|None).

    ams_mapping[i] = AMS tray id (0-based) chosen for model filament i.
    """
    nums = [int(x) for x in re.findall(r"\d+", message or "")]
    maxslot = len(ams) or 4
    if len(required) == 0 or len(nums) != len(required) or any(n < 1 or n > maxslot for n in nums):
        return False, None, (
            f"⚠️ Konnte das nicht zuordnen. Bitte {len(required)} Zahl(en) "
            f"zwischen 1 und {maxslot} schicken, eine pro Farbe."
        )
    return True, [n - 1 for n in nums], None
