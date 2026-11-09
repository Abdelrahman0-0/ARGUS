"""
setup_core.py - what the ARGUS installer / uninstaller actually does.
No UI in here, so it can be tested on its own.

Per-user install (no admin needed):  %LOCALAPPDATA%\\Programs\\ARGUS
  ARGUS.exe, app_icon.ico, Uninstall.exe
  + (created by ARGUS itself, kept across upgrades) logs\\, *.json, vapid_private.pem
Registers in "Apps & features" (HKCU ...\\Uninstall\\ARGUS).
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import winreg
except ImportError:          # not on Windows (tests)
    winreg = None

APP_NAME = "ARGUS"
PUBLISHER = "ARGUS"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\ARGUS"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CHUNK = 2 * 1024 * 1024
STALE_DIRS = ("updates", "updates_old", "updates_new", "updates_broken")


class SetupError(Exception):
    pass


# ---------------- Windows helpers (replaced in tests) ----------------
def default_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Programs" / APP_NAME


def existing_install():
    """Folder of a previous install, or None."""
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
            loc, _ = winreg.QueryValueEx(k, "InstallLocation")
        p = Path(loc)
        return p if (p / "ARGUS.exe").exists() else None
    except OSError:
        return None


def is_running() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ARGUS.exe", "/NH"],
                             capture_output=True, text=True, timeout=20,
                             creationflags=NO_WINDOW).stdout
        return "ARGUS.exe" in out
    except (OSError, subprocess.SubprocessError):
        return False


def folder(kind: str) -> Path:
    """'desktop' | 'start' | 'startup' -> real folder (handles OneDrive etc.)."""
    import win32com.client
    shell = win32com.client.Dispatch("WScript.Shell")
    name = {"desktop": "Desktop", "start": "Programs", "startup": "Startup"}[kind]
    return Path(shell.SpecialFolders(name))


def make_lnk(path: Path, target: Path, args: str, workdir: Path, icon: Path):
    import win32com.client
    shell = win32com.client.Dispatch("WScript.Shell")
    s = shell.CreateShortCut(str(path))
    s.TargetPath = str(target)
    if args:
        s.Arguments = args
    s.WorkingDirectory = str(workdir)
    s.IconLocation = str(icon)
    s.Description = "ARGUS - nothing goes unseen"
    s.save()


def reg_write(target: Path, version: str, self_uninstaller: Path):
    if winreg is None:
        return
    size_kb = int(sum(f.stat().st_size for f in target.glob("*.exe")) / 1024)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
        def sv(name, val, typ=winreg.REG_SZ):
            winreg.SetValueEx(k, name, 0, typ, val)
        sv("DisplayName", APP_NAME)
        sv("DisplayVersion", version)
        sv("Publisher", PUBLISHER)
        sv("InstallLocation", str(target))
        sv("DisplayIcon", str(target / "ARGUS.exe"))
        sv("UninstallString", f'"{self_uninstaller}" --uninstall')
        sv("NoModify", 1, winreg.REG_DWORD)
        sv("NoRepair", 1, winreg.REG_DWORD)
        sv("EstimatedSize", size_kb, winreg.REG_DWORD)


def reg_delete():
    if winreg is None:
        return
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass


# ---------------- install ----------------
def check_target(target: Path):
    low = str(target).lower()
    if "program files" in low or low.startswith(("c:\\windows", "c:/windows")):
        raise SetupError("ARGUS writes its logs next to itself, so it needs a folder you can "
                         "write to without admin rights. Pick another folder "
                         "(the default one is perfect).")
    if is_running():
        raise SetupError("ARGUS is still awake. Quit it first (tray icon > Quit; if away mode "
                         "is armed it will ask for the password), then try again.")


def install(target: Path, opts: dict, payload: Path, icon: Path, version: str, self_exe=None,
            wait_close: bool = False):
    """Generator of (fraction, message, delay_ms). Run it to completion."""
    target = Path(target)
    if wait_close:                      # launched by ARGUS itself: let it finish quitting
        for _ in range(60):
            if not is_running():
                break
            yield 0.01, "Waiting for it to close its eyes...", 500
    check_target(target)
    upgrading = (target / "ARGUS.exe").exists()
    yield 0.02, "Opening the eye...", 400

    target.mkdir(parents=True, exist_ok=True)
    if upgrading:
        # a fresh build already contains the latest code: stale overrides must not shadow it
        for d in STALE_DIRS:
            shutil.rmtree(target / d, ignore_errors=True)

    dest = target / "ARGUS.exe"
    tmp = target / "ARGUS.exe.new"
    total = max(payload.stat().st_size, 1)
    done = 0
    try:
        with open(payload, "rb") as src, open(tmp, "wb") as out:
            while True:
                buf = src.read(CHUNK)
                if not buf:
                    break
                out.write(buf)
                done += len(buf)
                yield 0.05 + 0.65 * done / total, "Binding it to this machine...", 8
        os.replace(tmp, dest)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise SetupError(f"Could not write ARGUS.exe: {e}")

    shutil.copy2(icon, target / "app_icon.ico")
    yield 0.76, "Learning the corridors...", 350

    uninstaller = target / "Uninstall.exe"
    if self_exe and Path(self_exe).exists() and Path(self_exe).resolve() != uninstaller.resolve():
        shutil.copy2(self_exe, uninstaller)
    yield 0.82, "Leaving a mark...", 350

    ico = target / "ARGUS.exe"
    if opts.get("desktop"):
        make_lnk(folder("desktop") / "ARGUS.lnk", dest, "", target, ico)
    if opts.get("start"):
        make_lnk(folder("start") / "ARGUS.lnk", dest, "", target, ico)
    if opts.get("startup"):
        make_lnk(folder("startup") / "ARGUS.lnk", dest, "--tray", target, ico)
    yield 0.92, "Registering with Windows...", 350

    reg_write(target, version, uninstaller)
    yield 0.98, "Almost awake...", 600
    yield 1.0, "It is awake.", 200


# ---------------- uninstall ----------------
def uninstall(target: Path, keep_data: bool):
    target = Path(target)
    if is_running():
        raise SetupError("ARGUS is still awake. Quit it first (tray icon > Quit), then try again.")
    yield 0.05, "Closing its eyes...", 500
    for kind in ("desktop", "start", "startup"):
        try:
            (folder(kind) / "ARGUS.lnk").unlink(missing_ok=True)
        except Exception:
            pass
    yield 0.30, "Erasing its marks...", 400
    reg_delete()
    yield 0.50, "Forgetting this machine...", 400
    for name in ("ARGUS.exe", "ARGUS.exe.new", "app_icon.ico"):
        (target / name).unlink(missing_ok=True)
    for d in STALE_DIRS:
        shutil.rmtree(target / d, ignore_errors=True)
    if not keep_data:
        shutil.rmtree(target / "logs", ignore_errors=True)
        for f in list(target.glob("*.json")) + list(target.glob("*.pem")):
            f.unlink(missing_ok=True)
    yield 0.85, "Letting go...", 500
    yield 1.0, "It sleeps.", 200


def schedule_final_cleanup(target: Path):
    """Uninstall.exe can't delete itself while running: a detached helper
    waits for us to exit, then removes it and the (now empty) folder."""
    t = str(target)
    cmd = (f'ping -n 3 127.0.0.1 >nul & del /f /q "{t}\\Uninstall.exe" & rmdir "{t}"')
    subprocess.Popen(["cmd", "/c", cmd], creationflags=NO_WINDOW | getattr(subprocess, "DETACHED_PROCESS", 0),
                     close_fds=True)


def launch_argus(target: Path):
    subprocess.Popen([str(Path(target) / "ARGUS.exe")], cwd=str(target),
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0), close_fds=True,
                     env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"})
