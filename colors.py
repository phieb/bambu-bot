"""Color analysis: required colors from a resolved model, AMS snapshot from
printer status, the numbered question text, and parsing the user's reply into
an ``ams_mapping`` array (index = model filament, value = 0-based AMS tray id)."""
import re

import i18n

# Curated palette for naming a hex value. Covers tones emoji squares can't
# (Grau/Beige/Rosa/…), so the text stays readable even without the swatch.
# Each anchor carries the hex it anchors plus its name per language — the
# language key is explicit (no positional de/en slots), so adding a language is
# just another key.
_PALETTE = [
    {"hex": "000000", "de": "Schwarz",    "en": "Black"},
    {"hex": "404040", "de": "Dunkelgrau", "en": "Dark Gray"},
    {"hex": "808080", "de": "Grau",       "en": "Gray"},
    {"hex": "C0C0C0", "de": "Hellgrau",   "en": "Light Gray"},
    {"hex": "FFFFFF", "de": "Weiß",       "en": "White"},
    {"hex": "E0301E", "de": "Rot",        "en": "Red"},
    {"hex": "F08000", "de": "Orange",     "en": "Orange"},
    {"hex": "F0D000", "de": "Gelb",       "en": "Yellow"},
    {"hex": "20A020", "de": "Grün",       "en": "Green"},
    {"hex": "0A5A0A", "de": "Dunkelgrün", "en": "Dark Green"},
    {"hex": "10B0A0", "de": "Türkis",     "en": "Teal"},
    {"hex": "60B0E0", "de": "Hellblau",   "en": "Light Blue"},
    {"hex": "1050C0", "de": "Blau",       "en": "Blue"},
    {"hex": "0A1A6A", "de": "Dunkelblau", "en": "Dark Blue"},
    {"hex": "8030C0", "de": "Lila",       "en": "Purple"},
    {"hex": "AE96D4", "de": "Flieder",    "en": "Lilac"},
    {"hex": "F060A0", "de": "Rosa",       "en": "Pink"},
    {"hex": "E0108A", "de": "Pink",       "en": "Magenta"},
    {"hex": "7A4A20", "de": "Braun",      "en": "Brown"},
    {"hex": "D8C8A8", "de": "Beige",      "en": "Beige"},
    {"hex": "C0A030", "de": "Gold",       "en": "Gold"},
    {"hex": "C8C8D0", "de": "Silber",     "en": "Silver"},
]


def _rgb(hex6):
    h = (hex6 or "").lstrip("#")[:6]
    if len(h) != 6:
        return None
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return None


def color_name(hex6, lang="de"):
    """Nearest color name for a hex value in ``lang`` (default German), or "" if
    unparseable."""
    rgb = _rgb(hex6)
    if rgb is None:
        return ""
    r, g, b = rgb
    key = i18n.normalize(lang)  # 'de' | 'en' — the explicit language key
    best, bestd = "", None
    for entry in _PALETTE:
        pr, pg, pb = _rgb(entry["hex"])
        d = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
        if bestd is None or d < bestd:
            best, bestd = entry[key], d
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


def model_name(resolved, lang="de"):
    design = resolved.get("design") or {}
    fallback = "Model" if i18n.normalize(lang) == "en" else "Modell"
    return design.get("title") or design.get("name") or fallback


def cover_url(resolved):
    """Thumbnail URL of the model, if the resolve response carries one."""
    design = resolved.get("design") or {}
    return design.get("coverUrl") or design.get("cover_url") or ""


def _compat_names(inst):
    """Every printer model a profile lists as compatible (primary first)."""
    names = []
    c = inst.get("compatibility") or {}
    if c.get("devProductName"):
        names.append(c["devProductName"])
    for o in inst.get("otherCompatibility") or []:
        if o.get("devProductName"):
            names.append(o["devProductName"])
    return names


def profiles_list(resolved, target=""):
    """MakerWorld print profiles of a model: [{index, profile_id, title, printer,
    is_target, author, score}]. ``is_target`` flags profiles made for our printer
    (e.g. P1S), used to mark them and count them in the question."""
    out = []
    for i, inst in enumerate(resolved.get("instances") or []):
        names = _compat_names(inst)
        creator = inst.get("creator") or {}
        out.append({
            "index": i,
            "profile_id": inst.get("profileId") or inst.get("profile_id"),
            "title": inst.get("title") or inst.get("name") or "Profil",
            "printer": names[0] if names else "?",
            "is_target": bool(target) and any(target.lower() == n.lower() for n in names),
            "author": creator.get("name") or "",
            "score": inst.get("score") or 0,
        })
    return out


def build_profile_question(name, profiles, target="P1S", lang="de"):
    en = i18n.normalize(lang) == "en"
    n_target = sum(1 for p in profiles if p["is_target"])
    if en:
        head = f'🧩 „{name}" has {len(profiles)} profiles'
        if n_target:
            head += f" ({n_target} of them for your {target})"
        by = "by"
        prompt = "\n\nWhich profile do you want to print? Reply with the number."
    else:
        head = f'🧩 „{name}" hat {len(profiles)} Profile'
        if n_target:
            head += f" ({n_target} davon für deinen {target})"
        by = "von"
        prompt = "\n\nWelches Profil willst du drucken? Antworte mit der Zahl."
    lines = []
    for i, p in enumerate(profiles, 1):
        mark = "✅" if p["is_target"] else "▫️"
        extra = []
        if p["author"]:
            extra.append(f"{by} {p['author']}")
        if p["score"]:
            extra.append(f"★{p['score']}")
        tail = (" · " + " · ".join(extra)) if extra else ""
        lines.append(f'{i}. {mark} {p["printer"]} · „{p["title"]}"{tail}')
    return f"{head}:\n" + "\n".join(lines) + prompt


def plate_required(plate):
    """A plate's filaments as required colors: [{index, type, color, name}]."""
    out = []
    for i, f in enumerate(plate.get("filaments") or []):
        out.append({
            "index": i,
            "type": f.get("type") or "",
            "color": (f.get("color") or "").lstrip("#").upper()[:6],
            "name": f.get("name") or "",
        })
    return out


def _fmt_minutes(seconds):
    m = round((seconds or 0) / 60)
    return f"{m} min" if m < 60 else f"{m // 60}h{m % 60:02d}"


def build_plate_question(name, plates, lang="de"):
    en = i18n.normalize(lang) == "en"
    lines = []
    for i, p in enumerate(plates, 1):
        cols = ", ".join(
            f"{f.get('type') or ''} {color_name(f.get('color'), lang)}".strip()
            for f in (p.get("filaments") or [])
        )
        meta = []
        if p.get("print_time_seconds"):
            meta.append(_fmt_minutes(p["print_time_seconds"]))
        if p.get("filament_used_grams"):
            meta.append(f"{round(p['filament_used_grams'])} g")
        tail = (" · " + " · ".join(meta)) if meta else ""
        pname = p.get("name") or f"Plate {p.get('index')}"
        lines.append(f'{i}. „{pname}" — {cols or "?"}{tail}')
    if en:
        head = f'🗂️ „{name}" has {len(plates)} plates:\n'
        prompt = '\n\nWhich do you want to print? One or more numbers, e.g. „1“ or „1 3“.'
    else:
        head = f'🗂️ „{name}" hat {len(plates)} Plates:\n'
        prompt = "\n\nWelche willst du drucken? Eine oder mehrere Zahlen, z.B. „1“ oder „1 3“."
    return head + "\n".join(lines) + prompt


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


_HELP_TEXT = {
    "de": (
        "🤖 Befehle (einfach hier reinschreiben):\n"
        "• MakerWorld-Link → neuer Druckauftrag\n"
        "• Datei-Anhang oder Direkt-Link (.3mf/.gcode/.stl/.zip) → direkt drucken\n"
        "• Zahlen, z.B. „3 1 2“ → Farben den AMS-Slots zuordnen\n"
        "• !progress → aktueller Druck (%, Layer, Restzeit) + Live-Foto\n"
        "• !liste → zeigt die Druck-Queue\n"
        "• !sync → übernimmt Jobs, die nicht über mich liefen (z.B. Studio), für Fertig-Meldungen\n"
        "• !abo all / !abo 2 3 → Start- & Fertig-Meldungen für Drucke abonnieren (Nummern aus !liste); "
        "!deabo bestellt ab\n"
        "• !go → Platte ist frei, nächsten Druck starten\n"
        "• !eject on/off → Auto-Auswurf (Farmloop) ein/aus; „!eject“ zeigt den Status\n"
        "• !platte cool/textured/smooth/… → Druckplatte setzen (für Bett-Temp); „!platte“ zeigt sie\n"
        "• !skip → aktuelles Plate überspringen (z.B. Farbe fehlt), Rest bleibt\n"
        "• !abbrechen → restliche Plates verwerfen (schon konfigurierte werden "
        "eingereiht); ohne offenen Dialog: letzten wartenden Queue-Job entfernen\n"
        "• !english → auf Englisch umstellen\n"
        "• !help → diese Übersicht\n"
        "Wenn dein Druck fertig ist, meld ich mich automatisch."
    ),
    "en": (
        "🤖 Commands (just type them here):\n"
        "• MakerWorld link → new print job\n"
        "• File attachment or direct link (.3mf/.gcode/.stl/.zip) → print directly\n"
        "• Numbers, e.g. „3 1 2“ → map colors to AMS slots\n"
        "• !progress → current print (%, layer, time left) + live photo\n"
        "• !list → shows the print queue\n"
        "• !sync → adopts jobs not sent through me (e.g. Studio) for done notifications\n"
        "• !abo all / !abo 2 3 → subscribe to start & done notifications for prints (numbers from !list); "
        "!deabo unsubscribes\n"
        "• !go → plate is clear, start the next print\n"
        "• !eject on/off → auto-eject (Farmloop) on/off; „!eject“ shows the status\n"
        "• !plate cool/textured/smooth/… → set the build plate (for bed temp); „!plate“ shows it\n"
        "• !skip → skip the current plate (e.g. color missing), keep the rest\n"
        "• !cancel → discard the remaining plates (already-configured ones get "
        "queued); with no open dialog: remove the last waiting queue job\n"
        "• !deutsch → switch to German\n"
        "• !help → this overview\n"
        "When your print is done, I'll message you automatically."
    ),
}

_HINT = {
    "de": "💡 mehrere Plates? !skip überspringt eins · !abbrechen reiht die fertigen ein · !liste · !go",
    "en": "💡 multiple plates? !skip skips one · !cancel queues the finished ones · !list · !go",
}

_OTHER_MODEL_TEXT = {
    "de": (
        "🔗 Das sieht nach einem 3D-Modell-Link aus — Links automatisch auflösen kann "
        "ich aber nur bei **MakerWorld**.\n"
        "Für Printables/Cults3D/Thingiverse & Co.: lad die Datei runter und **schick "
        "sie mir direkt als Anhang** (.3mf, .gcode oder .stl) — die reihe ich dann ein."
    ),
    "en": (
        "🔗 That looks like a 3D-model link — but I can only auto-resolve links from "
        "**MakerWorld**.\n"
        "For Printables/Cults3D/Thingiverse & co.: download the file and **send it to "
        "me directly as an attachment** (.3mf, .gcode or .stl) — then I'll queue it."
    ),
}


def help_text(lang="de"):
    return _HELP_TEXT[i18n.normalize(lang)]


def hint(lang="de"):
    return _HINT[i18n.normalize(lang)]


def unknown_text(lang="de"):
    en = i18n.normalize(lang) == "en"
    head = ("🤔 I'm not sure what that means. Here's what I can do:\n\n" if en
            else "🤔 Da kenn ich mich nicht aus. Das kann ich:\n\n")
    return head + help_text(lang)


def other_model_text(lang="de"):
    return _OTHER_MODEL_TEXT[i18n.normalize(lang)]


def build_question(name, required, ams, lang="de"):
    en = i18n.normalize(lang) == "en"
    color_word = "Color" if en else "Farbe"
    color_lines = "\n".join(
        f"{color_word} {c['index'] + 1}: {c['type']} {color_name(c['color'], lang)}".rstrip()
        for c in required
    )
    ams_lines = "\n".join(
        f"  {a['slot']}) {a['type']} {color_name(a['color'], lang)}".rstrip()
        + (f" {a['sub']}" if a["sub"] else "")
        for a in ams
    )
    maxslot = len(ams) or 1
    example = " ".join(str((i % maxslot) + 1) for i in range(len(required)))
    if en:
        return (
            f'🎨 "{name}" needs {len(required)} color(s):\n{color_lines}\n\n'
            f"AMS slots:\n{ams_lines}\n\n"
            f"Reply with one slot per color (in order), e.g.: {example}\n\n"
            f"{hint(lang)}"
        )
    return (
        f'🎨 "{name}" braucht {len(required)} Farbe(n):\n{color_lines}\n\n'
        f"AMS Slots:\n{ams_lines}\n\n"
        f"Antworte mit einem Slot pro Farbe (in Reihenfolge), z.B: {example}\n\n"
        f"{hint(lang)}"
    )


def parse_reply(message, required, ams, lang="de"):
    """Return (ok, ams_mapping|None, error_text|None).

    ams_mapping[i] = AMS tray id (0-based) chosen for model filament i.
    """
    nums = [int(x) for x in re.findall(r"\d+", message or "")]
    maxslot = len(ams) or 4
    if len(required) == 0 or len(nums) != len(required) or any(n < 1 or n > maxslot for n in nums):
        if i18n.normalize(lang) == "en":
            err = (f"⚠️ Couldn't map that. Please send {len(required)} number(s) "
                   f"between 1 and {maxslot}, one per color.")
        else:
            err = (f"⚠️ Konnte das nicht zuordnen. Bitte {len(required)} Zahl(en) "
                   f"zwischen 1 und {maxslot} schicken, eine pro Farbe.")
        return False, None, err
    return True, [n - 1 for n in nums], None
