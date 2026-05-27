"""SQLite state: persistent per-user groups and per-job dialog state.

Two kinds of ``jobs`` rows:
- one *dialog* per group at a time — a non-terminal stage
  (resolving/awaiting_profile/awaiting_plate/awaiting_colors/configuring) that
  carries the in-progress selection and mutates as the user answers;
- one *tracker* per queued plate (stage='queued') holding its queue_item_id,
  watched for completion. A multi-plate request fans out into several trackers.
"""
import json
import sqlite3
import time

import config

_TERMINAL = ("queued", "done", "failed", "cancelled")
# Columns update_dialog may set (guards against typos / injection).
_DIALOG_COLS = {
    "model_id", "library_file_id", "model_name", "required_colors", "ams_snapshot",
    "stage", "queue_item_id", "profile_id", "profiles", "plates", "pending_plates",
    "plate_index", "plate_name",
}


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
        for name, decl in (
            ("queue_item_id", "INTEGER"),
            ("profile_id", "INTEGER"),
            ("profiles", "TEXT"),
            ("plates", "TEXT"),
            ("pending_plates", "TEXT"),
            ("plate_index", "INTEGER"),
            ("plate_name", "TEXT"),
        ):
            if name not in cols:
                c.execute(f"ALTER TABLE jobs ADD COLUMN {name} {decl}")


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


# ----- dialog (the single open job per group) -----

def active_job(group_id):
    """The single open dialog for a group: any non-terminal stage (at most one
    by design). Excludes 'queued'/terminal tracker rows."""
    qs = ",".join("?" * len(_TERMINAL))
    with _conn() as c:
        r = c.execute(
            f"SELECT * FROM jobs WHERE group_id=? AND stage NOT IN ({qs}) ORDER BY id DESC LIMIT 1",
            (group_id, *_TERMINAL),
        ).fetchone()
        return dict(r) if r else None


def create_dialog(group_id, sender, model_id, model_name):
    """Open a fresh dialog at stage 'resolving'; returns its job id."""
    now = time.time()
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO jobs (group_id, sender, model_id, model_name, stage, created_at, updated_at)
               VALUES (?,?,?,?, 'resolving', ?, ?)""",
            (group_id, sender, model_id, model_name, now, now),
        )
        return cur.lastrowid


def update_dialog(job_id, **fields):
    """Set whitelisted dialog columns (+ bumps updated_at)."""
    cols = [k for k in fields if k in _DIALOG_COLS]
    if not cols:
        return
    sets = ", ".join(f"{k}=?" for k in cols) + ", updated_at=?"
    vals = [fields[k] for k in cols] + [time.time(), job_id]
    with _conn() as c:
        c.execute(f"UPDATE jobs SET {sets} WHERE id=?", vals)


def claim_stage(group_id, from_stage, to_stage):
    """Atomically move the group's open dialog from one stage to another, returning
    the (pre-update) row if this caller won — making each answer idempotent against
    duplicate/concurrent replies. Returns None if it wasn't in ``from_stage``."""
    with _conn() as c:
        r = c.execute(
            "SELECT * FROM jobs WHERE group_id=? AND stage=? ORDER BY id DESC LIMIT 1",
            (group_id, from_stage),
        ).fetchone()
        if not r:
            return None
        cur = c.execute(
            "UPDATE jobs SET stage=?, updated_at=? WHERE id=? AND stage=?",
            (to_stage, time.time(), r["id"], from_stage),
        )
        return dict(r) if cur.rowcount > 0 else None


def discard_dialog(job_id):
    """Drop an open (non-terminal) dialog the user gave up on."""
    qs = ",".join("?" * len(_TERMINAL))
    with _conn() as c:
        c.execute(f"DELETE FROM jobs WHERE id=? AND stage NOT IN ({qs})", (job_id, *_TERMINAL))


def delete_job(job_id):
    with _conn() as c:
        c.execute("DELETE FROM jobs WHERE id=?", (job_id,))


# ----- queued trackers (one per plate) -----

def add_queued(group_id, sender, model_name, library_file_id, queue_item_id):
    """Record a queued plate so it can be watched for completion / cancelled."""
    now = time.time()
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO jobs (group_id, sender, model_name, library_file_id,
                                 queue_item_id, stage, created_at, updated_at)
               VALUES (?,?,?,?,?, 'queued', ?, ?)""",
            (group_id, sender, model_name, library_file_id, queue_item_id, now, now),
        )
        return cur.lastrowid


def last_queued_job(group_id):
    """Most recent queued tracker for a group that has a Bambuddy queue item id."""
    with _conn() as c:
        r = c.execute(
            "SELECT * FROM jobs WHERE group_id=? AND stage='queued' "
            "AND queue_item_id IS NOT NULL ORDER BY id DESC LIMIT 1",
            (group_id,),
        ).fetchone()
        return dict(r) if r else None


def mark_cancelled(job_id):
    set_stage(job_id, "cancelled")


def set_stage(job_id, stage):
    with _conn() as c:
        c.execute("UPDATE jobs SET stage=?, updated_at=? WHERE id=?", (stage, time.time(), job_id))


def queued_jobs_with_item():
    """Queued trackers that have a Bambuddy item id — watched for completion."""
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM jobs WHERE stage='queued' AND queue_item_id IS NOT NULL"
        ).fetchall()
        return [dict(r) for r in rows]
