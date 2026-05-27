import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import classify  # noqa: E402

INTERNAL = "HsuVb5fL+Kfc7Ve5Ui53V9w98LYK5wCGrHRw8nTvomw="


def test_dm_with_link():
    p = classify.classify({"sourceNumber": "+43111", "dataMessage": {"message": "schau mal https://makerworld.com/en/models/123"}})
    assert p["is_dm"] and p["has_link"]
    assert p["url"] == "https://makerworld.com/en/models/123"


def test_dm_bare_link_gets_scheme():
    p = classify.classify({"sourceNumber": "+43111", "dataMessage": {"message": "makerworld.com/models/9"}})
    assert p["url"].startswith("https://")


def test_dm_no_link():
    p = classify.classify({"sourceNumber": "+43111", "dataMessage": {"message": "hallo"}})
    assert p["is_dm"] and not p["has_link"] and not p["is_numbered"]


def test_group_numbered_reply():
    env = {"sourceNumber": "+43111", "dataMessage": {"message": "3 1", "groupInfo": {"groupId": INTERNAL}}}
    p = classify.classify(env)
    assert not p["is_dm"] and p["is_numbered"]
    assert p["group_send_id"].startswith("group.")


def test_group_id_roundtrip_both_forms():
    internal, send_id = classify.normalize_group(INTERNAL)
    assert internal == INTERNAL and send_id.startswith("group.")
    internal2, send_id2 = classify.normalize_group(send_id)
    assert internal2 == INTERNAL and send_id2 == send_id


def test_comma_separated_is_numbered():
    p = classify.classify({"sourceNumber": "+1", "dataMessage": {"message": "2, 4, 1", "groupInfo": {"groupId": INTERNAL}}})
    assert p["is_numbered"]


def _grp(msg):
    return classify.classify(
        {"sourceNumber": "+1", "dataMessage": {"message": msg, "groupInfo": {"groupId": INTERNAL}}}
    )


def test_cancel_keywords():
    for m in ("!abbrechen", "!Abbrechen", " !cancel ", "!stop", "!verwerfen"):
        assert _grp(m)["is_cancel"], m
    assert not _grp("3 1")["is_cancel"]
    assert not _grp("abbrechen")["is_cancel"]  # needs the ! prefix
    assert not _grp("bitte !abbrechen jetzt")["is_cancel"]  # whole message only


def test_list_keywords():
    for m in ("!liste", "!Queue", "!warteschlange"):
        assert _grp(m)["is_list"], m
    assert not _grp("liste")["is_list"]  # needs the ! prefix
    assert not _grp("3 1")["is_list"]


def test_progress_keywords():
    for m in ("!progress", "!fortschritt", "!status", "!druck"):
        assert _grp(m)["is_progress"], m
    assert not _grp("!status")["is_list"]  # status is progress now, not list
    assert not _grp("progress")["is_progress"]  # needs the ! prefix


def test_help_keywords():
    for m in ("!help", "!hilfe", "!?", "!befehle", " !commands "):
        assert _grp(m)["is_help"], m
    assert not _grp("help")["is_help"]  # needs the ! prefix
    assert not _grp("3 1")["is_help"]


INNER = {"sourceNumber": "+1", "dataMessage": {"message": "x"}}


def test_to_envelopes_bare():
    assert classify.to_envelopes(INNER) == [INNER]


def test_to_envelopes_wrapped_envelope():
    assert classify.to_envelopes({"envelope": INNER}) == [INNER]


def test_to_envelopes_n8n_body_object():
    assert classify.to_envelopes({"body": {"envelope": INNER}}) == [INNER]


def test_to_envelopes_raw_list():
    assert classify.to_envelopes([{"envelope": INNER}, INNER]) == [INNER, INNER]


def test_to_envelopes_body_list():
    assert classify.to_envelopes({"body": [{"envelope": INNER}]}) == [INNER]


def test_to_envelopes_skips_non_dicts():
    assert classify.to_envelopes(["nope", INNER]) == [INNER]
