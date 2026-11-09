"""
away_mode.py
"I'm leaving" / "I'm back" workflow, v2.

What changed from v1:
- Passwords use scrypt (old SHA-256 hashes still work and are upgraded
  the first time they're used) and wrong attempts lock the prompt.
- Alerts mean "a person touched the device": keyboard/mouse input is the
  trigger. App switches and browser visits only become alerts when real
  input happened just before them, so background title changes (e.g. an
  unread counter in a tab title) no longer cause false alarms.
- ARGUS's own windows never raise app-switch alerts.
- A short grace period after arming lets you finish and walk away.
- The armed state survives a restart. If ARGUS comes back up armed
  without having been quit with the password, that itself is an alert.
- Listeners (desktop table, phone push) subscribe via add_listener().
"""
import json
import os
import re
import sys
import threading
import time
from datetime import datetime

import security
from paths import APP_DIR, LOG_DIR

CONFIG_FILE = APP_DIR / "away_config.json"
STATE_FILE = APP_DIR / "away_state.json"
ALERTS_LOG = LOG_DIR / "away_alerts.log"

GRACE_SECONDS = 15           # after arming, ignore everything (you're leaving)
INPUT_SESSION_GAP = 30       # idle seconds before new input counts as a new visit
APP_SWITCH_INPUT_WINDOW = 5  # app switch counts only if input within N seconds
BROWSER_INPUT_WINDOW = 120   # browser visit counts only if input within N seconds
DEDUP_SECONDS = 3
MAX_ALERTS = 200

OWN_PROCESS = os.path.basename(sys.executable).lower()
_LINE = re.compile(r"^\[(.+?)\] (\w+) \| (.*)$")


def _write_json(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


class AwayMode:
    def __init__(self):
        self._lock = threading.Lock()
        self._listeners = []
        self.armed = False
        self.armed_at = None
        self._grace_until = 0.0
        self._last_input = 0.0
        self._last_alert = {}
        self._pw = None
        self._restored_unclean = False
        self.alerts = []
        self.lockout = security.Lockout(max_fails=5, lock_seconds=300)
        self._load_config()
        self._load_state()
        self._load_alerts()

    # ---------------- persistence ----------------
    def _load_config(self):
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return
        if "password" in data:
            self._pw = data["password"]
        elif data.get("password_hash"):  # legacy v1 file
            self._pw = {"algo": "sha256", "salt": data.get("salt", ""),
                        "hash": data["password_hash"]}

    def _save_config(self):
        _write_json(CONFIG_FILE, {"password": self._pw})

    def _load_state(self):
        try:
            st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return
        if st.get("armed"):
            self.armed = True
            try:
                self.armed_at = datetime.fromisoformat(st.get("armed_at"))
            except Exception:
                self.armed_at = datetime.now()
            self._restored_unclean = not st.get("clean_exit", False)
        # from now on this run is "unclean" until quit_clean() is called
        self._save_state(clean_exit=False)

    def _save_state(self, clean_exit=False):
        try:
            _write_json(STATE_FILE, {
                "armed": self.armed,
                "armed_at": self.armed_at.isoformat() if self.armed_at else None,
                "clean_exit": clean_exit,
            })
        except Exception:
            pass

    def _load_alerts(self):
        try:
            lines = ALERTS_LOG.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            return
        for ln in lines[-MAX_ALERTS:]:
            m = _LINE.match(ln.strip())
            if m:
                self.alerts.append({"time": m.group(1), "kind": m.group(2), "detail": m.group(3)})

    def restore_state(self):
        """Call once after listeners are attached. Returns an alert if ARGUS
        came back up armed without being quit properly."""
        if self.armed and self._restored_unclean:
            self._grace_until = 0
            return self.report_event(
                "TAMPER", "ARGUS اتقفل أو الجهاز اتعمله restart وهو في وضع الحماية", force=True)
        return None

    def quit_clean(self):
        self._save_state(clean_exit=True)

    # ---------------- listeners ----------------
    def add_listener(self, fn):
        self._listeners.append(fn)

    # ---------------- password ----------------
    def has_password(self) -> bool:
        return bool(self._pw and self._pw.get("hash"))

    def set_password(self, new_password: str):
        self._pw = security.hash_password(new_password)
        self._save_config()

    def lockout_remaining(self) -> int:
        return self.lockout.remaining()

    def check_password(self, password: str) -> bool:
        if self.lockout.remaining():
            return False
        if security.verify_password(password, self._pw):
            self.lockout.ok()
            if security.is_legacy(self._pw):
                self.set_password(password)  # upgrade old SHA-256 hash
            return True
        self.lockout.fail()
        self.report_event("TAMPER", "حد دخل باسورد غلط", force=True)
        return False

    # ---------------- arming / disarming ----------------
    def arm(self):
        with self._lock:
            self.armed = True
            self.armed_at = datetime.now()
            self._grace_until = time.time() + GRACE_SECONDS
        self._save_state()

    def _clear_armed(self):
        with self._lock:
            self.armed = False
            self.armed_at = None
        self._save_state()

    def disarm(self, password: str) -> bool:
        if not self.check_password(password):
            return False
        self._clear_armed()
        return True

    def disarm_trusted(self):
        """For callers that already authenticated (the phone session)."""
        self._clear_armed()

    # ---------------- events ----------------
    def mark_input(self):
        """Called whenever the OS reports keyboard/mouse input."""
        now = time.time()
        with self._lock:
            gap = now - self._last_input
            self._last_input = now
        if self.armed and gap >= INPUT_SESSION_GAP:
            return self.report_event("INPUT", "حد بيستخدم الكيبورد أو الماوس على الجهاز", force=True)
        return None

    def report_event(self, kind: str, detail: str, force: bool = False):
        if not self.armed:
            return None
        now = time.time()
        if now < self._grace_until:
            return None
        if not force:
            if kind == "APP_SWITCH" and now - self._last_input > APP_SWITCH_INPUT_WINDOW:
                return None
            if kind == "BROWSER" and now - self._last_input > BROWSER_INPUT_WINDOW:
                return None
        key = (kind, detail)
        with self._lock:
            if now - self._last_alert.get(key, 0) < DEDUP_SECONDS:
                return None
            self._last_alert[key] = now
            entry = {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                     "kind": kind, "detail": detail}
            self.alerts.append(entry)
            del self.alerts[:-MAX_ALERTS]
        self._log_alert(entry)
        for fn in list(self._listeners):
            try:
                fn(entry)
            except Exception as e:
                print(f"[away] listener error: {e}")
        return entry

    def report_app_switch(self, process_name, title):
        if (process_name or "").lower() == OWN_PROCESS:
            return None
        return self.report_event("APP_SWITCH", f"{process_name} - {title}")

    def _log_alert(self, entry):
        try:
            with open(ALERTS_LOG, "a", encoding="utf-8") as f:
                f.write(f"[{entry['time']}] {entry['kind']} | {entry['detail']}\n")
        except Exception:
            pass

    def get_alerts(self, limit=None):
        with self._lock:
            out = list(self.alerts)
        return out[-limit:] if limit else out

    def clear_alerts(self):
        with self._lock:
            self.alerts.clear()
        try:
            open(ALERTS_LOG, "w", encoding="utf-8").close()
        except Exception:
            pass
