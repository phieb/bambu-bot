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
            # Bambu's own P1S process profiles are published under the P1P family,
            # with every non-0.4 nozzle spelled out in the name.
            {"id": "PP04", "source": "cloud", "name": "0.20mm Standard @BBL P1P"},
            {"id": "PP02", "source": "cloud", "name": "0.10mm Standard @BBL P1P 0.2 nozzle"},
            {"id": "PP02f", "source": "cloud", "name": "0.06mm Fine @BBL P1P 0.2 nozzle"},
            {"id": "PP06", "source": "cloud", "name": "0.30mm Standard @BBL P1P 0.6 nozzle"},
        ],
        "filament": [
            {"id": "GFSA00_34", "source": "cloud", "name": "Bambu PLA Basic @BBL P1S 0.4 nozzle"},
            {"id": "GFSA01_34", "source": "cloud", "name": "Bambu PLA Matte @BBL P1S 0.4 nozzle"},
            {"id": "GFSG02_36", "source": "cloud", "name": "Bambu PETG HF @BBL P1S 0.4 nozzle"},
            # Personal/custom presets (PFUS…) — the user's own spool profiles.
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


def test_process_preset_prefers_the_users_own_p1s_preset():
    # A personal preset carries the model name; Bambu's system ones say P1P.
    assert slicing.process_preset(PRESETS, "P1S", "0.4") == {"source": "cloud", "id": "PR1"}


def test_process_preset_follows_the_fitted_nozzle():
    # Layer height is bounded by nozzle diameter, so the 0.4 profiles (named
    # without a nozzle suffix) must not be handed to a 0.2 nozzle. Bambu ships no
    # P1S-named process presets at all, so 0.2 falls through to the P1P family.
    assert slicing.process_preset(PRESETS, "P1S", "0.2") == {"source": "cloud", "id": "PP02"}
    assert slicing.process_preset(PRESETS, "P1S", "0.6") == {"source": "cloud", "id": "PP06"}


def test_process_preset_none_when_the_nozzle_has_no_profile():
    assert slicing.process_preset(PRESETS, "P1S", "0.8") is None


def test_filament_generic_pla():
    # plain PLA slot, no sub-brand → generic Bambu PLA Basic, P1S (not X1C)
    assert slicing.filament_preset(PRESETS, "P1S", "0.4", "PLA", "") == {"source": "cloud", "id": "GFSA00_34"}


def test_filament_petg():
    assert slicing.filament_preset(PRESETS, "P1S", "0.4", "PETG", "") == {"source": "cloud", "id": "GFSG02_36"}


def test_filament_sub_brand_match():
    # sub-brand "PLA Matte" beats the generic basic; the personal "- Copy" scores
    # the same but loses on name length, so the Bambu system Matte preset wins.
    assert slicing.filament_preset(PRESETS, "P1S", "0.4", "PLA", "PLA Matte") == {"source": "cloud", "id": "GFSA01_34"}


def test_personal_preset_wins_for_the_actual_spool():
    # The AMS tag resolves (via filament-id-map) to the user's own preset, which
    # carries that spool's real flow ratio and temperatures. It must be used —
    # blanket-skipping PFUS… presets silently sliced every SUNLU spool as generic
    # Bambu PLA (flow 0.98 / 220 °C instead of 0.931 / 210 °C).
    assert slicing.filament_preset(
        PRESETS, "P1S", "0.4", "PETG", "", "eSUN PETG Basic"
    ) == {"source": "cloud", "id": "PFUSesun"}
    assert slicing.filament_preset(
        PRESETS, "P1S", "0.4", "PLA", "", "SUNLU PLA Meta"
    ) == {"source": "cloud", "id": "PFUSsunlu"}


def test_system_only_is_the_retry_pool():
    # ``system_only`` is what the re-slice retries with if a personal preset fails.
    assert slicing.filament_preset(
        PRESETS, "P1S", "0.4", "PLA", "", "SUNLU PLA Meta", system_only=True
    ) == {"source": "cloud", "id": "GFSA00_34"}
    for args in (("PLA", ""), ("PETG", ""), ("PLA", "PLA Matte")):
        ref = slicing.filament_preset(PRESETS, "P1S", "0.4", *args, system_only=True)
        assert slicing.is_system({"id": ref["id"]})


def test_preset_name_resolves_the_display_name():
    assert slicing.preset_name(PRESETS, "filament", "PFUSsunlu") == \
        "SUNLU PLA Meta @Bambu Lab P1S 0.4 nozzle"
    assert slicing.preset_name(PRESETS, "filament", "nope") == ""


def test_filament_none_when_nothing_matches():
    assert slicing.filament_preset(PRESETS, "P1S", "0.4", "TPU", "") is not None  # falls back ignoring type
    empty = {"cloud": {"filament": []}, "standard": {}, "local": {}}
    assert slicing.filament_preset(empty, "P1S", "0.4", "PLA", "") is None
