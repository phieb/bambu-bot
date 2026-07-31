"""Pick slicer presets (printer / process / filament) for re-slicing a
MakerWorld import for the *actual* target printer — MakerWorld profiles arrive
pre-sliced for whatever the author used (often X1C).

The presets API exposes only ``name`` (``filament_type`` / ``compatible_printers``
come back null), so all matching is name-based: best-effort, with a generic
Bambu preset as fallback. Pure functions over the ``/slicer/presets`` payload."""

# Generic Bambu preset per AMS filament type, used when a slot has no sub-brand
# (or nothing more specific matches).
_TYPE_GENERIC = {"PLA": "Bambu PLA Basic", "PETG": "Bambu PETG HF"}


def _all(presets, kind):
    """Flatten cloud+standard+local presets of one kind into [{source,id,name}]."""
    out = []
    for source in ("cloud", "standard", "local"):
        for p in (presets.get(source) or {}).get(kind) or []:
            if p.get("id"):
                out.append({"source": p.get("source") or source, "id": p["id"], "name": p.get("name") or ""})
    return out


def _ref(p):
    return {"source": p["source"], "id": p["id"]} if p else None


def preset_name(presets, kind, preset_id):
    """Display name of a preset id, or "" — the ref we pass to the slicer carries
    only source+id, but the *name* is what makes a slot line readable."""
    return next((p["name"] for p in _all(presets, kind) if p["id"] == preset_id), "")


def is_system(p):
    """True for a Bambu *system* preset (``GF…``) as opposed to a personal one
    (``PFUS…``, synced from the user's own spools like SUNLU Meta or eSUN).

    Personal presets used to be filtered out entirely: they once made the slicer
    fail with "input preset file is invalid and can not be parsed". That is no
    longer true (verified 2026-07-31 against library file 144 — a slice with
    ``PFUS513d393118294f`` "SUNLU PLA Meta @Bambu Lab P1S 0.4 nozzle" completed and
    produced ``filament_vendor = SUNLU``, flow 0.931, 210 °C instead of Bambu PLA
    Basic's 0.98/220 °C). Blanket-skipping them meant every SUNLU spool was
    silently sliced as generic Bambu PLA, so they are used first now and this
    predicate only marks the *retry* pool for when a personal preset still fails."""
    return not (p.get("id") or "").upper().startswith("PFUS")


def printer_preset(presets, model, nozzle):
    """The ``Bambu Lab <model> <nozzle> nozzle`` printer preset."""
    want = f"Bambu Lab {model} {nozzle} nozzle"
    cands = _all(presets, "printer")
    exact = next((p for p in cands if p["name"] == want), None)
    if exact:
        return _ref(exact)
    loose = next((p for p in cands if model in p["name"] and f"{nozzle} nozzle" in p["name"]), None)
    return _ref(loose)


# Bambu ships **no** P1S-named process presets — the P1S runs on the P1P family,
# and only user-made presets ever carry 'P1S' in the name. So look for the model's
# own name first (a personal preset the user tuned wins) and fall back to the
# family Bambu actually publishes for it.
_PROCESS_FAMILY = {"P1S": ("P1S", "P1P")}

# The nozzle Bambu leaves implicit in preset names: '0.20mm Standard @BBL P1P' is
# the 0.4 profile, every other size is spelled out ('… P1P 0.2 nozzle').
_IMPLICIT_NOZZLE = "0.4"


def _nozzle_ok(name, nozzle):
    if f"{nozzle} nozzle" in name:
        return True
    return nozzle == _IMPLICIT_NOZZLE and "nozzle" not in name


def process_preset(presets, model, nozzle=_IMPLICIT_NOZZLE):
    """A process preset for the model **and the fitted nozzle**.

    Layer height is bounded by nozzle diameter, so a 0.20mm profile is simply not
    printable through a 0.2 nozzle — the process preset has to follow the physical
    nozzle rather than a config default. Within the matching family prefer the
    user's own presets (they carry the model name), then 'Standard', then
    'Optimal', then the finest layer height, so each nozzle gets its sane default
    (0.4 → 0.20mm Standard, 0.2 → 0.10mm Standard, 0.6 → 0.30mm Standard)."""
    pool = [p for p in _all(presets, "process") if _nozzle_ok(p["name"], nozzle)]
    for family in _PROCESS_FAMILY.get(model, (model,)):
        cands = [p for p in pool if family in p["name"]]
        if not cands:
            continue
        cands.sort(key=lambda p: ("Standard" not in p["name"], "Optimal" not in p["name"],
                                  p["name"]))
        return _ref(cands[0])
    return None


def filament_preset(presets, model, nozzle, tray_type, sub_brand, filament_name="",
                    system_only=False):
    """Best filament preset for an AMS slot, matched by name against the target
    printer. ``filament_name`` (resolved from the AMS tag via filament-id-map,
    e.g. 'SUNLU PLA Meta') is the strongest signal — it pins the *actual* spool,
    including non-Bambu ones. Falls back to sub-brand, then generic Bambu.
    ``system_only`` restricts the pool to Bambu system presets: the safe retry
    after a slice failed with the user's personal preset (see :func:`is_system`)."""
    noz = f"{nozzle} nozzle"
    pool = _all(presets, "filament")
    if system_only:
        pool = [p for p in pool if is_system(p)]
    cands = [p for p in pool
             if model in p["name"] and noz in p["name"] and tray_type and tray_type in p["name"]]
    if not cands:  # last resort: ignore type, just the right printer/nozzle
        cands = [p for p in pool if model in p["name"] and noz in p["name"]]
    if not cands:
        return None

    generic = _TYPE_GENERIC.get(tray_type)

    def score(p):
        n = p["name"]
        s = 0
        if filament_name and filament_name in n:
            s += 1000
        if sub_brand and sub_brand in n:
            s += 100
        if generic and generic in n:
            s += 20
        if n.startswith("Bambu "):
            s += 5
        return (-s, len(n), n)  # best score first, then shortest / alphabetical

    cands.sort(key=score)
    return _ref(cands[0])
