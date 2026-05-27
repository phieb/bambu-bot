import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import colors  # noqa: E402

RESOLVED = {
    "model_id": 123,
    "profile_id": None,
    "design": {
        "title": "Test Modell",
        "instances": [
            {"id": 1, "instanceFilaments": [
                {"type": "PLA", "color": "#FFFFFF"},
                {"type": "PLA", "color": "#000000"},
            ]}
        ],
    },
    "instances": [{"id": 1, "profileId": 999}],
}

STATUS = {"ams": [{"tray": [
    {"id": 0, "tray_type": "PLA", "tray_color": "898989FF"},
    {"id": 1, "tray_type": "PLA", "tray_color": "FFFFFFFF"},
    {"id": 2, "tray_type": "PETG", "tray_color": "000000FF"},
]}]}


def test_required_colors():
    rc = colors.required_colors(RESOLVED)
    assert [c["color"] for c in rc] == ["FFFFFF", "000000"]


def test_model_name():
    assert colors.model_name(RESOLVED) == "Test Modell"


def test_ams_snapshot_slots_are_one_based():
    ams = colors.ams_snapshot(STATUS)
    assert [a["slot"] for a in ams] == [1, 2, 3]
    assert ams[0]["color"] == "898989"


def test_parse_reply_maps_to_zero_based_tray():
    rc = colors.required_colors(RESOLVED)
    ams = colors.ams_snapshot(STATUS)
    ok, mapping, err = colors.parse_reply("2 1", rc, ams)
    assert ok and mapping == [1, 0] and err is None


def test_parse_reply_rejects_wrong_count():
    rc = colors.required_colors(RESOLVED)
    ams = colors.ams_snapshot(STATUS)
    ok, mapping, err = colors.parse_reply("1", rc, ams)
    assert not ok and mapping is None and "1 und 3" in err


def test_parse_reply_rejects_out_of_range():
    rc = colors.required_colors(RESOLVED)
    ams = colors.ams_snapshot(STATUS)
    ok, _, err = colors.parse_reply("2 9", rc, ams)
    assert not ok


def test_build_question_mentions_colors_and_slots():
    rc = colors.required_colors(RESOLVED)
    ams = colors.ams_snapshot(STATUS)
    q = colors.build_question("Test Modell", rc, ams)
    assert "Test Modell" in q and "AMS Slots" in q and "1)" in q
