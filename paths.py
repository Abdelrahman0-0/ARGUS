"""
paths.py
Central place that figures out where ARGUS should read/write its data
(logs, password config, icon) regardless of whether it's running from
source (`python gui_app.py`) or as a compiled .exe (PyInstaller).

- APP_DIR: a persistent folder next to the running app. This is where
  logs and away_config.json live, so they survive restarts.
- RESOURCE_DIR: where bundled read-only assets (the icon) live. For a
  PyInstaller --onefile build this is a temporary extraction folder
  that only exists while the app is running - fine for read-only
  assets, but must never be used for anything ARGUS writes, or it would
  silently "forget" everything on every restart.
"""
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).parent
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
else:
    APP_DIR = Path(__file__).parent
    RESOURCE_DIR = APP_DIR

LOG_DIR = APP_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

ICON_PATH = RESOURCE_DIR / "app_icon.ico"
