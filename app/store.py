"""Store SQLite: enrollments, touches (toques programados), claims atomicos."""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS enrollments (
    id TEXT PRIMARY KEY,
    sequence TEXT NOT NULL,
    email TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    demo_at TEXT,
    data_json TEXT NOT NULL DEFAULT '{}',
    suppressed INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_enroll_email ON enrollments(email);
CREATE TABLE IF NOT EXISTS touches (
    id TEXT PRIMARY KEY,
    enrollment_id TEXT NOT NULL REFERENCES enrollments(id),
    step_key TEXT NOT NULL,
    scheduled_for TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    claimed_by TEXT,
    claimed_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    last_error TEXT,
    sent_at TEXT,
    note TEXT,
    UNIQUE (enrollment_id, step_key)
);
CREATE INDEX IF NOT EXISTS idx_touch_due ON touches(status, scheduled_for);
"""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def create_enrollment(conn, *, sequence, email, name, demo_at, data,
                      idempotency_key):
    eid = "enr_" + uuid.uuid4().hex[:12]
    now = utcnow_iso()
    conn.execute(
        """INSERT INTO enrollments
           (id, sequence, email, name, demo_at, data_json, idempotency_key,
            created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (eid, sequence, email.strip().lower(), name or "", demo_at,
         json.dumps(data or {}), idempotency_key, now, now),
    )
    return eid


def find_enrollment_by_key(conn, idempotency_key):
    row = conn.execute(
        "SELECT * FROM enrollments WHERE idempotency_key=?",
        (idempotency_key,),
    ).fetchone()
    return dict(row) if row else None


def get_enrollment(conn, enrollment_id):
    row = conn.execute(
        "SELECT * FROM enrollments WHERE id=?", (enrollment_id,)
    ).fetchone()
    return dict(row) if row else None


def suppress_enrollment(conn, enrollment_id) -> bool:
    """Supresion v1: flag + cancela toques pendientes/claimed. Manual/admin."""
    cur = conn.execute(
        "UPDATE enrollments SET suppressed=1, updated_at=? WHERE id=?",
        (utcnow_iso(), enrollment_id),
    )
    if cur.rowcount == 0:
        return False
    conn.execute(
        """UPDATE touches SET status='cancelled', note='suppressed'
           WHERE enrollment_id=? AND status IN ('pending','claimed')""",
        (enrollment_id,),
    )
    return True


def suppress_by_email(conn, email) -> int:
    rows = conn.execute(
        "SELECT id FROM enrollments WHERE email=? AND suppressed=0",
        (email.strip().lower(),),
    ).fetchall()
    for r in rows:
        suppress_enrollment(conn, r["id"])
    return len(rows)


def add_touch(conn, *, enrollment_id, step_key, scheduled_for):
    tid = "tch_" + uuid.uuid4().hex[:12]
    conn.execute(
        """INSERT INTO touches (id, enrollment_id, step_key, scheduled_for)
           VALUES (?,?,?,?)""",
        (tid, enrollment_id, step_key, scheduled_for),
    )
    return tid


def claim_due_touches(conn, *, now_iso, token, limit=50):
    """Claim atomico: solo un worker puede tomar cada toque.

    El UPDATE ... WHERE status='pending' es atomico en SQLite; dos
    workers corriendo tick() a la vez no pueden reclamar lo mismo.
    """
    conn.execute(
        """UPDATE touches
           SET status='claimed', claimed_by=?, claimed_at=?
           WHERE id IN (
               SELECT t.id FROM touches t
               JOIN enrollments e ON e.id = t.enrollment_id
               WHERE t.status='pending'
                 AND e.suppressed=0
                 AND t.scheduled_for <= ?
                 AND (t.next_attempt_at IS NULL OR t.next_attempt_at <= ?)
               ORDER BY t.scheduled_for ASC
               LIMIT ?
           )""",
        (token, now_iso, now_iso, now_iso, limit),
    )
    rows = conn.execute(
        """SELECT t.*, e.email, e.name, e.sequence, e.demo_at, e.data_json
           FROM touches t JOIN enrollments e ON e.id = t.enrollment_id
           WHERE t.claimed_by=? AND t.status='claimed'""",
        (token,),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_sent(conn, touch_id, *, note=None):
    conn.execute(
        """UPDATE touches
           SET status='sent', sent_at=?, note=?
           WHERE id=? AND status='claimed'""",
        (utcnow_iso(), note, touch_id),
    )


def mark_failed(conn, touch_id, *, error, next_attempt_at):
    conn.execute(
        """UPDATE touches
           SET status='pending', attempts=attempts+1,
               next_attempt_at=?, last_error=?
           WHERE id=? AND status='claimed'""",
        (next_attempt_at, error[:500], touch_id),
    )


def mark_dead(conn, touch_id, *, error):
    conn.execute(
        """UPDATE touches SET status='failed', last_error=?
           WHERE id=? AND status='claimed'""",
        (error[:500], touch_id),
    )


def recover_stale_claims(conn, *, now_iso, stale_after_seconds):
    """Si un worker murio con claims abiertos, se liberan (reintento)."""
    from datetime import timedelta
    cutoff = (datetime.fromisoformat(now_iso)
              - timedelta(seconds=stale_after_seconds)).isoformat()
    cur = conn.execute(
        """UPDATE touches
           SET status='pending', claimed_by=NULL, claimed_at=NULL,
               attempts=attempts+1, last_error='stale claim recovered'
           WHERE status='claimed' AND claimed_at < ?""",
        (cutoff,),
    )
    return cur.rowcount


def counts(conn):
    out = {}
    for (status,) in conn.execute(
            "SELECT DISTINCT status FROM touches").fetchall():
        out[status] = conn.execute(
            "SELECT COUNT(*) FROM touches WHERE status=?",
            (status,)).fetchone()[0]
    out["enrollments"] = conn.execute(
        "SELECT COUNT(*) FROM enrollments").fetchone()[0]
    out["suppressed"] = conn.execute(
        "SELECT COUNT(*) FROM enrollments WHERE suppressed=1").fetchone()[0]
    return out


def recent_touches(conn, limit=20):
    rows = conn.execute(
        """SELECT t.id, t.step_key, t.status, t.scheduled_for, t.sent_at,
                  t.attempts, e.email, e.sequence
           FROM touches t JOIN enrollments e ON e.id=t.enrollment_id
           ORDER BY t.scheduled_for DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
