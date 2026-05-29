import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import slicer  # noqa: E402


def test_filament_profile_picker():
    # SUNLU PLA Meta → the tuned profile
    assert slicer.filament_profile("PLA", "SUNLU PLA Meta").endswith("filament_sunlu_pla_meta.json")
    assert slicer.filament_profile("PLA", "sunlu meta").endswith("filament_sunlu_pla_meta.json")
    # generic PLA / PETG → the standard bundled profiles
    assert slicer.filament_profile("PLA", "PLA Basic").endswith("filament_pla_basic.json")
    assert slicer.filament_profile("PETG", "").endswith("filament_petg.json")
    # unknown → PLA default
    assert slicer.filament_profile(None, None).endswith("filament_pla_basic.json")


def test_bundled_profiles_exist_and_valid():
    import json
    for path in [slicer.PRINTER, *slicer._FILAMENTS.values()]:
        assert os.path.exists(path), path
        json.load(open(path))  # parses
