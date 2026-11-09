"""
startup.py
Handles everything related to launching ARGUS like a normal installed
Windows app:

- Enabling/disabling "start automatically when Windows starts", done via
  a shortcut in the current user's Startup folder. This is the same
  mechanism most normal apps use (visible in Task Manager > Startup,
  and removable by just deleting the .lnk) - nothing hidden or written
  to obscure registry locations.
- Creating a Desktop shortcut and a Start Menu shortcut so the app can
  be launched with a click instead of a terminal command.

Windows only (uses pywin32's WScript.Shell COM object to build .lnk
files, the standard way to create Windows shortcuts from Python).
"""
import os
import sys
from pathlib import Path

from paths import APP_DIR, ICON_PATH

APP_NAME = "ARGUS"
BASE_DIR = APP_DIR

STARTUP_DIR = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup"
START_MENU_DIR = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs"
DESKTOP_DIR = Path(os.environ.get("USERPROFILE", "")) / "Desktop"


def _target_and_args():
    """
    Figures out what a shortcut should actually launch:
    - If ARGUS was built into a standalone .exe (via PyInstaller,
      `sys.frozen` is set), launch that exe directly.
    - Otherwise (running from source), launch pythonw.exe (no console
      window flashing up) with gui_app.py as the argument.
    """
    if getattr(sys, "frozen", False):
        return sys.executable, ""

    python_dir = Path(sys.executable).parent
    pythonw = python_dir / "pythonw.exe"
    interpreter = str(pythonw) if pythonw.exists() else sys.executable
    script = str(BASE_DIR / "gui_app.py")
    return interpreter, f'"{script}"'


def _icon_location() -> str:
    """When frozen into a .exe, the icon is already baked into the exe
    itself (via PyInstaller's --icon flag), so point shortcuts at the
    exe. Only when running from source do we point at the standalone
    app_icon.ico file next to the scripts."""
    if getattr(sys, "frozen", False):
        return sys.executable
    return str(ICON_PATH) if ICON_PATH.exists() else ""


def _make_shortcut(lnk_path: Path, extra_args: str = ""):
    import win32com.client  # part of pywin32

    target, args = _target_and_args()
    if extra_args:
        args = f"{args} {extra_args}".strip()

    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(str(lnk_path))
    shortcut.TargetPath = target
    if args:
        shortcut.Arguments = args
    shortcut.WorkingDirectory = str(BASE_DIR)
    icon_location = _icon_location()
    if icon_location:
        shortcut.IconLocation = icon_location
    shortcut.Description = "ARGUS - personal device activity monitor"
    shortcut.save()


# ---------------- startup (auto-run on login) ----------------
def is_startup_enabled() -> bool:
    return (STARTUP_DIR / f"{APP_NAME}.lnk").exists()


def enable_startup():
    """Launches minimized to the tray with monitoring already on -
    that's what '--tray' tells gui_app.py to do."""
    STARTUP_DIR.mkdir(parents=True, exist_ok=True)
    _make_shortcut(STARTUP_DIR / f"{APP_NAME}.lnk", extra_args="--tray")


def disable_startup():
    path = STARTUP_DIR / f"{APP_NAME}.lnk"
    if path.exists():
        path.unlink()


# ---------------- manual shortcuts ----------------
def create_desktop_shortcut():
    DESKTOP_DIR.mkdir(parents=True, exist_ok=True)
    _make_shortcut(DESKTOP_DIR / f"{APP_NAME}.lnk")


def create_start_menu_shortcut():
    START_MENU_DIR.mkdir(parents=True, exist_ok=True)
    _make_shortcut(START_MENU_DIR / f"{APP_NAME}.lnk")
