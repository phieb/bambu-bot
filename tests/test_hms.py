"""Explaining the printer's error codes.

Bambuddy sends the *number* only — its ``hms_errors`` schema has no description
field at all — so before this the bot relayed „⚠️ Fehler: 0x4003" and left the
reader none the wiser. The catalogue under ``hms_data/`` supplies the sentence.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import hms  # noqa: E402
import i18n  # noqa: E402


def bambuddy_entry(full_code, severity=2, actions=()):
    """The shape Bambuddy really posts (schemas/printer.py HMSErrorResponse):
    ``code`` is only the low 32 bits, ``full_code`` is the catalogue key."""
    attr, code = int(full_code[:8], 16), int(full_code[8:] or "0", 16)
    return {"code": f"0x{code:x}", "attr": attr, "module": (attr >> 24) & 0xFF,
            "severity": severity, "actions": list(actions), "job_id": None,
            "full_code": full_code}


# A real HMS code + its real catalogue text. Verified against Bambu's published
# table; if a firmware refresh ever renames it, that's worth noticing.
RUNOUT = "0700200000020001"
RUNOUT_TEXT = "AMS A Slot 1 Filament ist aufgebraucht"


def test_catalogue_is_bundled_for_both_languages():
    for lang in i18n.LANGS:
        table = hms._table(lang)
        assert len(table["hms"]) > 1000 and len(table["err"]) > 100


def test_explains_a_real_hms_code():
    line = hms.describe(bambuddy_entry(RUNOUT))
    assert RUNOUT_TEXT in line
    assert "0700-2000-0002-0001" in line          # printer-screen grouping


def test_explains_in_english_too():
    line = hms.describe(bambuddy_entry(RUNOUT), "en")
    assert "AMS A Slot 1" in line and "run out" in line


def test_explains_a_print_error_code():
    """print_error rides in the same list with an 8-char code and its own table."""
    line = hms.describe(bambuddy_entry("07028002"), "de")
    assert "0702-8002" in line and "Schneid" in line


def test_code_is_reconstructed_when_full_code_is_missing():
    """`code` alone is the low half — useless as a key. attr + code rebuilds it."""
    entry = bambuddy_entry(RUNOUT)
    del entry["full_code"]
    assert RUNOUT_TEXT in hms.describe(entry)


def test_unknown_code_still_reports_the_number():
    line = hms.describe(bambuddy_entry("FFFFFFFFFFFFFFFF"))
    assert "FFFF-FFFF-FFFF-FFFF" in line and "unbekannt" in line


def test_severity_picks_the_emoji():
    assert hms.describe(bambuddy_entry(RUNOUT, severity=1)).startswith("🛑")
    assert hms.describe(bambuddy_entry(RUNOUT, severity=4)).startswith("ℹ️")
    # No severity reported → derived from the code's severity nibble (0x8 = warn).
    assert hms.describe({"full_code": RUNOUT}).startswith("⚠️")


def test_an_explicit_description_wins_over_the_catalogue():
    """Older Bambuddy builds may caption it themselves — don't overrule them."""
    line = hms.describe({"code": "0500-4003", "description": "Filament ausgegangen"})
    assert "Filament ausgegangen" in line and "0500-4003" in line


@pytest.mark.parametrize("entry", [None, "", "junk", {}, {"code": None}, 42, []])
def test_junk_entries_never_raise(entry):
    """This runs in the completion poller: one exception kills the whole cycle."""
    assert hms.describe(entry)


@pytest.mark.parametrize("status", [None, {}, {"hms_errors": None},
                                    {"hms_errors": "not-a-list"}, "nonsense"])
def test_broken_status_reads_as_healthy(status):
    assert hms.entries(status) == [] and hms.codes(status) == []
    assert hms.detail(status) == ""


def test_detail_is_one_line_per_error_and_capped():
    status = {"hms_errors": [bambuddy_entry(RUNOUT)] * 4}
    out = hms.detail(status, "de", limit=3)
    assert out.startswith(":\n")
    assert out.count(RUNOUT_TEXT) == 3
    assert "1 weitere" in out


def test_codes_identify_the_incident():
    """The alert 'is this still the same problem?' key must be the full code —
    two different faults in one module must not collapse into one."""
    status = {"hms_errors": [bambuddy_entry(RUNOUT), bambuddy_entry("0700200000020005")]}
    assert hms.codes(status) == [RUNOUT, "0700200000020005"]
    assert hms.codes({"hms_errors": []}) == []


# ----- the failure message ----------------------------------------------------

def test_failure_message_falls_back_to_the_hms_reason(tmp_path, monkeypatch):
    """Bambuddy usually leaves error_message empty; the printer's standing HMS
    code is what actually says why the print died."""
    import asyncio
    import config
    import handlers
    import store

    config.DB_PATH = str(tmp_path / "t.db")
    store.init_db()
    store.add_queued("group.x", "+1", "Benchy", 4, 42)
    sent = []

    async def _none():
        return None
    monkeypatch.setattr(handlers.signal_client, "send_to_group",
                        lambda gid, msg, **kw: sent.append(msg) or _none())

    async def _item(_id):
        return {"status": "failed"}

    async def _pstatus(_pid):
        return {"state": "FAILED", "hms_errors": [
            {"code": "0x20001", "attr": 0x07002000, "severity": 2,
             "full_code": "0700200000020001"}]}
    monkeypatch.setattr(handlers.bambuddy, "get_queue_item", _item)
    monkeypatch.setattr(handlers.bambuddy, "printer_status", _pstatus)

    asyncio.run(handlers._check_completions())
    assert "fehlgeschlagen" in sent[0]
    assert "AMS A Slot 1 Filament ist aufgebraucht" in sent[0]
