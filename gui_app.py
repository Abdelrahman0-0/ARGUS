"""
gui_app.py - thin launcher.

It only sets up the update mechanism and then starts the real app
(app_ui.py). If an installed update crashes ARGUS at startup, the update
is switched off automatically and ARGUS restarts on the built-in version.
"""
import sys

import updater

updater.install_finder()

try:
    import app_ui
    app_ui.main()
except Exception:
    if updater.has_updates():
        updater.disable_updates()
        updater.relaunch(delay=1)
        sys.exit(1)
    raise
