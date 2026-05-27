import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config  # noqa: E402
import store  # noqa: E402


def test_stage_transitions_and_completion_watch(tmp_path):
    config.DB_PATH = str(tmp_path / "t.db")
    store.init_db()
    store.create_job("group.x", "+1", 111, 222, "Benchy", [], [])

    job = store.active_job("group.x")
    assert job  # awaiting_colors
    assert store.queued_jobs_with_item() == []  # not yet queued → not watched

    assert store.claim_job_for_queue("group.x")
    store.set_queue_item_id(job["id"], 5)
    watched = store.queued_jobs_with_item()
    assert [w["id"] for w in watched] == [job["id"]]
    assert watched[0]["queue_item_id"] == 5

    store.set_stage(job["id"], "done")
    assert store.queued_jobs_with_item() == []  # terminal → no longer watched
