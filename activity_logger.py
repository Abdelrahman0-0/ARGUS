"""
activity_logger.py
Tracks how actively you're using the keyboard/mouse WITHOUT ever recording
what you actually type or click on.

By design this only counts events (keystrokes per interval, clicks per
interval) - it never stores a key's identity, so passwords and typed
content never touch disk. Use the Window Activity log if you want to know
*which app* you were using; this is just "how active was I".
"""
import threading
from datetime import datetime
from pathlib import Path

from pynput import keyboard, mouse

from paths import LOG_DIR

LOG_FILE = LOG_DIR / "activity_level.log"

FLUSH_INTERVAL = 10  # seconds between each summary line


class _Counters:
    def __init__(self):
        self.lock = threading.Lock()
        self.key_count = 0
        self.click_count = 0

    def add_key(self):
        with self.lock:
            self.key_count += 1

    def add_click(self):
        with self.lock:
            self.click_count += 1

    def pop(self):
        with self.lock:
            k, c = self.key_count, self.click_count
            self.key_count = 0
            self.click_count = 0
        return k, c


def run(stop_event: threading.Event = None, on_event=None):
    """
    Runs until stop_event is set (or forever if stop_event is None).
    on_event(key_count, click_count) is called every FLUSH_INTERVAL
    seconds with the counts for that interval (both may be 0).
    """
    stop_event = stop_event or threading.Event()
    print(f"[activity_logger] Logging started at: {LOG_FILE}")

    counters = _Counters()

    def on_press(key):
        counters.add_key()

    def on_click(x, y, button, pressed):
        if pressed:
            counters.add_click()

    kb_listener = keyboard.Listener(on_press=on_press)
    ms_listener = mouse.Listener(on_click=on_click)
    kb_listener.start()
    ms_listener.start()

    while not stop_event.is_set():
        stop_event.wait(FLUSH_INTERVAL)
        keys, clicks = counters.pop()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] KEYSTROKES={keys} CLICKS={clicks}\n")
        if on_event:
            try:
                on_event(keys, clicks)
            except Exception:
                pass

    kb_listener.stop()
    ms_listener.stop()
    print("[activity_logger] Stopped.")


if __name__ == "__main__":
    run()
