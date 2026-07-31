"""AMS slots must be described by the *assigned Spoolman spool*, not by guessing
a name from the tray's hex, and must name the slicer profile they'd be sliced
with. Real case (2026-07-31): four SUNLU Meta pastels read out as
Flieder/Grau/Beige/Hellblau while Spoolman knew them as Taro Purple / Apple Green
/ Lemon Yellow / Ice Blue."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import bambuddy  # noqa: E402
import colors  # noqa: E402
import config  # noqa: E402
import handlers  # noqa: E402
import i18n  # noqa: E402
import slicing  # noqa: E402

# Verbatim shapes from the live P1S / Bambuddy on 2026-07-31.
STATUS = {"ams": [{"tray": [
    {"id": 0, "tray_color": "A69ED0FF", "tray_type": "PLA", "tray_sub_brands": "",
     "tray_info_idx": "Pa8c5a1a"},
    {"id": 1, "tray_color": "79B78BFF", "tray_type": "PLA", "tray_sub_brands": "",
     "tray_info_idx": "Pa8c5a1a"},
]}]}

ASSIGNMENTS = [
    {"printer_id": 1, "ams_id": 0, "tray_id": 0, "spoolman_spool_id": 42},
    {"printer_id": 1, "ams_id": 0, "tray_id": 1, "spoolman_spool_id": 34},
    # A different printer's slot must not bleed into this snapshot.
    {"printer_id": 9, "ams_id": 0, "tray_id": 0, "spoolman_spool_id": 1},
]

SPOOLS = {"spools": [
    {"id": 42, "filament": {"name": "Meta Taro Purple", "material": "PLA",
                            "color_hex": "8577B6", "vendor": {"name": "SUNLU"}}},
    {"id": 34, "filament": {"name": "Meta Apple Green", "material": "PLA",
                            "color_hex": "79B78B", "vendor": {"name": "SUNLU"}}},
    {"id": 1, "filament": {"name": "Black", "material": "PLA",
                           "vendor": {"name": "Bambu Lab"}}},
]}

PRESETS = {"cloud": {"filament": [
    {"id": "PFUSsunlu", "source": "cloud",
     "name": "SUNLU PLA Meta @Bambu Lab P1S 0.4 nozzle"},
    {"id": "GFSA00_34", "source": "cloud", "name": "Bambu PLA Basic @BBL P1S 0.4 nozzle"},
]}, "standard": {}, "local": {}}

IDMAP = {"Pa8c5a1a": "SUNLU PLA Meta"}


def _wire(monkeypatch, **over):
    async def ok(value):
        return value

    monkeypatch.setattr(config, "PRINTER_ID", 1)
    monkeypatch.setattr(bambuddy, "slot_assignments",
                        lambda: ok(over.get("assignments", ASSIGNMENTS)))
    monkeypatch.setattr(bambuddy, "spoolman_spools", lambda: ok(over.get("spools", SPOOLS)))
    monkeypatch.setattr(bambuddy, "get_presets", lambda: ok(over.get("presets", PRESETS)))
    monkeypatch.setattr(bambuddy, "filament_id_map", lambda: ok(over.get("idmap", IDMAP)))


def test_slots_carry_spool_name_and_slicer_profile(monkeypatch):
    _wire(monkeypatch)
    ams = asyncio.run(handlers._describe_slots(colors.ams_snapshot(STATUS)))
    assert ams[0]["spool"] == "Meta Taro Purple" and ams[0]["vendor"] == "SUNLU"
    assert ams[1]["spool"] == "Meta Apple Green"
    # The tag resolves to the user's own SUNLU preset, not generic Bambu PLA.
    assert ams[0]["preset"] == "SUNLU PLA Meta"


def test_question_shows_product_name_instead_of_hex_guess(monkeypatch):
    _wire(monkeypatch)
    ams = asyncio.run(handlers._describe_slots(colors.ams_snapshot(STATUS)))
    q = colors.build_question("Ding", [{"index": 0, "type": "PLA", "color": "79B78B"}], ams)
    assert "SUNLU Meta Apple Green (PLA)" in q
    assert "⚙️ SUNLU PLA Meta" in q
    # …and specifically not the misleading nearest-palette guess for that slot.
    assert "PLA Grau" not in q and "Flieder" not in q


def test_pastels_do_not_collapse_onto_the_neutrals():
    """The model side of the question has no spool to name from, so the palette
    itself has to cope with pastels. These four are the physical spools that
    prompted it (SUNLU Meta Taro Purple / Apple Green / Lemon Yellow / Ice Blue)."""
    assert colors.color_name("79B78B") == "Hellgrün"
    assert colors.color_name("FBE988") == "Hellgelb"
    assert colors.color_name("7CC9D4") == "Pastellblau"
    assert colors.color_name("A69ED0") == "Flieder"


def test_unassigned_slot_falls_back_to_hex_guess(monkeypatch):
    _wire(monkeypatch, assignments=[])
    ams = asyncio.run(handlers._describe_slots(colors.ams_snapshot(STATUS)))
    assert "spool" not in ams[0]
    assert colors.slot_label(ams[0]) == "PLA Flieder"


def test_spoolman_outage_does_not_break_the_question(monkeypatch):
    async def boom():
        raise RuntimeError("spoolman down")

    _wire(monkeypatch)
    monkeypatch.setattr(bambuddy, "slot_assignments", boom)
    monkeypatch.setattr(bambuddy, "spoolman_spools", boom)
    ams = asyncio.run(handlers._describe_slots(colors.ams_snapshot(STATUS)))
    assert "spool" not in ams[0]
    # The slicer profile is a separate lookup and must survive on its own.
    assert ams[0]["preset"] == "SUNLU PLA Meta"


def test_reslice_uses_the_personal_preset(monkeypatch):
    """End of the chain: the preset shown in the question is the one sent to the
    slicer. This is the bug that put ``Bambu PLA Basic`` in every SUNLU slice."""
    sent = []

    async def fake_slice(lfid, printer_p, process_p, filament_ps, plate=None, bed_type=None):
        sent.append(filament_ps)
        return {"job_id": 1}

    async def fake_await(job_id, **kw):
        return 999

    _wire(monkeypatch)
    monkeypatch.setattr(slicing, "printer_preset", lambda *a: {"source": "cloud", "id": "GM014"})
    monkeypatch.setattr(slicing, "process_preset", lambda *a: {"source": "cloud", "id": "PR1"})
    monkeypatch.setattr(bambuddy, "slice_file", fake_slice)
    monkeypatch.setattr(handlers, "_await_slice", fake_await)

    ams = colors.ams_snapshot(STATUS)
    new_id, _ = asyncio.run(handlers._reslice(
        7, [{"index": 0, "type": "PLA", "color": "79B78B"}], ams, [1]))
    assert new_id == 999
    assert sent[0] == [{"source": "cloud", "id": "PFUSsunlu"}]


def test_reslice_retries_with_system_preset_when_personal_one_fails(monkeypatch):
    sent = []

    async def fake_slice(lfid, printer_p, process_p, filament_ps, plate=None, bed_type=None):
        sent.append(filament_ps)
        return {"job_id": len(sent)}

    async def fake_await(job_id, **kw):
        return None if job_id == 1 else 555  # first (personal) attempt fails

    _wire(monkeypatch)
    monkeypatch.setattr(slicing, "printer_preset", lambda *a: {"source": "cloud", "id": "GM014"})
    monkeypatch.setattr(slicing, "process_preset", lambda *a: {"source": "cloud", "id": "PR1"})
    monkeypatch.setattr(bambuddy, "slice_file", fake_slice)
    monkeypatch.setattr(handlers, "_await_slice", fake_await)

    ams = colors.ams_snapshot(STATUS)
    new_id, _ = asyncio.run(handlers._reslice(
        7, [{"index": 0, "type": "PLA", "color": "79B78B"}], ams, [1]))
    assert new_id == 555
    assert [f[0]["id"] for f in sent] == ["PFUSsunlu", "GFSA00_34"]


NOZZLE_PRESETS = {"cloud": {
    "printer": [
        {"id": "GM014", "source": "cloud", "name": "Bambu Lab P1S 0.4 nozzle"},
        {"id": "GM015", "source": "cloud", "name": "Bambu Lab P1S 0.2 nozzle"},
    ],
    "process": [
        {"id": "PP04", "source": "cloud", "name": "0.20mm Standard @BBL P1P"},
        {"id": "PP02", "source": "cloud", "name": "0.10mm Standard @BBL P1P 0.2 nozzle"},
    ],
    "filament": [
        {"id": "PFUS04", "source": "cloud", "name": "SUNLU PLA Meta @Bambu Lab P1S 0.4 nozzle"},
        {"id": "PFUS02", "source": "cloud", "name": "SUNLU PLA Meta @Bambu Lab P1S 0.2 nozzle"},
    ],
}, "standard": {}, "local": {}}


def _slice_capture(monkeypatch, mounted):
    """Wire a re-slice that records the presets it was asked to slice with."""
    seen = {}

    async def fake_slice(lfid, printer_p, process_p, filament_ps, plate=None, bed_type=None):
        seen.update(printer=printer_p["id"], process=process_p["id"],
                    filament=[f["id"] for f in filament_ps])
        return {"job_id": 1}

    async def fake_await(job_id, **kw):
        return 42

    async def fake_mounted(printer_id):
        return mounted

    _wire(monkeypatch, presets=NOZZLE_PRESETS)
    monkeypatch.setattr(bambuddy, "mounted_nozzle", fake_mounted)
    monkeypatch.setattr(bambuddy, "slice_file", fake_slice)
    monkeypatch.setattr(handlers, "_await_slice", fake_await)
    return seen


def test_slice_follows_the_nozzle_the_printer_reports(monkeypatch):
    """Printer, process and filament preset all key off nozzle diameter. Slicing
    against config.NOZZLE_DIAMETER instead of the fitted nozzle produced gcode the
    machine can't print — and the queue gate then rejected the bot's own output."""
    seen = _slice_capture(monkeypatch, "0.2")
    monkeypatch.setattr(config, "NOZZLE_DIAMETER", "0.4")  # config must lose to reality
    asyncio.run(handlers._reslice(
        7, [{"index": 0, "type": "PLA", "color": "79B78B"}], colors.ams_snapshot(STATUS), [1]))
    assert seen == {"printer": "GM015", "process": "PP02", "filament": ["PFUS02"]}


def test_slice_falls_back_to_config_when_the_nozzle_is_unreadable(monkeypatch):
    seen = _slice_capture(monkeypatch, None)
    monkeypatch.setattr(config, "NOZZLE_DIAMETER", "0.4")
    asyncio.run(handlers._reslice(
        7, [{"index": 0, "type": "PLA", "color": "79B78B"}], colors.ams_snapshot(STATUS), [1]))
    assert seen == {"printer": "GM014", "process": "PP04", "filament": ["PFUS04"]}


def test_sidecar_fallback_declines_a_nozzle_it_has_no_profile_for(monkeypatch):
    """Only profiles/printer_p1s_0.4.json is bundled, so any other nozzle would
    silently come back as 0.4 gcode."""
    called = []
    monkeypatch.setattr(handlers.slicer, "PROFILE_NOZZLE", "0.4")
    monkeypatch.setattr(bambuddy, "download_file",
                        lambda *a, **k: called.append(a) or None)
    assert asyncio.run(handlers._reslice_via_sidecar(1, 1, [], [0], nozzle="0.2")) is None
    assert not called  # declined before even fetching the file


def test_go_hint_only_when_plate_clear_is_required(monkeypatch):
    async def required():
        return {"require_plate_clear": True}

    async def not_required():
        return {"require_plate_clear": False}

    async def boom():
        raise RuntimeError("bambuddy down")

    monkeypatch.setattr(bambuddy, "get_settings", required)
    assert "!go" in asyncio.run(handlers._go_hint("de"))
    monkeypatch.setattr(bambuddy, "get_settings", not_required)
    assert asyncio.run(handlers._go_hint("de")) == ""
    monkeypatch.setattr(bambuddy, "get_settings", boom)
    assert asyncio.run(handlers._go_hint("de")) == ""


def test_go_hint_translated():
    assert "!go" in i18n.t("en", "go_hint") and "!go" in i18n.t("de", "go_hint")
