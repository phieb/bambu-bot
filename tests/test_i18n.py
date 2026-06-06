import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import classify  # noqa: E402
import colors  # noqa: E402
import config  # noqa: E402
import handlers  # noqa: E402
import i18n  # noqa: E402
import store  # noqa: E402


# ----- i18n core -----

def test_t_picks_language_and_formats():
    assert i18n.t("en", "go_ok").startswith("✅ Plate confirmed clear")
    assert i18n.t("de", "go_ok").startswith("✅ Platte als frei")
    # unknown language → German fallback (the bot's default)
    assert i18n.t("fr", "go_ok") == i18n.t("de", "go_ok")
    # placeholder filling
    assert "5" in i18n.t("en", "profile_pick_invalid", n=5)


def test_every_key_has_both_languages():
    for key, entry in i18n._M.items():
        assert "de" in entry and "en" in entry, key
        assert entry["de"] and entry["en"], key


def test_unknown_key_is_empty_not_error():
    assert i18n.t("en", "does_not_exist") == ""


# ----- classify: language command -----

def test_lang_command_shortcuts():
    assert classify.lang_command("!english") == "en"
    assert classify.lang_command("!englisch") == "en"
    assert classify.lang_command("!en") == "en"
    assert classify.lang_command("!deutsch") == "de"
    assert classify.lang_command("!de") == "de"
    assert classify.lang_command("!german") == "de"


def test_lang_command_with_argument():
    assert classify.lang_command("!lang en") == "en"
    assert classify.lang_command("!sprache deutsch") == "de"
    assert classify.lang_command("!language english") == "en"


def test_lang_command_no_argument_shows():
    assert classify.lang_command("!lang") == "show"
    assert classify.lang_command("!sprache") == "show"


def test_lang_command_rejects_non_commands():
    assert classify.lang_command("english please") is None
    assert classify.lang_command("!list") is None
    assert classify.lang_command("3 1 2") is None
    # exposed on the classify dict too
    assert classify.classify({"dataMessage": {"message": "!english"}})["lang_command"] == "en"


# ----- store: per-group language -----

def test_store_lang_defaults_to_german(tmp_path):
    config.DB_PATH = str(tmp_path / "t.db")
    store.init_db()
    assert store.get_lang("group.unknown") == "de"


def test_store_set_and_get_lang(tmp_path):
    config.DB_PATH = str(tmp_path / "t.db")
    store.init_db()
    store.save_group("+43111", "group.abc", "grp")
    store.set_lang("group.abc", "en")
    assert store.get_lang("group.abc") == "en"
    store.set_lang("group.abc", "de")
    assert store.get_lang("group.abc") == "de"


# ----- colors: bilingual rendering -----

def test_color_name_english():
    assert colors.color_name("FFFFFF", "en") == "White"
    assert colors.color_name("000000", "en") == "Black"
    assert colors.color_name("FFFFFF", "de") == "Weiß"
    assert colors.color_name("FFFFFF") == "Weiß"  # default stays German


def test_build_question_english():
    rc = [{"index": 0, "type": "PLA", "color": "FFFFFF", "name": ""}]
    ams = [{"slot": 1, "tray_id": 0, "type": "PLA", "color": "FFFFFF", "sub": ""}]
    q = colors.build_question("Cat", rc, ams, "en")
    assert "needs 1 color" in q and "AMS slots" in q and "White" in q
    # German default unchanged
    q_de = colors.build_question("Cat", rc, ams)
    assert "braucht 1 Farbe" in q_de and "Weiß" in q_de


def test_parse_reply_english_error():
    rc = [{"index": 0, "type": "PLA", "color": "FFFFFF"}]
    ams = [{"slot": 1, "tray_id": 0, "type": "PLA", "color": "FFFFFF", "sub": ""}]
    ok, _, err = colors.parse_reply("9", rc, ams, "en")
    assert not ok and "between 1 and 1" in err


def test_help_and_unknown_text_english():
    assert "Commands" in colors.help_text("en")
    assert "Befehle" in colors.help_text("de")
    assert "what that means" in colors.unknown_text("en")
    assert "MakerWorld" in colors.other_model_text("en")


# ----- handlers: switching a group's language end to end -----

async def _none():
    return None


def test_set_lang_switches_and_confirms_in_target(tmp_path, monkeypatch):
    config.DB_PATH = str(tmp_path / "t.db")
    store.init_db()
    store.save_group("+1", "group.x", "grp")
    sent = []
    monkeypatch.setattr(handlers.signal_client, "send_to_group",
                        lambda gid, msg, **kw: sent.append(msg) or _none())

    asyncio.run(handlers._set_lang("group.x", "en"))
    assert store.get_lang("group.x") == "en"
    assert "English" in sent[-1]  # confirmation comes in the new language

    asyncio.run(handlers._set_lang("group.x", "de"))
    assert store.get_lang("group.x") == "de"
    assert "Deutsch" in sent[-1]


def test_help_route_uses_group_language(tmp_path, monkeypatch):
    config.DB_PATH = str(tmp_path / "t.db")
    store.init_db()
    store.save_group("+1", "group.x", "grp")
    store.set_lang("group.x", "en")
    sent = []
    monkeypatch.setattr(handlers.signal_client, "send_to_group",
                        lambda gid, msg, **kw: sent.append(msg) or _none())

    env = {"sourceNumber": "+1",
           "dataMessage": {"message": "!help", "groupInfo": {"groupId": "internal"}}}
    # point the registered group at the send-id the classifier derives
    gid = classify.classify(env)["group_send_id"]
    store.save_group("+1", gid, "grp")
    store.set_lang(gid, "en")

    asyncio.run(handlers.handle(env))
    assert sent and "Commands" in sent[0]


def test_completion_message_localized(tmp_path, monkeypatch):
    config.DB_PATH = str(tmp_path / "t.db")
    store.init_db()
    store.save_group("+1", "group.x", "grp")
    store.set_lang("group.x", "en")
    store.add_queued("group.x", "+1", "Benchy", 4, 42)
    sent = []
    monkeypatch.setattr(handlers.signal_client, "send_to_group",
                        lambda gid, msg, **kw: sent.append(msg) or _none())

    item = {"status": "completed"}

    async def _get_item(_id):
        return item

    async def _pstatus(_pid):
        return {"state": "IDLE"}
    monkeypatch.setattr(handlers.bambuddy, "get_queue_item", _get_item)
    monkeypatch.setattr(handlers.bambuddy, "printer_status", _pstatus)

    asyncio.run(handlers._check_completions())
    assert sent and "has finished printing" in sent[0]
