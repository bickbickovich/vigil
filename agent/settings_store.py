"""Tiny key/value settings store, shared by the tracker and dashboard."""

SETTINGS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""


def ensure_table(conn):
    conn.execute(SETTINGS_TABLE_SQL)
    conn.commit()


def get(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set(conn, key, value):
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def is_paused(conn):
    return get(conn, "paused", "0") == "1"
