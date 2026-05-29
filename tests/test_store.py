import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config  # noqa: E402
import store  # noqa: E402


def test_dialog_lifecycle_and_completion_watch(tmp_path):
    config.DB_PATH = str(tmp_path / "t.db")
    store.init_db()

    job_id = store.create_dialog("group.x", "+1", 111, "Benchy")
    dlg = store.active_job("group.x")
    assert dlg and dlg["id"] == job_id and dlg["stage"] == "resolving"
    assert store.queued_jobs_with_item() == []  # nothing queued yet

    # advance the dialog through stages, carrying state
    store.update_dialog(job_id, stage="awaiting_plate", library_file_id=4,
                        plates=json.dumps([{"index": 1}, {"index": 2}]))
    assert store.active_job("group.x")["stage"] == "awaiting_plate"

    # atomic stage claim is single-winner (idempotent against duplicate replies)
    assert store.claim_stage("group.x", "awaiting_plate", "configuring")
    assert store.claim_stage("group.x", "awaiting_plate", "configuring") is None

    store.update_dialog(job_id, stage="awaiting_colors", plate_index=1,
                        required_colors=json.dumps([{"index": 0}]))
    assert store.active_job("group.x")["plate_index"] == 1


def test_queued_trackers_are_watched_and_terminal(tmp_path):
    config.DB_PATH = str(tmp_path / "t.db")
    store.init_db()

    t1 = store.add_queued("group.x", "+1", "Benchy — ABS", 4, 5)
    store.add_queued("group.x", "+1", "Benchy — PETG", 4, 6)
    # both are watched; the dialog query ignores queued trackers
    assert {j["queue_item_id"] for j in store.queued_jobs_with_item()} == {5, 6}
    assert store.active_job("group.x") is None

    assert store.last_queued_job("group.x")["queue_item_id"] == 6  # most recent
    store.set_stage(t1, "done")
    assert {j["queue_item_id"] for j in store.queued_jobs_with_item()} == {6}

    # a started print ('printing') is still watched (for completion) but is no
    # longer an open dialog and no longer the cancellable 'queued' tracker
    t3 = store.add_queued("group.y", "+1", "Tall — PLA", 7, 8)
    store.set_stage(t3, "printing")
    assert 8 in {j["queue_item_id"] for j in store.queued_jobs_with_item()}
    assert store.active_job("group.y") is None
    assert store.last_queued_job("group.y") is None  # not cancellable once printing


def test_discard_only_drops_open_dialog(tmp_path):
    config.DB_PATH = str(tmp_path / "t.db")
    store.init_db()
    job_id = store.create_dialog("group.x", "+1", 1, "X")
    store.update_dialog(job_id, stage="awaiting_colors")
    queued = store.add_queued("group.x", "+1", "X", 4, 9)

    store.discard_dialog(job_id)
    assert store.active_job("group.x") is None          # dialog gone
    assert store.last_queued_job("group.x")["id"] == queued  # tracker untouched
    store.discard_dialog(queued)                         # refuses terminal rows
    assert store.last_queued_job("group.x")["id"] == queued
