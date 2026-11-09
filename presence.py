"""
presence.py
Detects real keyboard/mouse input using the Windows GetLastInputInfo API
(no key logging - it only reports *that* input happened). Used by away
mode so alerts mean "a person touched the device", not "a window title
changed in the background".
"""
import ctypes
import sys
import threading

POLL_SECONDS = 0.4


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def _last_input_tick():
    if sys.platform != "win32":
        return None
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    return info.dwTime


def idle_seconds():
    """Seconds since the last keyboard/mouse input, or None if unknown."""
    tick = _last_input_tick()
    if tick is None:
        return None
    now = ctypes.windll.kernel32.GetTickCount()
    return ((now - tick) & 0xFFFFFFFF) / 1000.0


def run(stop_event: threading.Event, away):
    """Calls away.mark_input() every time the OS reports new input."""
    last = _last_input_tick()
    if last is None:
        print("[presence] input detection unavailable on this OS")
        return
    print("[presence] Watching for input.")
    while not stop_event.is_set():
        stop_event.wait(POLL_SECONDS)
        cur = _last_input_tick()
        if cur is not None and cur != last:
            last = cur
            try:
                away.mark_input()
            except Exception as e:
                print(f"[presence] error: {e}")
    print("[presence] Stopped.")
