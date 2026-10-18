"""
Focus Session: a self-started, timed lock-in.

Pick a duration and start it — for that long, everything in the
`distracting` category is closed or redirected the moment it's opened, no
grace period. Unlike self-locks (which block one specific app/site
indefinitely), a session blocks the whole distracting list for a fixed
window you choose up front.

Ending a session early isn't instant: requesting an end starts the same
cooldown used for self-locks, so a moment of temptation can't undo it —
the session stays enforced until the cooldown elapses. A session also
always ends on its own once its planned duration is up, no action needed.

There's at most one session at a time; starting a new one while one is
already running is a no-op (end the current one first).
"""
from datetime import datetime, timedelta

FOCUS_SESSION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS focus_session (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    started_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    end_requested_at TEXT
)
"""


def ensure_table(conn):
    conn.execute(FOCUS_SESSION_TABLE_SQL)
    conn.commit()


def _row(conn):
    return conn.execute("SELECT * FROM focus_session WHERE id = 1").fetchone()


def effective_end(row, cooldown_seconds):
    planned = datetime.fromisoformat(row["ends_at"])
    if not row["end_requested_at"]:
        return planned
    cooldown_end = datetime.fromisoformat(row["end_requested_at"]) + timedelta(seconds=cooldown_seconds)
    return min(planned, cooldown_end)


def cleanup_expired(conn, cooldown_seconds, now=None):
    now = now or datetime.now()
    row = _row(conn)
    if row is not None and now >= effective_end(row, cooldown_seconds):
        conn.execute("DELETE FROM focus_session WHERE id = 1")
        conn.commit()


def active_session(conn, cooldown_seconds, now=None):
    now = now or datetime.now()
    cleanup_expired(conn, cooldown_seconds, now)
    return _row(conn)


def start(conn, minutes, now=None):
    now = now or datetime.now()
    if _row(conn) is not None:
        return False
    ends_at = now + timedelta(minutes=minutes)
    conn.execute(
        "INSERT INTO focus_session (id, started_at, ends_at, end_requested_at) VALUES (1, ?, ?, NULL)",
        (now.isoformat(timespec="seconds"), ends_at.isoformat(timespec="seconds")),
    )
    conn.commit()
    return True


def request_end(conn, now=None):
    now = now or datetime.now()
    row = _row(conn)
    if row is None or row["end_requested_at"] is not None:
        return
    conn.execute(
        "UPDATE focus_session SET end_requested_at = ? WHERE id = 1",
        (now.isoformat(timespec="seconds"),),
    )
    conn.commit()


def cancel_end_request(conn):
    conn.execute("UPDATE focus_session SET end_requested_at = NULL WHERE id = 1")
    conn.commit()
