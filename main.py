"""
main.py
CLI entry point: runs window tracking + activity-level logging + periodic
browser history checks, all together. Use gui_app.py instead if you want
the graphical interface with Start/Stop controls and Away Mode.
"""
import threading
import time

import window_tracker
import activity_logger
import browser_history


def main():
    print("=== ARGUS — CLI mode ===")
    print("Press Ctrl+C to stop.")

    stop_event = threading.Event()

    threads = [
        threading.Thread(target=window_tracker.run, args=(stop_event,), daemon=True),
        threading.Thread(target=activity_logger.run, args=(stop_event,), daemon=True),
        threading.Thread(target=browser_history.run, args=(stop_event, 60), daemon=True),
    ]

    for t in threads:
        t.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[main] Stopping...")
        stop_event.set()
        for t in threads:
            t.join(timeout=3)
        print("[main] Monitoring stopped.")


if __name__ == "__main__":
    main()
