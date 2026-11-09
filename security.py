"""
security.py
Password hashing (scrypt) and brute-force lockout, shared by the desktop
app (away-mode password) and the phone server (remote password).
"""
import hashlib
import hmac
import os
import threading
import time

_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password: str) -> dict:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return {"algo": "scrypt", "n": _N, "r": _R, "p": _P,
            "salt": salt.hex(), "hash": digest.hex()}


def verify_password(password: str, record: dict) -> bool:
    if not record or not record.get("hash"):
        return False
    try:
        salt = bytes.fromhex(record["salt"])
        expected = bytes.fromhex(record["hash"])
        if record.get("algo") == "scrypt":
            got = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                                 n=record["n"], r=record["r"], p=record["p"],
                                 dklen=len(expected))
        else:  # legacy: sha256(salt_hex + password), from older ARGUS versions
            got = hashlib.sha256((record["salt"] + password).encode("utf-8")).digest()
        return hmac.compare_digest(got, expected)
    except Exception:
        return False


def is_legacy(record: dict) -> bool:
    return bool(record) and record.get("algo") != "scrypt"


class Lockout:
    """After `max_fails` wrong attempts for a key, refuse that key for
    `lock_seconds`. Wrong attempts are counted per key; success resets."""

    def __init__(self, max_fails=5, lock_seconds=300):
        self.max_fails, self.lock_seconds = max_fails, lock_seconds
        self._fails = {}
        self._until = {}
        self._lock = threading.Lock()

    def remaining(self, key="default") -> int:
        with self._lock:
            return max(0, int(self._until.get(key, 0) - time.time()))

    def fail(self, key="default"):
        with self._lock:
            n = self._fails.get(key, 0) + 1
            self._fails[key] = n
            if n >= self.max_fails:
                self._until[key] = time.time() + self.lock_seconds
                self._fails[key] = 0

    def ok(self, key="default"):
        with self._lock:
            self._fails.pop(key, None)
            self._until.pop(key, None)
