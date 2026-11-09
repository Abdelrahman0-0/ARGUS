"""
actions.py
Things the phone can make this PC do. Windows-only calls are wrapped so the
module imports (and fails with a clear message) on other systems.
"""
import io
import subprocess
import sys
import threading
import time

IS_WIN = sys.platform == "win32"
_siren_stop = threading.Event()
_siren_lock = threading.Lock()


class ActionError(Exception):
    pass


def _need_windows():
    if not IS_WIN:
        raise ActionError("الأمر ده بيشتغل على ويندوز بس")


def lock_workstation():
    _need_windows()
    import ctypes
    if not ctypes.windll.user32.LockWorkStation():
        raise ActionError("ويندوز رفض يقفل الجهاز")


def shutdown(delay_seconds=5):
    _need_windows()
    subprocess.Popen(["shutdown", "/s", "/f", "/t", str(int(delay_seconds))],
                     creationflags=0x08000000)  # CREATE_NO_WINDOW


def siren(seconds=20):
    """Loud two-tone alarm. Replaces a siren that's already running."""
    _need_windows()
    import winsound
    seconds = max(3, min(int(seconds), 120))
    _siren_stop.set()          # stop previous one
    time.sleep(0.05)
    _siren_stop.clear()

    def _run():
        end = time.time() + seconds
        while time.time() < end and not _siren_stop.is_set():
            for freq in (900, 1500):
                if _siren_stop.is_set():
                    return
                try:
                    winsound.Beep(freq, 350)
                except Exception:
                    return

    threading.Thread(target=_run, daemon=True).start()


def siren_stop():
    _siren_stop.set()


_message_handler = None   # set by the GUI: shows the horror overlay on the GUI thread


def set_message_handler(fn):
    global _message_handler
    _message_handler = fn


def show_message(text: str):
    _need_windows()
    import ctypes
    text = (text or "").strip()[:300] or "الجهاز ده متراقب. اتركه."
    if _message_handler is not None:
        try:
            _message_handler(text)      # emits a Qt signal -> overlay on GUI thread
            return
        except Exception:
            pass                        # fall back to the plain box below
    # MB_OK | MB_ICONWARNING | MB_SETFOREGROUND | MB_TOPMOST
    flags = 0x0 | 0x30 | 0x10000 | 0x40000
    threading.Thread(
        target=lambda: ctypes.windll.user32.MessageBoxW(0, text, "ARGUS", flags),
        daemon=True).start()


def screenshot_jpeg(max_width=1600, quality=60) -> bytes:
    try:
        from PIL import ImageGrab
    except ImportError:
        raise ActionError("مكتبة Pillow مش متسطبة")
    try:
        img = ImageGrab.grab(all_screens=True)
    except Exception as e:
        raise ActionError(f"مقدرتش آخد لقطة شاشة: {e}")
    img = img.convert("RGB")
    if img.width > max_width:
        img = img.resize((max_width, int(img.height * max_width / img.width)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def webcam_available() -> bool:
    try:
        import cv2  # noqa: F401
        return True
    except Exception:
        return False


def webcam_jpeg(quality=70) -> bytes:
    try:
        import cv2
    except Exception:
        raise ActionError("الكاميرا محتاجة opencv-python (pip install opencv-python)")
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW if IS_WIN else 0)
    try:
        if not cap.isOpened():
            raise ActionError("مقدرتش أفتح الكاميرا")
        frame = None
        for _ in range(8):          # let auto-exposure settle
            ok, frame = cap.read()
            time.sleep(0.1)
        if frame is None:
            raise ActionError("الكاميرا ما رجّعتش صورة")
        ok, enc = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if not ok:
            raise ActionError("فشل تحويل صورة الكاميرا")
        return enc.tobytes()
    finally:
        cap.release()
