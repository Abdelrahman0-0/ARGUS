"""
tailscale_helper.py
Small wrapper around the Tailscale CLI so ARGUS can publish the phone
server privately (HTTPS, only reachable from your own devices).
"""
import json
import os
import shutil
import subprocess
import sys

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def find_cli():
    exe = shutil.which("tailscale")
    if exe:
        return exe
    for base in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")):
        p = os.path.join(base, "Tailscale", "tailscale.exe")
        if base and os.path.exists(p):
            return p
    return None


def _run(args, timeout=25):
    exe = find_cli()
    if not exe:
        return 127, "Tailscale مش متسطب. نزّله من tailscale.com/download وسجّل دخول."
    try:
        p = subprocess.run([exe, *args], capture_output=True, text=True,
                           timeout=timeout, creationflags=_NO_WINDOW)
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        # `serve` can wait for you to enable HTTPS in the admin console
        return 124, "Tailscale مستني تأكيد. افتح اللينك اللي ظهر في نافذة Tailscale وفعّل HTTPS."
    except Exception as e:
        return 1, str(e)


def dns_name():
    """This PC's MagicDNS name (e.g. my-pc.tail1234.ts.net), or None."""
    code, out = _run(["status", "--json"], timeout=10)
    if code != 0:
        return None
    try:
        name = json.loads(out)["Self"]["DNSName"].rstrip(".")
        return name or None
    except Exception:
        return None


def phone_url():
    name = dns_name()
    return f"https://{name}" if name else None


def enable_serve(port: int):
    """Publishes 127.0.0.1:<port> over HTTPS inside your tailnet. Persists
    across reboots (--bg). Returns (ok, message)."""
    code, out = _run(["serve", "--bg", str(port)])
    return code == 0, out


def disable_serve():
    code, out = _run(["serve", "reset"])
    return code == 0, out
