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


def _sliceable(p):
    """Personal/custom filament presets (id ``PFUS…``, synced from non-Bambu
    spools like SUNLU/eSUN) make the slicer sidecar fail with "input preset file
    is invalid and can not be parsed" — only Bambu *system* presets (``GF…``)
    are parseable, so we skip the custom ones and fall back to a Bambu preset of
    the same type."""
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


def process_preset(presets, model):
    """A process preset for the model; prefer a 0.20mm one."""
    cands = [p for p in _all(presets, "process") if model in p["name"]]
    if not cands:
        return None
    cands.sort(key=lambda p: ("0.20mm" not in p["name"], p["name"]))
    return _ref(cands[0])


def filament_preset(presets, model, nozzle, tray_type, sub_brand, filament_name=""):
    """Best filament preset for an AMS slot, matched by name against the target
    printer. ``filament_name`` (resolved from the AMS tag via filament-id-map,
    e.g. 'eSUN PETG Basic') is the strongest signal — it pins the *actual* spool,
    including non-Bambu ones. Falls back to sub-brand, then generic Bambu."""
    noz = f"{nozzle} nozzle"
    pool = [p for p in _all(presets, "filament") if _sliceable(p)]
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
