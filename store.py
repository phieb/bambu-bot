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

# Stages that are NOT an open dialog: tracker rows ('queued'/'printing') and
# terminal ones. active_job / delete_job use this to leave trackers alone.
_TERMINAL = ("queued", "printing", "done", "failed", "cancelled")
# Columns update_dialog may set (guards against typos / injection).
_DIALOG_COLS = {
    "model_id", "library_file_id", "model_name", "required_colors", "ams_snapshot",
    "stage", "queue_item_id", "profile_id", "profiles", "plates", "pending_plates",
    "plate_index", "plate_name", "decisions",
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
        gcols = [r[1] for r in c.execute("PRAGMA table_info(groups)").fetchall()]
        if "lang" not in gcols:
            c.execute("ALTER TABLE groups ADD COLUMN lang TEXT")
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
        c.execute(
            """CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )"""
        )
        # Per-group preferences. Deliberately NOT a column on `groups`: that table
        # is keyed by sender and save_group() is an INSERT OR REPLACE listing only
        # four columns, so anything added there is silently wiped whenever someone
        # re-registers. And `settings` above is a global KV store by contract.
        c.execute(
            """CREATE TABLE IF NOT EXISTS group_settings (
                group_id TEXT PRIMARY KEY,
                standing_abo INTEGER NOT NULL DEFAULT 0,
                standing_since REAL
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
            ("decisions", "TEXT"),
            ("eject", "INTEGER"),
            ("archive_id", "INTEGER"),
            ("alerts", "TEXT"),
        ):
            if name not in cols:
                c.execute(f"ALTER TABLE jobs ADD COLUMN {name} {decl}")


# ----- settings (global key/value) -----

def get_flag(key, default=False):
    """Read a boolean flag (stored as '1'/'0'); ``default`` if unset."""
    with _conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return default if r is None else r["value"] == "1"


def set_flag(key, value):
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
            (key, "1" if value else "0"),
        )


def get_setting(key, default=None):
    """Read a string setting, or ``default`` if unset (vs get_flag's bool)."""
    with _conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return default if r is None else r["value"]


def set_setting(key, value):
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
            (key, str(value)),
        )


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


def get_lang(group_id, default="de"):
    """The display language ('de'|'en') chosen for a group; ``default`` if the
    group is unknown or never switched (the bot's default stays German)."""
    with _conn() as c:
        r = c.execute("SELECT lang FROM groups WHERE group_id=?", (group_id,)).fetchone()
    return (r["lang"] if r and r["lang"] else default)


def set_lang(group_id, lang):
    """Persist a group's display language (no-op if the group isn't registered)."""
    with _conn() as c:
        c.execute("UPDATE groups SET lang=? WHERE group_id=?", (lang, group_id))


# ----- per-group preferences -----

def set_standing_abo(group_id, enabled):
    """Turn a group's standing subscription ('Dauer-Abo') on/off — with it on the
    group gets a tracker for every future queue item automatically, whatever the
    print's source."""
    with _conn() as c:
        c.execute(
            """INSERT INTO group_settings (group_id, standing_abo, standing_since)
               VALUES (?,?,?)
               ON CONFLICT(group_id) DO UPDATE SET standing_abo=excluded.standing_abo,
                                                   standing_since=excluded.standing_since""",
            (group_id, 1 if enabled else 0, time.time() if enabled else None),
        )


def get_standing_abo(group_id):
    with _conn() as c:
        row = c.execute("SELECT standing_abo FROM group_settings WHERE group_id=?",
                        (group_id,)).fetchone()
    return bool(row and row["standing_abo"])


def standing_abo_groups():
    """group_ids with a standing subscription — the auto-adopt pass's work list."""
    with _conn() as c:
        rows = c.execute(
            "SELECT group_id FROM group_settings WHERE standing_abo=1").fetchall()
    return [r["group_id"] for r in rows]


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

def add_queued(group_id, sender, model_name, library_file_id, queue_item_id, eject=False,
               plate_index=None, plate_name=None, archive_id=None):
    """Record a queued plate so it can be watched for completion / cancelled.
    ``eject`` = a Farmloop eject was injected into this job's gcode.

    ``plate_index``/``plate_name``/``archive_id`` identify *which* plate of a
    multi-plate file this tracker is. They used to be left NULL, which meant the
    completion poller could never render the per-plate thumbnail (it always fell
    back to the generic model image) and a tracker stopped being self-describing
    once its queue item aged out of Bambuddy."""
    now = time.time()
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO jobs (group_id, sender, model_name, library_file_id,
                                 queue_item_id, eject, plate_index, plate_name,
                                 archive_id, stage, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?, 'queued', ?, ?)""",
            (group_id, sender, model_name, library_file_id, queue_item_id,
             1 if eject else 0, plate_index, plate_name, archive_id, now, now),
        )
        return cur.lastrowid


def eject_by_item():
    """{queue_item_id: bool} — whether each still-active tracked job has an eject
    injected. For annotating !liste and warning on !eject off."""
    with _conn() as c:
        rows = c.execute(
            "SELECT queue_item_id, eject FROM jobs "
            "WHERE stage IN ('queued','printing') AND queue_item_id IS NOT NULL"
        ).fetchall()
        return {r["queue_item_id"]: bool(r["eject"]) for r in rows}


def queued_eject_jobs():
    """model_names of still-active tracked jobs that have an eject baked in —
    these keep ejecting even after !eject off (it's already in their gcode)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT model_name FROM jobs "
            "WHERE stage IN ('queued','printing') AND eject=1 ORDER BY id"
        ).fetchall()
        return [r["model_name"] for r in rows]


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


# ----- intervention alerts (printer needs a human) -----

def get_alerts(job_id):
    """Condition keys already announced for this tracker (e.g. {'pause'})."""
    with _conn() as c:
        row = c.execute("SELECT alerts FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row or not row["alerts"]:
        return set()
    try:
        return set(json.loads(row["alerts"]))
    except (ValueError, TypeError):
        return set()


def mark_alert(job_id, key):
    """Record that alert ``key`` was announced for this tracker. Returns True the
    first time (→ send it) and False afterwards (→ stay quiet).

    This is the 'once per condition per job' gate. It lives in sqlite rather than
    memory so an unresolved pause can't re-announce itself every 60 s poll, or
    again after a restart."""
    have = get_alerts(job_id)
    if key in have:
        return False
    have.add(key)
    with _conn() as c:
        c.execute("UPDATE jobs SET alerts=?, updated_at=? WHERE id=?",
                  (json.dumps(sorted(have)), time.time(), job_id))
    return True


def clear_alerts(job_id):
    """Forget a tracker's announced alerts once the printer recovers, so a later,
    genuinely new occurrence of the same condition alerts again."""
    with _conn() as c:
        c.execute("UPDATE jobs SET alerts=NULL, updated_at=? WHERE id=?",
                  (time.time(), job_id))


def tracked_item_ids(group_id=None):
    """Bambuddy queue_item_ids the bot already has a tracker for (any stage), so a
    command doesn't double-adopt a job it already watches. Global across all groups
    by default (used by !sync); scoped to one group when ``group_id`` is given (used
    by !abo, so each group can independently subscribe to — and get notified for —
    the same print)."""
    q = "SELECT DISTINCT queue_item_id FROM jobs WHERE queue_item_id IS NOT NULL"
    params = ()
    if group_id is not None:
        q += " AND group_id=?"
        params = (group_id,)
    with _conn() as c:
        rows = c.execute(q, params).fetchall()
        return {r["queue_item_id"] for r in rows}


def subscribed_item_ids(group_id):
    """queue_item_ids this group will *actually* still be notified about — only
    live trackers ('queued'/'printing').

    Distinct from tracked_item_ids on purpose: that one is stage-agnostic because
    a muted ('cancelled') row has to keep counting as tracked, or the standing-abo
    pass would re-adopt what the user just stopped. But for display, a muted or
    finished row is *not* a subscription — showing 🔔 for it told the user their
    !abo stop hadn't worked."""
    with _conn() as c:
        rows = c.execute(
            "SELECT DISTINCT queue_item_id FROM jobs WHERE group_id=? "
            "AND queue_item_id IS NOT NULL AND stage IN ('queued','printing')",
            (group_id,),
        ).fetchall()
        return {r["queue_item_id"] for r in rows}


def untrack_items(group_id, item_ids):
    """Mute a group's active trackers for the given Bambuddy queue_item_ids (used
    by !abo stop <n>). Only mutes notifications for this group — the print itself
    is untouched. Returns how many trackers were muted.

    Muted rather than deleted: with a standing subscription on, a deleted tracker
    is simply re-adopted on the next poll, silently undoing the user's !abo stop.
    'cancelled' is terminal (so it stops notifying) but still counts as tracked,
    which is exactly what makes the auto-adopt pass skip it."""
    if not item_ids:
        return 0
    qs = ",".join("?" * len(item_ids))
    with _conn() as c:
        cur = c.execute(
            f"UPDATE jobs SET stage='cancelled', updated_at=? "
            f"WHERE group_id=? AND stage IN ('queued','printing') "
            f"AND queue_item_id IN ({qs})",
            (time.time(), group_id, *item_ids),
        )
        return cur.rowcount


def untrack_all(group_id):
    """Mute all of a group's active trackers (used by !abo stop / !deabo) so it
    stops getting start/finish notifications. The prints themselves keep running.
    Returns how many trackers were muted. See untrack_items on why muted, not
    deleted."""
    with _conn() as c:
        cur = c.execute(
            "UPDATE jobs SET stage='cancelled', updated_at=? "
            "WHERE group_id=? AND stage IN ('queued','printing')",
            (time.time(), group_id),
        )
        return cur.rowcount


def revive_trackers(group_id, item_ids, printing_item_ids=()):
    """Un-mute trackers a group earlier stopped, so it can re-subscribe to a print
    it silenced. Returns how many were revived.

    ``printing_item_ids`` are the ones already running on the machine; they come
    back as 'printing', not 'queued'. Reviving a mid-print job to 'queued' makes
    the completion poller read the next tick as a queued→printing transition and
    announce "druckt jetzt los" for a print that started hours ago — the same
    trap _adopt_item avoids for freshly adopted items."""
    if not item_ids:
        return 0
    now = time.time()
    printing = set(printing_item_ids or ())
    revived = 0
    with _conn() as c:
        for iid in item_ids:
            cur = c.execute(
                "UPDATE jobs SET stage=?, updated_at=? "
                "WHERE group_id=? AND stage='cancelled' AND queue_item_id=?",
                ("printing" if iid in printing else "queued", now, group_id, iid),
            )
            revived += cur.rowcount
    return revived


def queued_jobs_with_item():
    """Trackers with a Bambuddy item id — watched for start ('queued') and for
    finish ('printing', already announced as started)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM jobs WHERE stage IN ('queued','printing') "
            "AND queue_item_id IS NOT NULL"
        ).fetchall()
        return [dict(r) for r in rows]
