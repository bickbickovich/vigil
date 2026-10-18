#!/usr/bin/env python3
"""
Vigil tracker.

Polls the frontmost app (and, for browsers, the active tab's URL), classifies
it as productive / distracting / neutral, and logs everything to a local
SQLite database. Enforces two kinds of blocking: self-locks (indefinite,
opt-in, cooldown to undo) and focus sessions (timed, blocks the whole
distracting list).

Meant to be visible, not covert: every action fires a notification
explaining what happened, and all activity is readable via the dashboard.
"""
import json
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import focus_session
import selfblocks
import settings_store

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
DB_PATH = BASE_DIR / "data" / "activity.db"


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
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
    conn.row_factory = sqlite3.Row
    return conn


def run_osascript(script):
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            print(f"[osascript failed rc={result.returncode}] {script!r} -> {result.stderr.strip()}", file=sys.stderr)
        return result.stdout.strip()
    except Exception as e:
        print(f"[osascript exception] {script!r} -> {e}", file=sys.stderr)
        return ""


def get_frontmost_app():
    script = (
        'tell application "System Events" to get name of first application '
        'process whose frontmost is true'
    )
    name = run_osascript(script)
    return name or None


def get_browser_url(app_name):
    scripts = {
        "Safari": 'tell application "Safari" to get URL of front document',
        "Google Chrome": 'tell application "Google Chrome" to get URL of active tab of front window',
        "Arc": 'tell application "Arc" to get URL of active tab of front window',
        "Brave Browser": 'tell application "Brave Browser" to get URL of active tab of front window',
        "Microsoft Edge": 'tell application "Microsoft Edge" to get URL of active tab of front window',
    }
    script = scripts.get(app_name)
    if not script:
        return None
    url = run_osascript(script)
    return url or None


def extract_domain(url):
    if not url:
        return None
    try:
        netloc = urlparse(url).netloc.lower()
        netloc = netloc.rsplit("@", 1)[-1]
        netloc = netloc.split(":", 1)[0]
        netloc = re.sub(r"^www\.", "", netloc)
        return netloc or None
    except Exception:
        return None


def domain_matches(domain, domain_list):
    if not domain:
        return False
    return any(domain == d or domain.endswith("." + d) for d in domain_list)


def classify(app, domain, categories):
    productive = categories.get("productive", {})
    distracting = categories.get("distracting", {})

    if domain_matches(domain, distracting.get("domains", [])):
        return "distracting"
    if domain_matches(domain, productive.get("domains", [])):
        return "productive"
    if app in distracting.get("apps", []):
        return "distracting"
    if app in productive.get("apps", []):
        return "productive"
    return "neutral"


def notify(title, message, sound=True):
    safe_title = title.replace('"', '\\"')
    safe_message = message.replace('"', '\\"')
    script = f'display notification "{safe_message}" with title "{safe_title}"'
    if sound:
        script += ' sound name "Glass"'
    run_osascript(script)


def quit_app(app_name):
    run_osascript(f'tell application "{app_name}" to quit')


def redirect_browser(app_name, url):
    scripts = {
        "Safari": f'tell application "Safari" to set URL of front document to "{url}"',
        "Google Chrome": f'tell application "Google Chrome" to set URL of active tab of front window to "{url}"',
        "Arc": f'tell application "Arc" to set URL of active tab of front window to "{url}"',
        "Brave Browser": f'tell application "Brave Browser" to set URL of active tab of front window to "{url}"',
        "Microsoft Edge": f'tell application "Microsoft Edge" to set URL of active tab of front window to "{url}"',
    }
    script = scripts.get(app_name)
    if script:
        run_osascript(script)


def log_event(conn, app, domain, category):
    conn.execute(
        "INSERT INTO activity_events (ts, app, domain, category) VALUES (?, ?, ?, ?)",
        (datetime.now().isoformat(timespec="seconds"), app, domain, category),
    )
    conn.commit()


def log_intervention(conn, kind, app, domain, message):
    conn.execute(
        "INSERT INTO interventions (ts, kind, app, domain, message) VALUES (?, ?, ?, ?, ?)",
        (datetime.now().isoformat(timespec="seconds"), kind, app, domain, message),
    )
    conn.commit()


def main():
    config = load_config()
    conn = init_db()

    self_block_last_notified = {}
    focus_block_last_notified = {}
    consecutive_no_app_polls = 0

    poll = config.get("poll_interval_seconds", 5)
    cooldown = config.get("early_unlock_cooldown_seconds", 300)

    print(f"Vigil tracker started. Logging to {DB_PATH}")

    while True:
        try:
            now = datetime.now()
            app = get_frontmost_app()
            domain = None
            if app in config.get("browsers", []):
                url = get_browser_url(app)
                domain = extract_domain(url)

            if app:
                consecutive_no_app_polls = 0
            else:
                consecutive_no_app_polls += 1
                if consecutive_no_app_polls * poll >= 30:
                    today_str = now.strftime("%Y-%m-%d")
                    if settings_store.get(conn, "last_detection_alert_date") != today_str:
                        msg = ("Vigil can't see the active app or website anymore — it's running but blind, "
                               "so nothing is being tracked or enforced. Check System Settings > Privacy & "
                               "Security > Automation and make sure python3 can control System Events and "
                               "your browser.")
                        notify("Vigil — needs attention", msg)
                        log_intervention(conn, "tracker_health", None, None, msg)
                        settings_store.set(conn, "last_detection_alert_date", today_str)

            category = classify(app, domain, config["categories"])

            selfblocks.cleanup_expired(conn, cooldown, now)

            self_blocked_now = False
            for row in conn.execute("SELECT * FROM self_blocks").fetchall():
                if not selfblocks.is_active(row, now, cooldown):
                    continue
                if not selfblocks.target_matches(row, app, domain):
                    continue
                self_blocked_now = True
                if app in config.get("browsers", []) and row["target_type"] == "domain":
                    redirect_browser(app, config.get("redirect_url_on_block"))
                else:
                    quit_app(app)
                notify_key = (row["target"], row["target_type"])
                last_notified = self_block_last_notified.get(notify_key, 0)
                if time.time() - last_notified >= 60:
                    unlock_dt = selfblocks.unlocks_at(row, cooldown)
                    if unlock_dt:
                        msg = f"{row['target']} is locked (you set this yourself) — unlocking at {unlock_dt.strftime('%-I:%M %p')}."
                    else:
                        msg = f"{row['target']} is locked (you set this yourself)."
                    notify("Vigil — self-lock", msg)
                    log_intervention(conn, "self_block", app, domain, msg)
                    self_block_last_notified[notify_key] = time.time()
                break

            if self_blocked_now:
                time.sleep(poll)
                continue

            session = focus_session.active_session(conn, cooldown, now)
            if session:
                start_flag_key = f"focus_start_{session['started_at']}"
                if settings_store.get(conn, start_flag_key) != "1":
                    end_label = focus_session.effective_end(session, cooldown).strftime("%-I:%M %p")
                    msg = f"Focus session started — distracting apps/sites are blocked until {end_label}."
                    notify("Vigil — focus session", msg)
                    log_intervention(conn, "focus_session_start", None, None, msg)
                    settings_store.set(conn, start_flag_key, "1")

                if app and category == "distracting":
                    target = domain or app
                    if app in config.get("browsers", []) and domain:
                        redirect_browser(app, config.get("redirect_url_on_block"))
                    else:
                        quit_app(app)
                    notify_key = ("focus_session", target)
                    last_notified = focus_block_last_notified.get(notify_key, 0)
                    if time.time() - last_notified >= 60:
                        end_label = focus_session.effective_end(session, cooldown).strftime("%-I:%M %p")
                        msg = f"{target} is closed — focus session is on until {end_label}."
                        notify("Vigil — focus session", msg)
                        log_intervention(conn, "focus_session_block", app, domain, msg)
                        focus_block_last_notified[notify_key] = time.time()

                time.sleep(poll)
                continue

            if settings_store.is_paused(conn):
                time.sleep(poll)
                continue

            log_event(conn, app, domain, category)

        except Exception as e:
            print(f"[tracker error] {e}")

        time.sleep(poll)


if __name__ == "__main__":
    main()
