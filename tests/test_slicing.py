import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import slicing  # noqa: E402

PRESETS = {
    "cloud": {
        "printer": [
            {"id": "GM014", "source": "cloud", "name": "Bambu Lab P1S 0.4 nozzle"},
            {"id": "GM010", "source": "cloud", "name": "Bambu Lab X1 Carbon 0.4 nozzle"},
        ],
        "process": [
            {"id": "PR1", "source": "cloud", "name": "0.20mm Meta PLA fast @BBL P1S"},
            {"id": "PR2", "source": "cloud", "name": "P1S eSUN PETG Process"},
            {"id": "PRX", "source": "cloud", "name": "0.20mm Standard @BBL X1C"},
        ],
        "filament": [
            {"id": "GFSA00_34", "source": "cloud", "name": "Bambu PLA Basic @BBL P1S 0.4 nozzle"},
            {"id": "GFSA01_34", "source": "cloud", "name": "Bambu PLA Matte @BBL P1S 0.4 nozzle"},
            {"id": "GFSG02_36", "source": "cloud", "name": "Bambu PETG HF @BBL P1S 0.4 nozzle"},
            # Personal/custom presets (PFUS…) — unparseable by the slicer sidecar.
            {"id": "PFUS01copy", "source": "cloud", "name": "Bambu PLA Matte @BBL P1S 0.4 nozzle - Copy"},
            {"id": "PFUSesun", "source": "cloud", "name": "eSUN PETG Basic @Bambu Lab P1S 0.4 nozzle"},
            {"id": "PFUSsunlu", "source": "cloud", "name": "SUNLU PLA Meta @Bambu Lab P1S 0.4 nozzle"},
            {"id": "GFSA00_X1", "source": "cloud", "name": "Bambu PLA Basic @BBL X1C 0.4 nozzle"},
        ],
    },
    "standard": {}, "local": {},
}


def test_printer_preset_exact():
    assert slicing.printer_preset(PRESETS, "P1S", "0.4") == {"source": "cloud", "id": "GM014"}


def test_process_preset_prefers_020_for_model():
    assert slicing.process_preset(PRESETS, "P1S") == {"source": "cloud", "id": "PR1"}


def test_filament_generic_pla():
    # plain PLA slot, no sub-brand → generic Bambu PLA Basic, P1S (not X1C)
    assert slicing.filament_preset(PRESETS, "P1S", "0.4", "PLA", "") == {"source": "cloud", "id": "GFSA00_34"}


def test_filament_petg():
    assert slicing.filament_preset(PRESETS, "P1S", "0.4", "PETG", "") == {"source": "cloud", "id": "GFSG02_36"}


def test_filament_sub_brand_match():
    # sub-brand "PLA Matte" beats the generic basic; the custom "- Copy" (PFUS) is
    # excluded as unparseable, so the Bambu system Matte preset wins.
    assert slicing.filament_preset(PRESETS, "P1S", "0.4", "PLA", "PLA Matte") == {"source": "cloud", "id": "GFSA01_34"}


def test_custom_presets_excluded_fall_back_to_bambu():
    # A non-Bambu spool resolves by name to a PFUS… custom preset, which the
    # slicer can't parse — so we must fall back to the Bambu system preset.
    assert slicing.filament_preset(
        PRESETS, "P1S", "0.4", "PETG", "", "eSUN PETG Basic"
    ) == {"source": "cloud", "id": "GFSG02_36"}
    assert slicing.filament_preset(
        PRESETS, "P1S", "0.4", "PLA", "", "SUNLU PLA Meta"
    ) == {"source": "cloud", "id": "GFSA00_34"}
    # never returns a PFUS… preset
    for args in (("PLA", ""), ("PETG", ""), ("PLA", "PLA Matte")):
        ref = slicing.filament_preset(PRESETS, "P1S", "0.4", *args)
        assert not ref["id"].startswith("PFUS")


def test_filament_none_when_nothing_matches():
    assert slicing.filament_preset(PRESETS, "P1S", "0.4", "TPU", "") is not None  # falls back ignoring type
    empty = {"cloud": {"filament": []}, "standard": {}, "local": {}}
    assert slicing.filament_preset(empty, "P1S", "0.4", "PLA", "") is None
