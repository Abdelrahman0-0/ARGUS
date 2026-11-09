"""
browser_history.py
Reads browsing history (visited URLs + page titles) from Chrome, Edge,
and Firefox.

Two things were causing activity to "go missing" in the old version and
are fixed here:

1. Only the "Default" profile was checked. If you use a different
   Chrome/Edge profile (Profile 1, Profile 2, ...), none of its history
   was ever read. Now every profile folder is scanned.
2. The history database is exclusively locked while the browser is
   running, and copying it could silently fail while the browser was
   busy writing to it - so entire check cycles could be skipped with no
   error shown. Now it opens the database directly in SQLite's
   "immutable" read-only mode (a documented trick for reading a locked
   SQLite file without needing a writable copy at all), and only falls
   back to copying if that isn't possible.

Note: private/incognito windows are never written to the browser's
history database by the browser itself - that's a deliberate privacy
feature of the browser, not something readable from this file at all.
"""
import os
import shutil
import sqlite3
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path

from paths import LOG_DIR

LOG_FILE = LOG_DIR / "browser_activity.log"

LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")
APPDATA = os.environ.get("APPDATA", "")

CHROMIUM_ROOTS = {
    "Chrome": Path(LOCALAPPDATA) / "Google/Chrome/User Data",
    "Edge": Path(LOCALAPPDATA) / "Microsoft/Edge/User Data",
}

FIREFOX_PROFILES_DIR = Path(APPDATA) / "Mozilla/Firefox/Profiles"

# Chrome/Edge store timestamps as microseconds since 1601-01-01
CHROME_EPOCH = datetime(1601, 1, 1)

# remembers, per database file, the newest visit time we've already
# logged - so the same visit is never written to the log twice even
# though each check re-reads the last N minutes
_last_seen = {}
_last_seen_lock = threading.Lock()


def _chrome_time_to_datetime(chrome_time: int) -> datetime:
    return CHROME_EPOCH + timedelta(microseconds=chrome_time)


def _discover_chromium_profiles(root: Path):
    """Yields (profile_label, History db path) for every profile folder
    under a Chrome/Edge "User Data" directory, not just Default."""
    if not root.exists():
        return
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        if entry.name == "Default" or entry.name.startswith("Profile "):
            db_path = entry / "History"
            if db_path.exists():
                yield entry.name, db_path


def _open_readonly(db_path: Path):
    """Opens the SQLite file directly in immutable/read-only mode, which
    works even while the browser has it open. Falls back to copying the
    file first if that URI mode isn't usable for some reason."""
    try:
        uri = f"file:{db_path.as_posix()}?immutable=1"
        conn = sqlite3.connect(uri, uri=True)
        conn.execute("SELECT 1")  # force it to actually touch the file now
        return conn, None
    except Exception:
        pass

    # fallback: copy then open normally
    if not db_path.exists():
        return None, None
    tmp = Path(tempfile.gettempdir()) / f"tmp_{db_path.name}_{os.getpid()}_{id(db_path)}.db"
    try:
        shutil.copy2(db_path, tmp)
        conn = sqlite3.connect(tmp)
        return conn, tmp
    except Exception:
        return None, None


def _newest_seen(db_key: str):
    with _last_seen_lock:
        return _last_seen.get(db_key)


def _update_seen(db_key: str, newest: datetime):
    with _last_seen_lock:
        if db_key not in _last_seen or newest > _last_seen[db_key]:
            _last_seen[db_key] = newest


def read_chromium_history(browser_name: str, profile_label: str, db_path: Path,
                           since_minutes: int = 60):
    db_key = str(db_path)
    conn, tmp_file = _open_readonly(db_path)
    if conn is None:
        return []

    results = []
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT url, title, last_visit_time FROM urls "
            "ORDER BY last_visit_time DESC LIMIT 500"
        )
        cutoff = datetime.now() - timedelta(minutes=since_minutes)
        already_seen = _newest_seen(db_key)
        newest_this_run = already_seen
        label = f"{browser_name} ({profile_label})" if profile_label != "Default" else browser_name

        for url, title, last_visit_time in cursor.fetchall():
            visited_at = _chrome_time_to_datetime(last_visit_time)
            if visited_at < cutoff:
                break
            if already_seen and visited_at <= already_seen:
                continue
            results.append((label, visited_at, url, title))
            if newest_this_run is None or visited_at > newest_this_run:
                newest_this_run = visited_at

        if newest_this_run:
            _update_seen(db_key, newest_this_run)
    except Exception as e:
        results.append((browser_name, datetime.now(), "ERROR", str(e)))
    finally:
        conn.close()
        if tmp_file:
            try:
                tmp_file.unlink()
            except Exception:
                pass

    return results


def read_firefox_history(since_minutes: int = 60):
    results = []
    if not FIREFOX_PROFILES_DIR.exists():
        return results

    for profile in FIREFOX_PROFILES_DIR.glob("*"):
        db_path = profile / "places.sqlite"
        if not db_path.exists():
            continue

        db_key = str(db_path)
        conn, tmp_file = _open_readonly(db_path)
        if conn is None:
            continue

        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT url, title, last_visit_date
                FROM moz_places
                WHERE last_visit_date IS NOT NULL
                ORDER BY last_visit_date DESC LIMIT 500
                """
            )
            cutoff = datetime.now() - timedelta(minutes=since_minutes)
            already_seen = _newest_seen(db_key)
            newest_this_run = already_seen

            for url, title, last_visit_date in cursor.fetchall():
                # Firefox stores microseconds since the regular 1970 epoch
                visited_at = datetime(1970, 1, 1) + timedelta(microseconds=last_visit_date)
                if visited_at < cutoff:
                    break
                if already_seen and visited_at <= already_seen:
                    continue
                results.append(("Firefox", visited_at, url, title))
                if newest_this_run is None or visited_at > newest_this_run:
                    newest_this_run = visited_at

            if newest_this_run:
                _update_seen(db_key, newest_this_run)
        except Exception as e:
            results.append(("Firefox", datetime.now(), "ERROR", str(e)))
        finally:
            conn.close()
            if tmp_file:
                try:
                    tmp_file.unlink()
                except Exception:
                    pass

    return results


def log_entries(entries):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        for browser_name, visited_at, url, title in sorted(entries, key=lambda e: e[1]):
            ts = visited_at.strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{ts}] BROWSER={browser_name} | URL={url} | TITLE={title}\n")


def run_once(since_minutes: int = 60, on_event=None):
    all_entries = []
    for name, root in CHROMIUM_ROOTS.items():
        for profile_label, db_path in _discover_chromium_profiles(root):
            all_entries.extend(
                read_chromium_history(name, profile_label, db_path, since_minutes)
            )
    all_entries.extend(read_firefox_history(since_minutes))

    if all_entries:
        log_entries(all_entries)
        if on_event:
            for browser_name, visited_at, url, title in all_entries:
                try:
                    on_event(browser_name, url, title, visited_at)
                except Exception:
                    pass
    print(f"[browser_history] Logged {len(all_entries)} entries to {LOG_FILE}")
    return len(all_entries)


def run(stop_event=None, interval_seconds: int = 60, on_event=None):
    """Periodically checks browser history until stop_event is set."""
    stop_event = stop_event or threading.Event()

    while not stop_event.is_set():
        try:
            # since_minutes is a safety window in case a check cycle was
            # delayed; the _last_seen tracking is what actually prevents
            # duplicate log lines
            run_once(since_minutes=15, on_event=on_event)
        except Exception as e:
            print(f"[browser_history] error: {e}")
        stop_event.wait(interval_seconds)

    print("[browser_history] Stopped.")


if __name__ == "__main__":
    run_once()
