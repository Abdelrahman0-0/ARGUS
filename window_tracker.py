"""
window_tracker.py
Tracks the active window/program - and the active *tab* within it - and
logs the moment either one changes.

Two Windows event hooks are used, both instant (no polling):

- EVENT_SYSTEM_FOREGROUND fires when you switch to a different window
  (e.g. Alt-Tab to another app).
- EVENT_OBJECT_NAMECHANGE fires when a window's title text changes -
  which is exactly what happens when you switch tabs inside the same
  browser window (the title bar updates to the new tab's page title,
  but the window itself never loses/regains focus, so FOREGROUND alone
  never fires for a tab switch). We filter this to only the window
  that's currently in the foreground, otherwise it would fire for
  every window on the whole desktop.

A fast-polling fallback is used only if the hooks fail to install.

Windows only (uses win32gui / ctypes user32).
"""
import ctypes
import threading
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import psutil
import win32gui
import win32process

from paths import LOG_DIR

LOG_FILE = LOG_DIR / "window_activity.log"

EVENT_SYSTEM_FOREGROUND = 0x0003
EVENT_OBJECT_NAMECHANGE = 0x800C
OBJID_WINDOW = 0
WINEVENT_OUTOFCONTEXT = 0x0000

user32 = ctypes.windll.user32

WinEventProcType = ctypes.WINFUNCTYPE(
    None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
    wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD,
)

# how often (seconds) we pump the Windows message queue / check stop_event
# while waiting for the next hook callback - keep this small, it's what
# determines the worst-case detection delay, not a polling interval for
# the window itself
PUMP_INTERVAL = 0.05

# only used if neither event hook could be installed at all
FALLBACK_POLL_INTERVAL = 0.3


def get_window_info(hwnd):
    """Returns (process_name, window_title) for a given window handle."""
    if not hwnd:
        return None, None
    title = win32gui.GetWindowText(hwnd)
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        process_name = psutil.Process(pid).name()
    except Exception:
        process_name = "unknown"
    return process_name, title


def log_line(text: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {text}\n")


def run(stop_event: threading.Event = None, on_event=None):
    """
    Runs until stop_event is set (or forever if stop_event is None).

    on_event(process_name, title, timestamp) is invoked immediately every
    time the foreground window OR its title (e.g. active browser tab)
    changes.
    """
    stop_event = stop_event or threading.Event()
    print(f"[window_tracker] Logging started at: {LOG_FILE}")

    last_title = {"value": None}

    def handle_switch(hwnd):
        process_name, title = get_window_info(hwnd)
        if title and title != last_title["value"]:
            last_title["value"] = title
            ts = datetime.now()
            log_line(f"APP={process_name} | TITLE={title}")
            if on_event:
                try:
                    on_event(process_name, title, ts)
                except Exception:
                    pass

    def on_foreground_changed(hwnd):
        handle_switch(hwnd)

    def on_name_changed(hwnd, id_object):
        # only care about the title bar (OBJID_WINDOW) of whichever
        # window is currently in the foreground - this is what catches
        # a tab switch inside the active browser window - anything else
        # would just be background-window noise
        if id_object != OBJID_WINDOW:
            return
        if hwnd != win32gui.GetForegroundWindow():
            return
        handle_switch(hwnd)

    def win_event_callback(hWinEventHook, event, hwnd, idObject, idChild,
                            idEventThread, dwmsEventTime):
        if event == EVENT_SYSTEM_FOREGROUND:
            on_foreground_changed(hwnd)
        elif event == EVENT_OBJECT_NAMECHANGE:
            on_name_changed(hwnd, idObject)

    # keep a reference alive for the lifetime of the hooks - ctypes does
    # not keep the callback alive on its own
    callback_ptr = WinEventProcType(win_event_callback)

    fg_hook = user32.SetWinEventHook(
        EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND,
        0, callback_ptr, 0, 0, WINEVENT_OUTOFCONTEXT,
    )
    name_hook = user32.SetWinEventHook(
        EVENT_OBJECT_NAMECHANGE, EVENT_OBJECT_NAMECHANGE,
        0, callback_ptr, 0, 0, WINEVENT_OUTOFCONTEXT,
    )

    # log whatever window is already active before any switch happens
    handle_switch(win32gui.GetForegroundWindow())

    if not fg_hook and not name_hook:
        # fallback: fast polling if neither hook could be installed
        print("[window_tracker] Event hooks unavailable, using fast polling.")
        while not stop_event.is_set():
            handle_switch(win32gui.GetForegroundWindow())
            stop_event.wait(FALLBACK_POLL_INTERVAL)
        print("[window_tracker] Stopped.")
        return

    # pump Windows messages so the hook callbacks actually fire, while
    # regularly checking stop_event so we can exit promptly
    msg = wintypes.MSG()
    while not stop_event.is_set():
        while user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        stop_event.wait(PUMP_INTERVAL)

    if fg_hook:
        user32.UnhookWinEvent(fg_hook)
    if name_hook:
        user32.UnhookWinEvent(name_hook)
    print("[window_tracker] Stopped.")


if __name__ == "__main__":
    run()
