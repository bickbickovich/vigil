"""
Self-locks: a commitment device you can set on yourself.

Each row is a simple persistent toggle for one target (an app name or a
website domain): locked until turned off. Turning it off doesn't unlock
immediately — it starts a cooldown (default 5 min), so a moment of
temptation can't instantly undo the commitment. Turning it back on during
that cooldown cancels the pending unlock.

Enforced at all times, independent of pause/focus sessions, because it's
opt-in.
"""
from datetime import datetime, timedelta

SELF_BLOCKS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS self_blocks (
    target TEXT NOT NULL,
    target_type TEXT NOT NULL,
    locked_at TEXT NOT NULL,
    disable_requested_at TEXT,
    PRIMARY KEY (target, target_type)
)
"""


def ensure_table(conn):
    conn.execute(SELF_BLOCKS_TABLE_SQL)
    conn.commit()


def _parse(ts):
    return datetime.fromisoformat(ts)


def unlocks_at(row, cooldown_seconds):
    if not row["disable_requested_at"]:
        return None
    return _parse(row["disable_requested_at"]) + timedelta(seconds=cooldown_seconds)


def is_active(row, now, cooldown_seconds):
    ua = unlocks_at(row, cooldown_seconds)
    return ua is None or now < ua


def target_matches(row, app, domain):
    if row["target_type"] == "domain":
        if not domain:
            return False
        t = row["target"].lower()
        d = domain.lower()
        return d == t or d.endswith("." + t)
    return app is not None and app == row["target"]


def cleanup_expired(conn, cooldown_seconds, now=None):
    now = now or datetime.now()
    rows = conn.execute("SELECT rowid, * FROM self_blocks").fetchall()
    for row in rows:
        if not is_active(row, now, cooldown_seconds):
            conn.execute(
                "DELETE FROM self_blocks WHERE target = ? AND target_type = ?",
                (row["target"], row["target_type"]),
            )
    conn.commit()
