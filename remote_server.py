"""
remote_server.py
Phone companion for ARGUS: a small web server (listens on 127.0.0.1 only)
that serves an installable web app (PWA) and a JSON API, plus Web Push
notifications so your phone buzzes the moment away mode catches someone.

How it reaches your phone: the server never listens on the network. You
publish it privately with Tailscale ("tailscale serve --bg <port>"), which
gives it HTTPS inside your own tailnet only - see README.

Security model
- Password (scrypt) -> random session token in an HttpOnly, SameSite=Strict
  cookie; only a hash of the token is stored on the PC.
- Wrong passwords are rate-limited per client and globally.
- Every POST needs a custom header and a same-origin Origin header (CSRF).
- Strict CSP; no third-party scripts, fonts or analytics.
- Every remote action is written to logs/remote_actions.log.
"""
import base64
import hashlib
import json
import logging
import os
import secrets
import socket
import threading
import time
from datetime import datetime
from functools import wraps
from urllib.parse import urlparse

from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.serving import make_server

import actions
import presence
import security
from paths import APP_DIR, LOG_DIR, RESOURCE_DIR

import updater
UI_DIR = updater.ui_dir()
CONFIG_FILE = APP_DIR / "remote_config.json"
VAPID_KEY_FILE = APP_DIR / "vapid_private.pem"
ACTION_LOG = LOG_DIR / "remote_actions.log"

DEFAULT_PORT = 8765
SESSION_DAYS = 30
MAX_SESSIONS = 10
MAX_SUBSCRIPTIONS = 10
PUSH_THROTTLE_SECONDS = 60          # for low-priority kinds (app switch/browser)
STATIC_FILES = {"app.js", "app.css", "sw.js", "manifest.webmanifest",
                "icon-192.png", "icon-512.png", "icon-badge.png"}

logging.getLogger("werkzeug").setLevel(logging.ERROR)
logging.raiseExceptions = False


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


# ----------------------------------------------------------------------
# Config (password, sessions, push subscriptions)
# ----------------------------------------------------------------------
class RemoteConfig:
    DEFAULTS = {"enabled": False, "port": DEFAULT_PORT, "webcam": False,
                "password": None, "sessions": {}, "subscriptions": []}

    def __init__(self):
        self._lock = threading.RLock()
        self.data = dict(self.DEFAULTS)
        try:
            self.data.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass

    def save(self):
        with self._lock:
            tmp = CONFIG_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data), encoding="utf-8")
            os.replace(tmp, CONFIG_FILE)

    def get(self, key):
        with self._lock:
            return self.data.get(key, self.DEFAULTS.get(key))

    def set(self, key, value):
        with self._lock:
            self.data[key] = value
            self.save()

    # sessions: only sha256(token) is stored
    def add_session(self) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            sess = {h: e for h, e in self.data["sessions"].items() if e > now}
            sess[hashlib.sha256(token.encode()).hexdigest()] = now + SESSION_DAYS * 86400
            if len(sess) > MAX_SESSIONS:
                for h in sorted(sess, key=sess.get)[:len(sess) - MAX_SESSIONS]:
                    del sess[h]
            self.data["sessions"] = sess
            self.save()
        return token

    def session_valid(self, token: str) -> bool:
        if not token:
            return False
        h = hashlib.sha256(token.encode()).hexdigest()
        with self._lock:
            return self.data["sessions"].get(h, 0) > time.time()

    def drop_session(self, token: str):
        with self._lock:
            self.data["sessions"].pop(hashlib.sha256((token or "").encode()).hexdigest(), None)
            self.save()

    def drop_all_sessions(self):
        self.set("sessions", {})


# ----------------------------------------------------------------------
# Web Push
# ----------------------------------------------------------------------
def vapid_public_key() -> str:
    """Creates the VAPID key pair on first use; returns the public key in the
    format browsers expect for pushManager.subscribe()."""
    from py_vapid import Vapid
    from cryptography.hazmat.primitives import serialization
    if not VAPID_KEY_FILE.exists():
        v = Vapid()
        v.generate_keys()
        v.save_key(str(VAPID_KEY_FILE))
    v = Vapid.from_file(str(VAPID_KEY_FILE))
    raw = v.public_key.public_bytes(serialization.Encoding.X962,
                                    serialization.PublicFormat.UncompressedPoint)
    return _b64url(raw)


class RemoteService:
    def __init__(self, away, status_fn=None):
        self.away = away
        self.status_fn = status_fn or (lambda: {})
        self.cfg = RemoteConfig()
        self.login_limit = security.Lockout(max_fails=5, lock_seconds=300)
        self.global_limit = security.Lockout(max_fails=30, lock_seconds=900)
        self._server = None
        self._thread = None
        self._last_low_push = 0.0
        self.last_push_error = ""
        self.app = self._build_app()

    # ---------------- lifecycle ----------------
    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def port(self) -> int:
        return int(self.cfg.get("port") or DEFAULT_PORT)

    def has_password(self) -> bool:
        return bool(self.cfg.get("password"))

    def set_password(self, pw: str):
        self.cfg.set("password", security.hash_password(pw))
        self.cfg.drop_all_sessions()       # changing it logs every phone out

    def start(self):
        if self.running:
            return
        if not self.has_password():
            raise RuntimeError("Set a phone password first.")
        self._server = make_server("127.0.0.1", self.port, self.app, threaded=True)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server = None
            self._thread = None

    # ---------------- notifications ----------------
    def notify_alert(self, entry: dict):
        """Away-mode listener: pushes an alert to every subscribed phone."""
        kind = entry.get("kind")
        now = time.time()
        if kind in ("APP_SWITCH", "BROWSER"):
            if now - self._last_low_push < PUSH_THROTTLE_SECONDS:
                return
            self._last_low_push = now
        titles = {"INPUT": "حد بيستخدم جهازك!", "TAMPER": "محاولة تلاعب بالجهاز!",
                  "APP_SWITCH": "نشاط على جهازك", "BROWSER": "نشاط في المتصفح"}
        self.notify(titles.get(kind, "تنبيه من ARGUS"), entry.get("detail", ""),
                    tag="argus-alert", urgent=True, alert=True)

    def notify(self, title, body, tag="argus", urgent=False, alert=False):
        payload = {"title": title, "body": body, "tag": tag,
                   "urgent": urgent, "alert": alert,
                   "time": datetime.now().strftime("%H:%M:%S")}
        threading.Thread(target=self._push_all, args=(payload,), daemon=True).start()

    def send_test(self) -> int:
        """Used by the desktop Settings page. Returns how many phones were targeted."""
        n = len(self.cfg.get("subscriptions") or [])
        if n:
            self.notify("تجربة من ARGUS", "الإشعارات شغالة.", tag="argus-test")
        return n

    def _push_all(self, payload):
        subs = list(self.cfg.get("subscriptions") or [])
        if not subs:
            return
        try:
            from pywebpush import webpush, WebPushException
            vapid_public_key()
        except Exception as e:
            self.last_push_error = f"pywebpush unavailable: {e}"
            return
        dead = []
        for sub in subs:
            try:
                webpush(subscription_info=sub,
                        data=json.dumps(payload, ensure_ascii=False),
                        vapid_private_key=str(VAPID_KEY_FILE),
                        vapid_claims={"sub": "mailto:argus.owner@example.com"},
                        ttl=3600, headers={"Urgency": "high"})
                self.last_push_error = ""
            except WebPushException as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status in (404, 410):
                    dead.append(sub)
                else:
                    self.last_push_error = str(e)[:200]
            except Exception as e:
                self.last_push_error = str(e)[:200]
        if dead:
            keep = [s for s in self.cfg.get("subscriptions") if s not in dead]
            self.cfg.set("subscriptions", keep)

    # ---------------- helpers ----------------
    @staticmethod
    def _client_key() -> str:
        fwd = request.headers.get("X-Forwarded-For", "")
        return (fwd.split(",")[0].strip() or request.remote_addr or "?")

    @staticmethod
    def _log_action(client, name, ok, detail=""):
        try:
            with open(ACTION_LOG, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {client} | {name} | "
                        f"{'OK' if ok else 'FAIL'} | {detail}\n")
        except Exception:
            pass

    def state(self) -> dict:
        away = self.away
        idle = presence.idle_seconds()
        st = {
            "host": socket.gethostname(),
            "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "armed": away.armed,
            "armed_at": away.armed_at.strftime("%Y-%m-%d %H:%M:%S") if away.armed_at else None,
            "idle": None if idle is None else int(idle),
            "alerts": away.get_alerts(40),
            "webcam": bool(self.cfg.get("webcam")) and actions.webcam_available(),
            "push_devices": len(self.cfg.get("subscriptions") or []),
        }
        st.update(self.status_fn() or {})
        return st

    # ---------------- Flask app ----------------
    def _build_app(self):
        app = Flask(__name__, static_folder=None)
        svc = self

        def authed():
            return svc.cfg.session_valid(request.cookies.get("argus_session", ""))

        def api(fn):
            @wraps(fn)
            def wrapper(*a, **kw):
                if request.method == "POST":
                    if request.headers.get("X-ARGUS") != "1":
                        return jsonify(error="bad request"), 400
                    origin = request.headers.get("Origin")
                    if origin and urlparse(origin).netloc != request.host:
                        return jsonify(error="bad origin"), 403
                if not authed():
                    return jsonify(error="login required"), 401
                return fn(*a, **kw)
            return wrapper

        @app.after_request
        def headers(resp):
            resp.headers["X-Content-Type-Options"] = "nosniff"
            resp.headers["Referrer-Policy"] = "no-referrer"
            resp.headers["X-Frame-Options"] = "DENY"
            resp.headers["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' blob: data:; "
                "style-src 'self'; script-src 'self'; connect-src 'self'; "
                "manifest-src 'self'; worker-src 'self'; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if request.path.startswith("/api/") or request.path in ("/", "/sw.js"):
                resp.headers["Cache-Control"] = "no-store"
            return resp

        @app.get("/")
        def index():
            return send_from_directory(UI_DIR, "index.html")

        @app.get("/<path:name>")
        def static_file(name):
            if name not in STATIC_FILES:
                return Response("not found", status=404)
            resp = send_from_directory(UI_DIR, name)
            if name == "sw.js":
                resp.headers["Service-Worker-Allowed"] = "/"
            return resp

        @app.post("/api/login")
        def login():
            if request.headers.get("X-ARGUS") != "1":
                return jsonify(error="bad request"), 400
            key = svc._client_key()
            wait = max(svc.login_limit.remaining(key), svc.global_limit.remaining("all"))
            if wait:
                return jsonify(error="locked", wait=wait), 429
            pw = (request.get_json(silent=True) or {}).get("password", "")
            if security.verify_password(str(pw), svc.cfg.get("password")):
                svc.login_limit.ok(key)
                token = svc.cfg.add_session()
                svc._log_action(key, "login", True)
                resp = jsonify(ok=True)
                https = request.is_secure or request.headers.get("X-Forwarded-Proto") == "https"
                resp.set_cookie("argus_session", token, max_age=SESSION_DAYS * 86400,
                                httponly=True, samesite="Strict", secure=https)
                return resp
            svc.login_limit.fail(key)
            svc.global_limit.fail("all")
            svc._log_action(key, "login", False)
            if svc.away.armed:
                svc.away.report_event("TAMPER", "حد حاول يدخل على تطبيق الموبايل بباسورد غلط", force=True)
            time.sleep(0.8)  # slow down guessing
            return jsonify(error="wrong password"), 401

        @app.post("/api/logout")
        @api
        def logout():
            svc.cfg.drop_session(request.cookies.get("argus_session", ""))
            resp = jsonify(ok=True)
            resp.delete_cookie("argus_session")
            return resp

        @app.get("/api/state")
        @api
        def get_state():
            return jsonify(svc.state())

        @app.get("/api/push/key")
        @api
        def push_key():
            try:
                return jsonify(key=vapid_public_key())
            except Exception as e:
                return jsonify(error=f"push unavailable: {e}"), 500

        @app.post("/api/push/subscribe")
        @api
        def push_subscribe():
            sub = (request.get_json(silent=True) or {}).get("subscription") or {}
            ep = sub.get("endpoint", "")
            keys = sub.get("keys") or {}
            if not (ep.startswith("https://") and keys.get("p256dh") and keys.get("auth")):
                return jsonify(error="invalid subscription"), 400
            subs = [s for s in svc.cfg.get("subscriptions") if s.get("endpoint") != ep]
            subs.append({"endpoint": ep, "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]}})
            svc.cfg.set("subscriptions", subs[-MAX_SUBSCRIPTIONS:])
            return jsonify(ok=True)

        @app.post("/api/push/unsubscribe")
        @api
        def push_unsubscribe():
            ep = (request.get_json(silent=True) or {}).get("endpoint", "")
            svc.cfg.set("subscriptions",
                        [s for s in svc.cfg.get("subscriptions") if s.get("endpoint") != ep])
            return jsonify(ok=True)

        @app.post("/api/push/test")
        @api
        def push_test():
            if not svc.cfg.get("subscriptions"):
                return jsonify(error="مفيش موبايل مفعّل عليه الإشعارات"), 400
            svc.notify("تجربة من ARGUS", "الإشعارات شغالة.", tag="argus-test")
            return jsonify(ok=True)

        @app.post("/api/action/<name>")
        @api
        def action(name):
            body = request.get_json(silent=True) or {}
            client = svc._client_key()
            try:
                result = svc._run_action(name, body)
            except actions.ActionError as e:
                svc._log_action(client, name, False, str(e))
                return jsonify(error=str(e)), 400
            except Exception as e:
                svc._log_action(client, name, False, repr(e))
                return jsonify(error="حصل خطأ غير متوقع"), 500
            svc._log_action(client, name, True)
            if isinstance(result, bytes):
                return Response(result, mimetype="image/jpeg",
                                headers={"Cache-Control": "no-store"})
            return jsonify(ok=True, **(result or {}))

        return app

    def _run_action(self, name, body):
        away = self.away
        if name == "arm":
            away.arm()
            return {"armed": True}
        if name == "disarm":
            away.disarm_trusted()
            return {"armed": False}
        if name == "lock":
            actions.lock_workstation()
            return {}
        if name == "siren":
            actions.siren(body.get("seconds", 20))
            return {}
        if name == "siren_stop":
            actions.siren_stop()
            return {}
        if name == "message":
            actions.show_message(str(body.get("text", "")))
            return {}
        if name == "screenshot":
            return actions.screenshot_jpeg()
        if name == "webcam":
            if not self.cfg.get("webcam"):
                raise actions.ActionError("الكاميرا مقفولة من إعدادات ARGUS على الكمبيوتر")
            return actions.webcam_jpeg()
        if name == "shutdown":
            if body.get("confirm") is not True:
                raise actions.ActionError("لازم تأكيد")
            actions.shutdown()
            return {}
        if name == "clear_alerts":
            away.clear_alerts()
            return {}
        if name == "phone_tamper":
            # a native guardian app on the phone itself reporting that
            # someone touched/moved/tried to unlock the phone while armed
            kind = str(body.get("kind", "PHONE"))[:40] or "PHONE"
            detail = str(body.get("detail", ""))[:200]
            away.report_external(kind, detail)
            return {}
        raise actions.ActionError("أمر غير معروف")
