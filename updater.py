"""
updater.py - in-app updates without rebuilding the exe.

An update is a ZIP with changed .py files at its top level (and optionally
a remote_ui/ folder and a VERSION file). "Update from file" in Settings
unpacks it into  <ARGUS folder>/updates/ . From then on ARGUS loads those
files instead of the copies built into the exe. Everything else (logs,
passwords, phone config) is never touched.

New third-party libraries: put a requirements.txt in the ZIP; they are
installed with the system Python (same version as the exe) into
updates/libs. Limit: a library that needs a standard-library module the
exe was built without still needs a fresh build.bat.
"""
import importlib.abc
import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from paths import APP_DIR, RESOURCE_DIR

UPDATES_DIR = APP_DIR / "updates"
BROKEN_DIR = APP_DIR / "updates_broken"
NEW_DIR = APP_DIR / "updates_new"
OLD_DIR = APP_DIR / "updates_old"
NEVER_OVERRIDE = {"updater", "gui_app", "paths"}
MAX_ZIP_BYTES = 30 * 1024 * 1024


class _UpdatesFinder(importlib.abc.MetaPathFinder):
    """Serves top-level modules from the updates folder before the exe's
    built-in copies are considered."""

    def find_spec(self, fullname, path=None, target=None):
        if path is not None or "." in fullname or fullname in NEVER_OVERRIDE:
            return None
        if not (UPDATES_DIR / f"{fullname}.py").is_file():
            return None
        return importlib.machinery.PathFinder.find_spec(fullname, [str(UPDATES_DIR)])


_finder = _UpdatesFinder()


def install_finder():
    if _finder not in sys.meta_path:
        sys.meta_path.insert(0, _finder)
    libs = UPDATES_DIR / "libs"
    if libs.is_dir():
        if str(libs) not in sys.path:
            sys.path.append(str(libs))      # after the built-in modules
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(str(libs))
            except OSError:
                pass


def has_updates() -> bool:
    return UPDATES_DIR.is_dir() and (any(UPDATES_DIR.glob("*.py")) or (UPDATES_DIR / "libs").is_dir())


def current_version() -> str:
    try:
        return (UPDATES_DIR / "VERSION").read_text(encoding="utf-8").strip() or "update installed"
    except OSError:
        return "update installed" if has_updates() else "built-in"


def ui_dir() -> Path:
    """Folder the phone web app is served from."""
    d = UPDATES_DIR / "remote_ui"
    return d if (d / "index.html").is_file() else RESOURCE_DIR / "remote_ui"


def _safe_name(name: str) -> bool:
    p = Path(name)
    return not (p.is_absolute() or ".." in p.parts or "\\" in name or ":" in name)


def _find_python():
    """A system Python with the same major.minor as this exe, so compiled
    wheels (.pyd) it downloads are loadable here."""
    want = f"{sys.version_info.major}.{sys.version_info.minor}"
    cands = []
    if shutil.which("py"):
        cands.append(["py", f"-{want}"])
    for exe in ("python", "python3"):
        if shutil.which(exe):
            cands.append([exe])
    for c in cands:
        try:
            out = subprocess.run(c + ["-c", "import sys;print('%d.%d'%sys.version_info[:2])"],
                                 capture_output=True, text=True, timeout=30,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode == 0 and out.stdout.strip() == want:
            return c
    return None


def _pip_install(req_text: str, target: Path) -> tuple[bool, str]:
    py = _find_python()
    want = f"{sys.version_info.major}.{sys.version_info.minor}"
    if py is None:
        return False, (f"This update needs new libraries, which needs Python {want} on this PC "
                       f"(the same version ARGUS was built with). Install it, or run build.bat instead.")
    req = target.parent / "requirements_update.txt"
    req.write_text(req_text, encoding="utf-8")
    try:
        r = subprocess.run(py + ["-m", "pip", "install", "--upgrade", "--no-input",
                                 "--disable-pip-version-check", "--only-binary=:all:",
                                 "--target", str(target), "-r", str(req)],
                           capture_output=True, text=True, timeout=900,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"Could not run pip: {e}"
    finally:
        req.unlink(missing_ok=True)
    if r.returncode != 0:
        tail = "\n".join((r.stderr or r.stdout).strip().splitlines()[-6:])
        return False, f"Installing the new libraries failed (check internet):\n{tail}"
    return True, ""


def apply_zip(zip_path) -> tuple[bool, str]:
    """Validate and install an update ZIP. Returns (ok, message)."""
    zip_path = Path(zip_path)
    try:
        if zip_path.stat().st_size > MAX_ZIP_BYTES:
            return False, "That file is too big to be an ARGUS update."
        zf = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile):
        return False, "That is not a valid ZIP file."

    with zf:
        members = [m for m in zf.infolist() if not m.is_dir()]
        # allow the ZIP to have everything inside one top folder
        names = [m.filename.replace("\\", "/") for m in members]
        for n in names:
            if not _safe_name(n):
                return False, f"Unsafe path in update: {n}"
        prefix = ""
        tops = {n.split("/")[0] for n in names if "/" in n}
        if len(tops) == 1 and all("/" in n for n in names) and next(iter(tops)) != "remote_ui":
            prefix = next(iter(tops)) + "/"

        files = {}
        for m, n in zip(members, names):
            rel = n[len(prefix):] if prefix and n.startswith(prefix) else n
            if not _safe_name(rel):
                return False, f"Unsafe path in update: {rel}"
            is_py = "/" not in rel and rel.endswith(".py")
            is_ui = rel.startswith("remote_ui/")
            if rel in ("VERSION", "requirements.txt") or is_py or is_ui:
                files[rel] = m
            elif rel.endswith((".bat", ".md", ".txt", ".ico")):
                continue                 # harmless extras are ignored
            else:
                return False, f"Unexpected file in update: {rel}"

        if not any(r.endswith(".py") or r.startswith("remote_ui/") for r in files):
            return False, "This ZIP has no ARGUS update files in it."

        # syntax-check every Python file before touching anything
        for rel, m in files.items():
            if rel.endswith(".py"):
                try:
                    compile(zf.read(m), rel, "exec")
                except SyntaxError as e:
                    return False, f"{rel} has an error (line {e.lineno}); nothing was changed."

        shutil.rmtree(NEW_DIR, ignore_errors=True)
        NEW_DIR.mkdir(parents=True)
        try:
            # keep previously installed update files that this ZIP doesn't replace
            if UPDATES_DIR.is_dir():
                shutil.copytree(UPDATES_DIR, NEW_DIR, dirs_exist_ok=True)
            if any(r.startswith("remote_ui/") for r in files):
                base = ui_dir()
                shutil.rmtree(NEW_DIR / "remote_ui", ignore_errors=True)
                shutil.copytree(base, NEW_DIR / "remote_ui")
            for rel, m in files.items():
                if rel == "requirements.txt":
                    continue
                dest = NEW_DIR / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(m))
            if "requirements.txt" in files:
                reqs = zf.read(files["requirements.txt"]).decode("utf-8", "replace")
                if any(l.strip() and not l.strip().startswith("#") for l in reqs.splitlines()):
                    ok, msg = _pip_install(reqs, NEW_DIR / "libs")
                    if not ok:
                        shutil.rmtree(NEW_DIR, ignore_errors=True)
                        return False, msg
        except OSError as e:
            shutil.rmtree(NEW_DIR, ignore_errors=True)
            return False, f"Could not write the update: {e}"

    shutil.rmtree(OLD_DIR, ignore_errors=True)
    if UPDATES_DIR.exists():
        UPDATES_DIR.rename(OLD_DIR)
    NEW_DIR.rename(UPDATES_DIR)
    return True, f"Update installed ({current_version()})."


def disable_updates():
    """Move the updates aside so the built-in version runs."""
    shutil.rmtree(BROKEN_DIR, ignore_errors=True)
    if UPDATES_DIR.exists():
        UPDATES_DIR.rename(BROKEN_DIR)


def reset_to_builtin():
    shutil.rmtree(UPDATES_DIR, ignore_errors=True)


def relaunch(delay: int = 2):
    """Start a fresh ARGUS a moment after this one exits."""
    if getattr(sys, "frozen", False):
        target = f'"{sys.executable}"'
    else:
        target = f'"{sys.executable}" "{Path(sys.argv[0]).resolve()}"'
    args = " ".join(f'"{a}"' for a in sys.argv[1:] if a == "--tray")
    cmd = f'ping -n {delay + 1} 127.0.0.1 >nul & start "" {target} {args}'
    env = dict(os.environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"   # new onefile process must not reuse our temp dir
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    subprocess.Popen(["cmd", "/c", cmd], env=env, creationflags=flags,
                     cwd=str(APP_DIR), close_fds=True)


# ======================================================================
# Online updates (GitHub Releases)
# ======================================================================
import hashlib
import json
import re
import tempfile
import urllib.error
import urllib.request

GITHUB_REPO = "Abdelrahman0-0/ARGUS"
SETUP_ASSET = "ARGUS_Setup.exe"


class UpdateError(Exception):
    pass


def _ver(v: str):
    nums = [int(x) for x in re.findall(r"\d+", v)[:4]]
    return tuple(nums + [0] * (4 - len(nums)))


def app_version() -> str:
    try:
        import version
        return version.APP_VERSION
    except Exception:
        return "0"


def check_latest(timeout: int = 12):
    """Return info dict for the newest release (never raises on 'no release')."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    req = urllib.request.Request(url, headers={"User-Agent": "ARGUS-updater",
                                               "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError("No release has been published yet.")
        raise UpdateError(f"GitHub answered {e.code}.")
    except (urllib.error.URLError, OSError, ValueError):
        raise UpdateError("Could not reach GitHub. Check the internet connection.")
    asset = next((a for a in data.get("assets", []) if a.get("name", "").lower() == SETUP_ASSET.lower()), None)
    if not asset:
        raise UpdateError(f"The latest release has no {SETUP_ASSET} file attached.")
    digest = (asset.get("digest") or "")
    return {
        "version": str(data.get("tag_name", "")).lstrip("vV"),
        "url": asset["browser_download_url"],
        "size": int(asset.get("size") or 0),
        "sha256": digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else "",
        "notes": (data.get("body") or "").strip(),
        "newer": _ver(str(data.get("tag_name", ""))) > _ver(app_version()),
    }


def download_setup(info, progress=None) -> Path:
    """Download the Setup exe to a temp folder and verify it."""
    if not info["url"].startswith("https://github.com/"):
        raise UpdateError("Unexpected download address.")
    dest_dir = Path(tempfile.gettempdir()) / "ARGUS_update"
    shutil.rmtree(dest_dir, ignore_errors=True)
    dest_dir.mkdir(parents=True)
    dest = dest_dir / SETUP_ASSET
    h = hashlib.sha256()
    req = urllib.request.Request(info["url"], headers={"User-Agent": "ARGUS-updater"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as out:
            total = int(r.headers.get("Content-Length") or info["size"] or 0)
            done = 0
            while True:
                buf = r.read(256 * 1024)
                if not buf:
                    break
                out.write(buf)
                h.update(buf)
                done += len(buf)
                if progress and total:
                    progress(done / total)
    except (urllib.error.URLError, OSError):
        raise UpdateError("The download failed. Try again.")
    if info["size"] and dest.stat().st_size != info["size"]:
        raise UpdateError("The downloaded file is incomplete. Try again.")
    if info["sha256"] and h.hexdigest() != info["sha256"]:
        raise UpdateError("The downloaded file did not match its checksum; it was discarded.")
    return dest


def launch_setup(path: Path):
    """Run the downloaded Setup in upgrade mode (it waits for ARGUS to exit)."""
    env = dict(os.environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
    subprocess.Popen([str(path), "--upgrade"], env=env, creationflags=flags, close_fds=True)
