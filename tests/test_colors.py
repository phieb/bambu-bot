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


def test_build_question_uses_names_not_hex():
    rc = colors.required_colors(RESOLVED)
    ams = colors.ams_snapshot(STATUS)
    q = colors.build_question("Test Modell", rc, ams)
    assert "#" not in q and "Weiß" in q and "Schwarz" in q


PROFILES_RESOLVED = {
    "design": {"title": "X1 & P1 Model"},
    "instances": [
        {"profileId": 11, "title": "fine", "creator": {"name": "alice"}, "score": 5,
         "compatibility": {"devProductName": "P1S"},
         "otherCompatibility": [{"devProductName": "X1 Carbon"}]},
        {"profileId": 22, "title": "draft", "creator": {"name": "bob"},
         "otherCompatibility": [{"devProductName": "H2D"}]},
    ],
}


def test_profiles_list_flags_target_printer():
    profs = colors.profiles_list(PROFILES_RESOLVED, "P1S")
    assert [p["profile_id"] for p in profs] == [11, 22]
    assert profs[0]["is_target"] and not profs[1]["is_target"]
    assert profs[0]["printer"] == "P1S" and profs[1]["printer"] == "H2D"


def test_build_profile_question_counts_target():
    profs = colors.profiles_list(PROFILES_RESOLVED, "P1S")
    q = colors.build_profile_question("X1 & P1 Model", profs, "P1S")
    assert "1 davon für deinen P1S" in q
    assert "✅ P1S" in q and "von alice" in q and "★5" in q


PLATES = [
    {"index": 1, "name": "ABS", "filaments": [{"type": "ABS", "color": "#E05028"}],
     "print_time_seconds": 1267, "filament_used_grams": 5.0},
    {"index": 2, "name": "PETG_HF", "filaments": [{"type": "PETG", "color": "#FFFFFF"}],
     "print_time_seconds": 1254, "filament_used_grams": 6.2},
]


def test_plate_required_is_per_plate():
    # the key multi-plate fix: one filament for this plate, not the model-wide union
    req = colors.plate_required(PLATES[1])
    assert len(req) == 1 and req[0]["type"] == "PETG" and req[0]["color"] == "FFFFFF"


def test_build_plate_question_lists_plates():
    q = colors.build_plate_question("Diffuser", PLATES)
    assert "2 Plates" in q and "ABS" in q and "PETG_HF" in q
    assert "21 min" in q and "5 g" in q  # time + weight formatted


def test_color_name_basics():
    assert colors.color_name("FFFFFF") == "Weiß"
    assert colors.color_name("000000") == "Schwarz"
    assert colors.color_name("898989") == "Grau"
    assert colors.color_name("") == ""
    assert colors.color_name("xyz") == ""


def test_swatch_renders_png():
    import swatch
    rc = colors.required_colors(RESOLVED)
    ams = colors.ams_snapshot(STATUS)
    b64 = swatch.build("Test Modell", rc, ams)
    assert isinstance(b64, str) and len(b64) > 100
    import base64
    assert base64.b64decode(b64)[:8] == b"\x89PNG\r\n\x1a\n"


def test_shrink_image_downscales_to_jpeg():
    import base64
    import io

    import swatch
    from PIL import Image

    big = Image.new("RGB", (2000, 1500), (120, 60, 200))
    buf = io.BytesIO()
    big.save(buf, format="PNG")
    out = swatch.shrink_image(buf.getvalue(), max_px=512)
    assert isinstance(out, str)
    data = base64.b64decode(out)
    assert data[:3] == b"\xff\xd8\xff"  # JPEG magic
    w, h = Image.open(io.BytesIO(data)).size
    assert max(w, h) == 512 and len(data) < len(buf.getvalue())


def test_shrink_image_handles_garbage():
    import swatch
    assert swatch.shrink_image(b"not an image") is None
    assert swatch.shrink_image(None) is None
