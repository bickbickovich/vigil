#!/usr/bin/env python3
"""
Vigil dashboard — a local JSON API + static frontend.

No external dependencies (stdlib http.server + sqlite3), so it runs with
plain `python3 server.py`. The frontend (dashboard/static/) is a hand-built
single-page app that talks to the /api/* endpoints below.
"""
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "agent"))
import focus_session  # noqa: E402
import selfblocks  # noqa: E402
import settings_store  # noqa: E402

DB_PATH = BASE_DIR / "data" / "activity.db"
CONFIG_PATH = BASE_DIR / "agent" / "config.json"
STATIC_DIR = Path(__file__).resolve().parent / "static"
PORT = 8900


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def save_config(config):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")


def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS activity_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            app TEXT,
            domain TEXT,
            category TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS interventions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            kind TEXT NOT NULL,
            app TEXT,
            domain TEXT,
            message TEXT
        )"""
    )
    selfblocks.ensure_table(conn)
    settings_store.ensure_table(conn)
    focus_session.ensure_table(conn)
    conn.commit()
    return conn


def seconds_by_category(conn, day_start, day_end, poll_interval=5):
    rows = conn.execute(
        "SELECT category, COUNT(*) as n FROM activity_events WHERE ts >= ? AND ts < ? GROUP BY category",
        (day_start.isoformat(), day_end.isoformat()),
    ).fetchall()
    return {r["category"]: r["n"] * poll_interval for r in rows}


def build_state():
    config = load_config()
    now = datetime.now()
    cooldown = config.get("early_unlock_cooldown_seconds", 300)
    poll = config.get("poll_interval_seconds", 5)

    conn = get_conn()

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    today_cats = seconds_by_category(conn, today_start, today_end, poll)

    week = []
    for i in range(6, -1, -1):
        day_start = today_start - timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        cats = seconds_by_category(conn, day_start, day_end, poll)
        week.append({
            "date": day_start.strftime("%Y-%m-%d"),
            "label": day_start.strftime("%a"),
            "productive": cats.get("productive", 0),
            "distracting": cats.get("distracting", 0),
            "neutral": cats.get("neutral", 0),
        })

    paused = settings_store.is_paused(conn)
    since_row = conn.execute(
        "SELECT ts FROM interventions WHERE kind IN ('paused','resumed') ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    paused_since = since_row["ts"] if since_row else None

    selfblocks.cleanup_expired(conn, cooldown, now)
    existing = {(r["target"], r["target_type"]): r for r in conn.execute("SELECT * FROM self_blocks").fetchall()}
    candidates = [(a, "app") for a in config["categories"].get("distracting", {}).get("apps", [])]
    candidates += [(d, "domain") for d in config["categories"].get("distracting", {}).get("domains", [])]
    for key in existing:
        if key not in candidates:
            candidates.append(key)

    locks = []
    for target, target_type in candidates:
        row = existing.get((target, target_type))
        if row is None:
            locks.append({"target": target, "target_type": target_type, "status": "none", "unlocks_at": None})
        else:
            unlock_dt = selfblocks.unlocks_at(row, cooldown)
            locks.append({
                "target": target, "target_type": target_type,
                "status": "unlocking" if unlock_dt else "locked",
                "unlocks_at": unlock_dt.isoformat(timespec="seconds") if unlock_dt else None,
            })

    session_row = focus_session.active_session(conn, cooldown, now)
    session = None
    if session_row:
        session = {
            "started_at": session_row["started_at"],
            "planned_ends_at": session_row["ends_at"],
            "effective_ends_at": focus_session.effective_end(session_row, cooldown).isoformat(timespec="seconds"),
            "ending": session_row["end_requested_at"] is not None,
        }

    interventions = [
        {"ts": r["ts"], "kind": r["kind"], "app": r["app"], "domain": r["domain"], "message": r["message"]}
        for r in conn.execute("SELECT * FROM interventions ORDER BY id DESC LIMIT 30").fetchall()
    ]

    conn.close()

    return {
        "now": now.isoformat(timespec="seconds"),
        "paused": paused,
        "paused_since": paused_since,
        "today": {
            "productive": today_cats.get("productive", 0),
            "distracting": today_cats.get("distracting", 0),
            "neutral": today_cats.get("neutral", 0),
        },
        "week": week,
        "self_locks": locks,
        "focus_session": session,
        "interventions": interventions,
        "categories": config["categories"],
        "cooldown_seconds": cooldown,
    }


def log_intervention(conn, kind, app, domain, message):
    conn.execute(
        "INSERT INTO interventions (ts, kind, app, domain, message) VALUES (?, ?, ?, ?, ?)",
        (datetime.now().isoformat(timespec="seconds"), kind, app, domain, message),
    )
    conn.commit()


CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json",
}


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            return json.loads(body) if body else {}
        except json.JSONDecodeError:
            return {}

    def _serve_static(self, path):
        if path == "/":
            path = "/index.html"
        file_path = (STATIC_DIR / path.lstrip("/")).resolve()
        if STATIC_DIR not in file_path.parents and file_path != STATIC_DIR:
            self.send_response(403)
            self.end_headers()
            return
        if not file_path.is_file():
            self.send_response(404)
            self.end_headers()
            return
        content_type = CONTENT_TYPES.get(file_path.suffix, "application/octet-stream")
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/state":
            self._json(build_state())
            return
        self._serve_static(self.path)

    def do_POST(self):
        config = load_config()
        now = datetime.now()
        cooldown = config.get("early_unlock_cooldown_seconds", 300)
        data = self._read_json()
        conn = get_conn()

        if self.path == "/api/pause":
            paused = settings_store.is_paused(conn)
            new_state = not paused
            settings_store.set(conn, "paused", "1" if new_state else "0")
            log_intervention(conn, "paused" if new_state else "resumed", None, None,
                              "Tracking paused." if new_state else "Tracking resumed.")
            conn.close()
            self._json({"ok": True})
            return

        if self.path == "/api/lock":
            target = (data.get("target") or "").strip()
            target_type = data.get("target_type") if data.get("target_type") in ("domain", "app") else "domain"
            if not target:
                conn.close()
                self._json({"ok": False, "error": "missing target"}, 400)
                return
            selfblocks.cleanup_expired(conn, cooldown, now)
            row = conn.execute(
                "SELECT * FROM self_blocks WHERE target = ? AND target_type = ?", (target, target_type)
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO self_blocks (target, target_type, locked_at, disable_requested_at) VALUES (?, ?, ?, NULL)",
                    (target, target_type, now.isoformat(timespec="seconds")),
                )
                conn.commit()
                log_intervention(conn, "self_lock_created", target, None, f"Locked {target}.")
            elif row["disable_requested_at"] is None:
                conn.execute(
                    "UPDATE self_blocks SET disable_requested_at = ? WHERE target = ? AND target_type = ?",
                    (now.isoformat(timespec="seconds"), target, target_type),
                )
                conn.commit()
                log_intervention(conn, "self_lock_unlock_requested", target, None,
                                  f"Requested unlock of {target} — takes effect in {cooldown // 60} min.")
            else:
                conn.execute(
                    "UPDATE self_blocks SET disable_requested_at = NULL WHERE target = ? AND target_type = ?",
                    (target, target_type),
                )
                conn.commit()
                log_intervention(conn, "self_lock_relocked", target, None, f"Cancelled pending unlock of {target}.")
            conn.close()
            self._json({"ok": True})
            return

        if self.path == "/api/focus/start":
            try:
                minutes = int(data.get("minutes", 0))
            except (TypeError, ValueError):
                minutes = 0
            if minutes <= 0 or minutes > 480:
                conn.close()
                self._json({"ok": False, "error": "minutes must be 1-480"}, 400)
                return
            started = focus_session.start(conn, minutes, now)
            if started:
                end_label = (now + timedelta(minutes=minutes)).strftime("%-I:%M %p")
                log_intervention(conn, "focus_session_start", None, None,
                                  f"Focus session started for {minutes} min — distracting apps/sites blocked until {end_label}.")
            conn.close()
            self._json({"ok": started})
            return

        if self.path == "/api/focus/end_request":
            row = focus_session._row(conn)
            if row:
                focus_session.request_end(conn, now)
                log_intervention(conn, "focus_session_end_requested", None, None,
                                  f"Requested to end the focus session early — takes effect in {cooldown // 60} min.")
            conn.close()
            self._json({"ok": True})
            return

        if self.path == "/api/focus/cancel_end":
            row = focus_session._row(conn)
            if row and row["end_requested_at"] is not None:
                focus_session.cancel_end_request(conn)
                log_intervention(conn, "focus_session_resumed", None, None,
                                  "Cancelled the early end — focus session stays on.")
            conn.close()
            self._json({"ok": True})
            return

        if self.path == "/api/settings/categories":
            categories = data.get("categories")
            if not isinstance(categories, dict):
                conn.close()
                self._json({"ok": False, "error": "invalid categories"}, 400)
                return
            def clean_list(v):
                return sorted({str(x).strip() for x in v if str(x).strip()}) if isinstance(v, list) else []
            new_categories = {}
            for cat in ("productive", "distracting"):
                c = categories.get(cat, {})
                new_categories[cat] = {
                    "apps": clean_list(c.get("apps", [])),
                    "domains": clean_list(c.get("domains", [])),
                }
            config["categories"] = new_categories
            save_config(config)
            log_intervention(conn, "settings_updated", None, None, "Categories updated from the dashboard.")
            conn.close()
            self._json({"ok": True})
            return

        conn.close()
        self.send_response(404)
        self.end_headers()

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    get_conn().close()
    print(f"Vigil dashboard at http://localhost:{PORT}")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
