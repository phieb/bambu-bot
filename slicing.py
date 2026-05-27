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


def filament_preset(presets, model, nozzle, tray_type, sub_brand):
    """Best filament preset for an AMS slot (type + optional sub-brand), matched
    by name against the target printer; generic Bambu preset as fallback."""
    noz = f"{nozzle} nozzle"
    cands = [p for p in _all(presets, "filament")
             if model in p["name"] and noz in p["name"] and tray_type and tray_type in p["name"]]
    if not cands:  # last resort: ignore type, just the right printer/nozzle
        cands = [p for p in _all(presets, "filament") if model in p["name"] and noz in p["name"]]
    if not cands:
        return None

    generic = _TYPE_GENERIC.get(tray_type)

    def score(p):
        n = p["name"]
        s = 0
        if sub_brand and sub_brand in n:
            s += 100
        if generic and generic in n:
            s += 20
        if n.startswith("Bambu "):
            s += 5
        return (-s, len(n), n)  # best score first, then shortest / alphabetical

    cands.sort(key=score)
    return _ref(cands[0])
