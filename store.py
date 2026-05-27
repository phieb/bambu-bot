"""SQLite state: persistent per-user groups and per-job dialog state."""
import json
import sqlite3
import time

import config


def _conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS groups (
                sender TEXT PRIMARY KEY,
                group_id TEXT NOT NULL,
                group_name TEXT,
                created_at REAL
            )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL,
                sender TEXT,
                model_id INTEGER,
                library_file_id INTEGER,
                model_name TEXT,
                required_colors TEXT,
                ams_snapshot TEXT,
                stage TEXT NOT NULL,
                created_at REAL,
                updated_at REAL
            )"""
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_jobs_group_stage ON jobs(group_id, stage)")
        cols = [r[1] for r in c.execute("PRAGMA table_info(jobs)").fetchall()]
        if "queue_item_id" not in cols:
            c.execute("ALTER TABLE jobs ADD COLUMN queue_item_id INTEGER")


# ----- groups (registry) -----

def get_group_by_sender(sender):
    with _conn() as c:
        r = c.execute("SELECT * FROM groups WHERE sender=?", (sender,)).fetchone()
        return dict(r) if r else None


def get_group_by_group_id(group_id):
    with _conn() as c:
        r = c.execute("SELECT * FROM groups WHERE group_id=?", (group_id,)).fetchone()
        return dict(r) if r else None


def save_group(sender, group_id, group_name):
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO groups (sender, group_id, group_name, created_at) VALUES (?,?,?,?)",
            (sender, group_id, group_name, time.time()),
        )


# ----- jobs (dialog state) -----

def active_job(group_id):
    """The single open dialog for a group (at most one by design)."""
    with _conn() as c:
        r = c.execute(
            "SELECT * FROM jobs WHERE group_id=? AND stage='awaiting_colors' ORDER BY id DESC LIMIT 1",
            (group_id,),
        ).fetchone()
        return dict(r) if r else None


def create_job(group_id, sender, model_id, library_file_id, model_name, required, ams):
    now = time.time()
    with _conn() as c:
        c.execute(
            """INSERT INTO jobs
               (group_id, sender, model_id, library_file_id, model_name,
                required_colors, ams_snapshot, stage, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?, 'awaiting_colors', ?, ?)""",
            (group_id, sender, model_id, library_file_id, model_name,
             json.dumps(required), json.dumps(ams), now, now),
        )


def claim_job_for_queue(group_id):
    """Atomically flip the open job awaiting_colors -> queued.

    Returns True only for the caller that actually performed the transition,
    making the queue step idempotent against duplicate/concurrent replies.
    """
    with _conn() as c:
        cur = c.execute(
            "UPDATE jobs SET stage='queued', updated_at=? WHERE group_id=? AND stage='awaiting_colors'",
            (time.time(), group_id),
        )
        return cur.rowcount > 0


def set_queue_item_id(job_id, queue_item_id):
    """Record the Bambuddy queue item id so the job can later be cancelled."""
    with _conn() as c:
        c.execute(
            "UPDATE jobs SET queue_item_id=?, updated_at=? WHERE id=?",
            (queue_item_id, time.time(), job_id),
        )


def discard_dialog(job_id):
    """Drop an open (awaiting_colors) dialog the user gave up on."""
    with _conn() as c:
        c.execute("DELETE FROM jobs WHERE id=? AND stage='awaiting_colors'", (job_id,))


def last_queued_job(group_id):
    """Most recent queued job for a group that has a Bambuddy queue item id."""
    with _conn() as c:
        r = c.execute(
            "SELECT * FROM jobs WHERE group_id=? AND stage='queued' "
            "AND queue_item_id IS NOT NULL ORDER BY id DESC LIMIT 1",
            (group_id,),
        ).fetchone()
        return dict(r) if r else None


def mark_cancelled(job_id):
    with _conn() as c:
        c.execute(
            "UPDATE jobs SET stage='cancelled', updated_at=? WHERE id=?",
            (time.time(), job_id),
        )
