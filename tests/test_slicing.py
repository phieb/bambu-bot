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
            {"id": "F_PLA", "source": "cloud", "name": "Bambu PLA Basic @BBL P1S 0.4 nozzle"},
            {"id": "F_MATTE", "source": "cloud", "name": "Bambu PLA Matte @BBL P1S 0.4 nozzle"},
            {"id": "F_MATTE_COPY", "source": "cloud", "name": "Bambu PLA Matte @BBL P1S 0.4 nozzle - Copy"},
            {"id": "F_PETG", "source": "cloud", "name": "Bambu PETG HF @BBL P1S 0.4 nozzle"},
            {"id": "F_ESUN", "source": "cloud", "name": "eSUN PETG Basic @Bambu Lab P1S 0.4 nozzle"},
            {"id": "F_SUNLU", "source": "cloud", "name": "SUNLU PLA Meta @Bambu Lab P1S 0.4 nozzle"},
            {"id": "F_PLA_X1", "source": "cloud", "name": "Bambu PLA Basic @BBL X1C 0.4 nozzle"},
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
    assert slicing.filament_preset(PRESETS, "P1S", "0.4", "PLA", "") == {"source": "cloud", "id": "F_PLA"}


def test_filament_petg():
    assert slicing.filament_preset(PRESETS, "P1S", "0.4", "PETG", "") == {"source": "cloud", "id": "F_PETG"}


def test_filament_sub_brand_match():
    # sub-brand "PLA Matte" beats the generic basic; shortest name wins the tie
    assert slicing.filament_preset(PRESETS, "P1S", "0.4", "PLA", "PLA Matte") == {"source": "cloud", "id": "F_MATTE"}


def test_filament_real_name_beats_generic():
    # non-Bambu PETG: resolved name "eSUN PETG Basic" must win over generic Bambu PETG HF
    assert slicing.filament_preset(
        PRESETS, "P1S", "0.4", "PETG", "", "eSUN PETG Basic"
    ) == {"source": "cloud", "id": "F_ESUN"}
    # non-Bambu PLA with no sub-brand: resolved "SUNLU PLA Meta" beats Bambu PLA Basic
    assert slicing.filament_preset(
        PRESETS, "P1S", "0.4", "PLA", "", "SUNLU PLA Meta"
    ) == {"source": "cloud", "id": "F_SUNLU"}


def test_filament_none_when_nothing_matches():
    assert slicing.filament_preset(PRESETS, "P1S", "0.4", "TPU", "") is not None  # falls back ignoring type
    empty = {"cloud": {"filament": []}, "standard": {}, "local": {}}
    assert slicing.filament_preset(empty, "P1S", "0.4", "PLA", "") is None
