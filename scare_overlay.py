"""
scare_overlay.py
Full-screen horror overlay for messages sent from the phone.

Pure QPainter (no image assets). Timeline:
  0.0s  dead static + red strobe flashes
  0.9s  a slit opens in the dark... an eye
  1.8s  JUMP: the eye snaps wide, screen shakes, white flash, bass hit
  2.0s+ the message is typed out with RGB-split glitching, the pupil follows
        the mouse cursor, the iris corrupts from ice-cyan to blood red
  end   the eye closes and the screen goes black

Sound is generated in memory (drone + heartbeat + scream hit) and played
with winsound, so there are no audio files to ship. Everything degrades
gracefully on non-Windows systems (no sound).

Click or press Esc to dismiss, but only after a few seconds so whoever is
sitting at the PC has to see the message first.
"""
import math
import random
import struct
import sys
import threading
import wave
import io

from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, QRect
from PySide6.QtGui import (
    QPainter, QColor, QFont, QRadialGradient, QLinearGradient, QPen,
    QPainterPath, QPixmap, QFontMetrics, QCursor, QGuiApplication,
)
from PySide6.QtWidgets import QWidget

ICE = QColor("#7EE8FF")
BLOOD = QColor("#ff1f3a")
DARK_BLOOD = QColor("#5a0610")
VOID = QColor("#020207")

FRAME_MS = 16
T_SLIT = 900          # eye starts to open
T_JUMP = 1800         # jump scare
T_TEXT = 2100         # message starts typing
MIN_DISMISS_MS = 4500
FONTS = ["Segoe UI", "Tahoma", "Arial", "Consolas"]

# ----------------------------------------------------------------------
# Sound (generated once, cached)
# ----------------------------------------------------------------------
SR = 22050
SOUND_SECONDS = 19
_sound_bytes = None
_sound_lock = threading.Lock()


def _build_sound() -> bytes:
    n = SR * SOUND_SECONDS
    rnd = random.Random(13)
    buf = [0.0] * n

    # 1) low drone that swells (two detuned sines + slow tremolo)
    for i in range(n):
        t = i / SR
        swell = min(1.0, t / 6.0) * (1.0 if t < SOUND_SECONDS - 2 else max(0.0, (SOUND_SECONDS - t) / 2))
        trem = 0.75 + 0.25 * math.sin(2 * math.pi * 0.35 * t)
        s = (math.sin(2 * math.pi * 55.0 * t) + 0.8 * math.sin(2 * math.pi * 58.3 * t)
             + 0.25 * math.sin(2 * math.pi * 110.7 * t))
        buf[i] += 0.16 * swell * trem * s

    # 2) heartbeat that speeds up (lub-dub)
    def thump(start, amp, freq=48.0, dur=0.20):
        a = int(start * SR)
        for k in range(int(dur * SR)):
            if a + k >= n:
                break
            tt = k / SR
            env = math.exp(-tt * 20.0)
            buf[a + k] += amp * env * math.sin(2 * math.pi * (freq - 14 * tt) * tt * 1.0 + 0.0)

    t = 0.5
    bpm = 62.0
    while t < SOUND_SECONDS - 1.5:
        thump(t, 0.55)
        thump(t + 0.24, 0.38, freq=42.0)
        bpm = min(118.0, bpm + 2.2)
        t += 60.0 / bpm

    # 3) the jump scare at T_JUMP: noise burst + falling screech + sub boom
    j = int(T_JUMP / 1000.0 * SR)
    for k in range(int(0.9 * SR)):
        if j + k >= n:
            break
        tt = k / SR
        env = math.exp(-tt * 3.2)
        noise = rnd.uniform(-1, 1) * math.exp(-tt * 7.0)
        f = 2300.0 * math.exp(-tt * 2.4) + 380.0
        screech = math.sin(2 * math.pi * f * tt + 6 * math.sin(2 * math.pi * 31 * tt))
        boom = math.sin(2 * math.pi * 38.0 * tt) * math.exp(-tt * 4.0)
        buf[j + k] += 0.55 * noise + 0.38 * env * screech + 0.9 * boom

    # 4) a couple of tiny glitch crackles while the text types
    for _ in range(14):
        c = int((T_TEXT / 1000.0 + rnd.random() * (SOUND_SECONDS - 6)) * SR)
        for k in range(int(0.035 * SR)):
            if c + k < n:
                buf[c + k] += rnd.uniform(-0.35, 0.35)

    # soft clip -> int16
    out = bytearray()
    for v in buf:
        v = math.tanh(v * 1.1)
        out += struct.pack("<h", int(v * 30000))
    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(bytes(out))
    return bio.getvalue()


def prewarm():
    """Build the soundtrack in the background so the first scare has no lag."""
    def work():
        global _sound_bytes
        with _sound_lock:
            if _sound_bytes is None:
                try:
                    _sound_bytes = _build_sound()
                except Exception:
                    _sound_bytes = b""
    threading.Thread(target=work, daemon=True).start()


def _play_sound():
    if sys.platform != "win32":
        return
    try:
        import winsound
        with _sound_lock:
            global _sound_bytes
            if _sound_bytes is None:
                _sound_bytes = _build_sound()
            data = _sound_bytes
        if data:
            winsound.PlaySound(data, winsound.SND_MEMORY | winsound.SND_ASYNC)
    except Exception:
        pass


def _stop_sound():
    if sys.platform != "win32":
        return
    try:
        import winsound
        winsound.PlaySound(None, winsound.SND_PURGE)
    except Exception:
        pass


# ----------------------------------------------------------------------
# Overlay
# ----------------------------------------------------------------------
def _ease_out(x):
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def _ease_in(x):
    x = max(0.0, min(1.0, x))
    return x * x * x


class _Clock:
    """Shared by every screen's overlay so they stay in sync."""
    def __init__(self):
        self.t = 0.0


class ScareOverlay(QWidget):
    def __init__(self, screen, text, clock, total_ms, primary=True, on_done=None):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setCursor(Qt.BlankCursor)
        self.setStyleSheet("background-color: #020207;")
        self._screen = screen
        self.setGeometry(screen.geometry())

        self.text = text
        self.clock = clock
        self.total = total_ms
        self.primary = primary
        self.on_done = on_done
        self._closing = False
        self._rnd = random.Random(99)
        self._veins = self._make_veins()
        self._static = self._make_static()
        self._layer_cache = {}
        self._cache_key = None
        self._text_font = None
        self._text_rect = None
        self._last_n = -1
        self._shake = (0.0, 0.0)
        self._glitch_slices = []
        self._next_glitch = 0.0
        self._glitch_until = 0.0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    # ---- lifecycle ----
    def start(self):
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self._timer.start(FRAME_MS)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self.clock.t >= MIN_DISMISS_MS:
            self.dismiss()

    def mousePressEvent(self, e):
        if self.clock.t >= MIN_DISMISS_MS:
            self.dismiss()

    def dismiss(self):
        if self._closing:
            return
        self._closing = True
        self._timer.stop()
        self.close()
        if self.on_done:
            self.on_done()

    def _tick(self):
        if self.primary:
            self.clock.t += FRAME_MS
        if self.clock.t >= self.total:
            self.dismiss()
            return
        self.update()

    # ---- cached assets ----
    def _make_veins(self):
        rnd = random.Random(5)
        veins = []
        for _ in range(26):
            ang = rnd.uniform(0, math.tau)
            length = rnd.uniform(0.35, 0.8)
            bend = rnd.uniform(-0.5, 0.5)
            veins.append((ang, length, bend, rnd.uniform(0.8, 2.2)))
        return veins

    def _make_static(self):
        pm = QPixmap(256, 256)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        rnd = random.Random(3)
        for _ in range(2600):
            v = rnd.randint(60, 255)
            p.setPen(QColor(v, v, v, rnd.randint(40, 200)))
            p.drawPoint(rnd.randrange(256), rnd.randrange(256))
        p.end()
        return pm

    def _scanlines(self, w, h):
        key = ("scan", w, h)
        if key not in self._layer_cache:
            pm = QPixmap(w, h)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setPen(QColor(0, 0, 0, 70))
            for y in range(0, h, 3):
                p.drawLine(0, y, w, y)
            p.end()
            self._layer_cache[key] = pm
        return self._layer_cache[key]

    def _vignette(self, w, h):
        key = ("vig", w, h)
        if key not in self._layer_cache:
            pm = QPixmap(w, h)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            g = QRadialGradient(QPointF(w / 2, h / 2), max(w, h) * 0.75)
            g.setColorAt(0.0, QColor(0, 0, 0, 0))
            g.setColorAt(0.55, QColor(0, 0, 0, 40))
            g.setColorAt(1.0, QColor(0, 0, 0, 235))
            p.fillRect(0, 0, w, h, g)
            p.end()
            self._layer_cache[key] = pm
        return self._layer_cache[key]

    # ---- text layout ----
    def _layout_text(self, w, h):
        key = (w, h)
        if self._cache_key == key:
            return
        self._cache_key = key
        self._layer_cache = {k: v for k, v in self._layer_cache.items() if k[0] in ("scan", "vig")}
        box_w = int(w * 0.82)
        box_h = int(h * 0.28)
        size = max(18, int(h / 11))
        flags = Qt.AlignCenter | Qt.TextWordWrap
        while size > 16:
            f = QFont()
            f.setFamilies(FONTS)
            f.setPixelSize(size)
            f.setBold(True)
            r = QFontMetrics(f).boundingRect(QRect(0, 0, box_w, 10000), int(flags), self.text)
            if r.height() <= box_h:
                break
            size -= 2
        self._text_font = f
        self._text_rect = QRect(int(w * 0.09), int(h * 0.65), box_w, box_h)

    def _text_layers(self, n, w, h):
        """White / red / cyan versions of the first n chars, cached."""
        key = ("txt", n)
        if key in self._layer_cache:
            return self._layer_cache[key]
        r = self._text_rect
        shown = self.text[:n]
        layers = []
        for col in (QColor(255, 255, 255), BLOOD, ICE):
            pm = QPixmap(r.width(), r.height())
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.TextAntialiasing)
            p.setFont(self._text_font)
            p.setPen(col)
            p.drawText(QRect(0, 0, r.width(), r.height()),
                       int(Qt.AlignCenter | Qt.TextWordWrap), shown)
            p.end()
            layers.append(pm)
        if len(self._layer_cache) > 80:
            self._layer_cache = {k: v for k, v in self._layer_cache.items() if k[0] != "txt"}
        self._layer_cache[key] = layers
        return layers

    # ---- paint ----
    def paintEvent(self, event):
        t = self.clock.t
        w, h = self.width(), self.height()
        self._layout_text(w, h)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), VOID)

        # screen shake (violent at the jump, then a constant tremble)
        since = t - T_JUMP
        amp = 0.0
        if 0 <= since < 700:
            amp = 26.0 * (1 - since / 700.0)
        elif t > T_TEXT:
            amp = 1.6
        sx = self._rnd.uniform(-amp, amp)
        sy = self._rnd.uniform(-amp, amp)
        p.translate(sx, sy)

        end_close = max(0.0, (t - (self.total - 1400)) / 1400.0)   # eyelid closing at the end
        open_p = self._eye_open(t, end_close)

        # blood glow pulsing with the heartbeat
        beat = self._beat(t)
        corrupt = _ease_out((t - T_JUMP) / 2500.0) if t > T_JUMP else 0.0
        self._draw_glow(p, w, h, open_p, beat, corrupt)
        self._draw_rings(p, w, h, t, open_p, corrupt)
        if open_p > 0.003:
            self._draw_eye(p, w, h, t, open_p, beat, corrupt)
        self._draw_ghost_words(p, w, h, t)
        if t >= T_TEXT and end_close < 0.85:
            self._draw_message(p, w, h, t, corrupt, end_close)
        self._draw_hud(p, w, h, t)

        p.translate(-sx, -sy)

        # opening static + strobe
        self._draw_noise(p, w, h, t)
        if t < 700:
            for f0 in (120, 300, 430):
                if f0 <= t < f0 + 55:
                    p.fillRect(self.rect(), QColor(190, 10, 25, 230))
        # white flash at the jump
        if T_JUMP <= t < T_JUMP + 160:
            a = 1 - (t - T_JUMP) / 160.0
            p.fillRect(self.rect(), QColor(255, 255, 255, int(255 * a)))

        p.drawPixmap(0, 0, self._vignette(w, h))
        p.setOpacity(0.55)
        p.drawPixmap(0, 0, self._scanlines(w, h))
        p.setOpacity(1.0)

        # a rare full black blink (feels like the eye blinked at you)
        if t > T_TEXT + 1500 and int(t) % 3700 < 70:
            p.fillRect(self.rect(), QColor(0, 0, 0, 255))

        # final fade to black
        if t > self.total - 500:
            a = min(1.0, (t - (self.total - 500)) / 500.0)
            p.fillRect(self.rect(), QColor(0, 0, 0, int(255 * a)))
        p.end()

    # ---- timeline helpers ----
    def _eye_open(self, t, end_close):
        if t < T_SLIT:
            return 0.0
        if t < T_JUMP:
            o = 0.04 + 0.16 * _ease_out((t - T_SLIT) / (T_JUMP - T_SLIT))
            # twitch
            o += 0.015 * math.sin(t / 37.0)
        elif t < T_JUMP + 260:
            o = 0.20 + 0.80 * _ease_out((t - T_JUMP) / 120.0)
        else:
            o = 1.0 - 0.04 * math.sin(t / 700.0)
        return max(0.0, o * (1 - _ease_in(end_close)))

    def _beat(self, t):
        """0..1 double thump roughly in sync with the soundtrack."""
        if t < 500:
            return 0.0
        bpm = 62.0
        tt = 500.0
        while tt + 60000.0 / bpm < t:
            tt += 60000.0 / bpm
            bpm = min(118.0, bpm + 2.2)
        d = t - tt
        v = math.exp(-d / 90.0)
        if d > 240:
            v = max(v, 0.7 * math.exp(-(d - 240) / 100.0))
        return max(0.0, min(1.0, v))

    # ---- layers ----
    def _draw_glow(self, p, w, h, open_p, beat, corrupt):
        if open_p <= 0:
            return
        cx, cy = w / 2, h * 0.36
        c = self._mix(ICE, BLOOD, corrupt)
        r = max(w, h) * (0.35 + 0.05 * beat) * (0.4 + 0.6 * open_p)
        g = QRadialGradient(QPointF(cx, cy), r)
        c1 = QColor(c); c1.setAlpha(int(110 + 70 * beat))
        c2 = QColor(c); c2.setAlpha(0)
        g.setColorAt(0, c1)
        g.setColorAt(1, c2)
        p.fillRect(self.rect(), g)

    def _draw_rings(self, p, w, h, t, open_p, corrupt):
        if open_p <= 0.05:
            return
        cx, cy = w / 2, h * 0.36
        base = min(w, h)
        col = self._mix(ICE, BLOOD, corrupt)
        for i in range(5):
            frac = (t / 2200.0 + i / 5.0) % 1.0
            r = (0.2 + frac * 0.75) * base
            c = QColor(col)
            c.setAlphaF(max(0.0, (1 - frac)) * 0.55 * open_p)
            pen = QPen(c)
            pen.setWidthF(1.5)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(cx, cy), r, r)
        # rotating dashed ring (radar tick marks)
        pen = QPen(self._mix(ICE, BLOOD, corrupt))
        pen.setWidthF(2.0)
        pen.setStyle(Qt.CustomDashLine)
        pen.setDashPattern([2, 10])
        pen.setDashOffset(t / 40.0)
        p.setPen(pen)
        p.setOpacity(0.7 * open_p)
        rr = base * 0.30
        p.drawEllipse(QPointF(cx, cy), rr, rr)
        p.setOpacity(1.0)

    def _draw_eye(self, p, w, h, t, open_p, beat, corrupt):
        cx, cy = w / 2, h * 0.36
        ew = min(w * 0.56, h * 0.85) * (1.0 + 0.012 * beat + (0.04 if T_JUMP <= t < T_JUMP + 300 else 0))
        eh = ew * 0.22 * open_p                     # half-height of the opening
        left, right = cx - ew / 2, cx + ew / 2

        eye = QPainterPath()
        eye.moveTo(left, cy)
        eye.cubicTo(left + ew * 0.25, cy - eh * 2.0, right - ew * 0.25, cy - eh * 2.0, right, cy)
        eye.cubicTo(right - ew * 0.25, cy + eh * 1.9, left + ew * 0.25, cy + eh * 1.9, left, cy)

        # sclera (dark, bloodshot)
        p.save()
        p.setClipPath(eye)
        sc = QRadialGradient(QPointF(cx, cy), ew * 0.5)
        sc.setColorAt(0.0, QColor(210, 190, 190))
        sc.setColorAt(0.55, QColor(120, 40, 46))
        sc.setColorAt(1.0, QColor(24, 2, 6))
        p.fillPath(eye, sc)

        # veins creeping in from the corners
        vein_a = 0.25 + 0.55 * corrupt
        for ang, length, bend, wd in self._veins:
            path = QPainterPath()
            sx0 = cx + math.cos(ang) * ew * 0.55
            sy0 = cy + math.sin(ang) * eh * 1.8
            path.moveTo(sx0, sy0)
            ex = cx + math.cos(ang) * ew * (0.55 - length * 0.4)
            ey = cy + math.sin(ang) * eh * 1.8 * (1 - length * 0.6)
            mx = (sx0 + ex) / 2 + math.sin(ang * 3) * ew * 0.03 * bend * 6
            my = (sy0 + ey) / 2 + bend * eh * 0.4
            path.quadTo(mx, my, ex, ey)
            c = QColor(190, 10, 28); c.setAlphaF(vein_a)
            pen = QPen(c); pen.setWidthF(wd * (0.8 + 0.4 * beat))
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)

        # iris follows the real mouse cursor (creepy on purpose)
        look = self._look_vector(cx, cy, w, h)
        irx = cx + look[0] * ew * 0.075
        iry = cy + look[1] * eh * 0.35
        ir = min(eh * 1.45, ew * 0.20)
        iris_col = self._mix(ICE, BLOOD, corrupt)
        ig = QRadialGradient(QPointF(irx, iry), ir)
        ig.setColorAt(0.0, QColor(0, 0, 0))
        ig.setColorAt(0.28, QColor(0, 0, 0))
        ig.setColorAt(0.34, self._mix(iris_col, QColor(255, 255, 255), 0.35))
        ig.setColorAt(0.75, iris_col)
        ig.setColorAt(1.0, self._mix(iris_col, QColor(0, 0, 0), 0.7))
        p.setPen(Qt.NoPen)
        p.setBrush(ig)
        p.drawEllipse(QPointF(irx, iry), ir, ir)

        # radial fibres in the iris
        pen = QPen(self._mix(iris_col, QColor(0, 0, 0), 0.5)); pen.setWidthF(1.0)
        p.setPen(pen)
        for k in range(36):
            a = k / 36 * math.tau
            p.drawLine(QPointF(irx + math.cos(a) * ir * 0.38, iry + math.sin(a) * ir * 0.38),
                       QPointF(irx + math.cos(a) * ir * 0.95, iry + math.sin(a) * ir * 0.95))

        # slit pupil that dilates with the heartbeat
        pw = ir * (0.10 + 0.07 * beat + 0.05 * (1 - corrupt))
        ph = ir * 0.78
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0))
        p.drawEllipse(QPointF(irx, iry), pw, ph)

        # wet highlight
        p.setBrush(QColor(255, 255, 255, 200))
        p.drawEllipse(QPointF(irx - ir * 0.32, iry - ir * 0.34), ir * 0.09, ir * 0.07)

        # lid shadow
        lid = QLinearGradient(0, cy - eh * 1.9, 0, cy + eh * 1.9)
        lid.setColorAt(0.0, QColor(0, 0, 0, 230))
        lid.setColorAt(0.28, QColor(0, 0, 0, 0))
        lid.setColorAt(0.72, QColor(0, 0, 0, 0))
        lid.setColorAt(1.0, QColor(0, 0, 0, 200))
        p.fillPath(eye, lid)
        p.restore()

        # glowing lid outline
        col = self._mix(ICE, BLOOD, corrupt)
        for wd, al in ((14, 18), (8, 40), (3.2, 255)):
            c = QColor(col); c.setAlpha(al)
            pen = QPen(c); pen.setWidthF(wd); pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(eye)

    def _look_vector(self, cx, cy, w, h):
        pos = QCursor.pos()
        geo = self._screen.geometry()
        lx = (pos.x() - geo.x()) - cx
        ly = (pos.y() - geo.y()) - cy
        d = math.hypot(lx, ly)
        if d < 1:
            return (0.0, 0.0)
        k = min(1.0, d / (min(w, h) * 0.5))
        return (lx / d * k, ly / d * k)

    def _draw_ghost_words(self, p, w, h, t):
        """Faint words that flash in the dark around the eye."""
        if t < T_JUMP + 600:
            return
        slot = int(t // 520)
        rnd = random.Random(slot * 7919)
        if rnd.random() > 0.45 or (t % 520) > 130:
            return
        words = ["أنا شايفك", "SEE YOU", "WATCHING", "اتفرجت عليك", "NOT ALONE", "ARGUS", "مش لوحدك"]
        word = rnd.choice(words)
        f = QFont(); f.setFamilies(FONTS); f.setPixelSize(int(h / rnd.choice([9, 12, 16])))
        f.setBold(True)
        p.setFont(f)
        c = QColor(BLOOD); c.setAlpha(rnd.randint(40, 110))
        p.setPen(c)
        x = rnd.uniform(0.02, 0.6) * w
        y = rnd.uniform(0.15, 0.95) * h
        p.drawText(QPointF(x, y), word)

    def _draw_message(self, p, w, h, t, corrupt, end_close):
        n_total = len(self.text)
        n = min(n_total, int((t - T_TEXT) / 55.0) + 1)
        r = self._text_rect
        white, red, cyan = self._text_layers(n, w, h)
        fade = 1.0 - _ease_in(end_close / 0.85)

        # periodic glitch burst: slice the text into strips and shove them sideways
        if t >= self._next_glitch:
            self._next_glitch = t + self._rnd.uniform(350, 1100)
            self._glitch_until = t + self._rnd.uniform(90, 260)
            self._glitch_slices = [(self._rnd.randint(0, r.height()), self._rnd.randint(6, 40),
                                    self._rnd.uniform(-60, 60)) for _ in range(self._rnd.randint(3, 8))]
        glitching = t < getattr(self, "_glitch_until", 0)
        split = 4 + 6 * (1 if glitching else 0) + 3 * self._beat(t)

        p.setOpacity(fade * 0.9)
        p.drawPixmap(int(r.x() - split), r.y(), red)
        p.setOpacity(fade * 0.75)
        p.drawPixmap(int(r.x() + split), r.y(), cyan)
        p.setOpacity(fade)
        flick = 1.0 if self._rnd.random() > 0.04 else 0.35
        p.setOpacity(fade * flick)
        p.drawPixmap(r.x(), r.y(), white)

        if glitching:
            for (y0, hh, dx) in self._glitch_slices:
                hh = min(hh, r.height() - y0)
                if hh <= 0:
                    continue
                p.setOpacity(fade)
                p.drawPixmap(int(r.x() + dx), r.y() + y0, white, 0, y0, r.width(), hh)
                p.drawPixmap(int(r.x() + dx * 1.6), r.y() + y0, red, 0, y0, r.width(), hh)
        p.setOpacity(1.0)

        # blinking cursor block while typing
        if n < n_total and int(t / 140) % 2 == 0:
            p.fillRect(QRectF(r.center().x() - 6, r.bottom() + 6, 12, 5), BLOOD)

    def _draw_hud(self, p, w, h, t):
        if t < T_JUMP:
            return
        f = QFont(); f.setFamilies(["Consolas", "Cascadia Code", "Courier New"])
        f.setPixelSize(max(12, int(h / 55)))
        p.setFont(f)
        c = QColor(BLOOD); c.setAlpha(230)
        p.setPen(c)
        pad = int(h / 28)
        if int(t / 600) % 2 == 0:
            p.setBrush(BLOOD)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(pad + 6, pad + 8), 6, 6)
        p.setPen(c)
        p.drawText(QPointF(pad + 22, pad + 14), "REC")
        p.drawText(QPointF(pad, h - pad), "ARGUS // SUBJECT ACQUIRED")
        txt = "TRACKING " + "".join(random.Random(int(t // 90)).choice("0123456789ABCDEF") for _ in range(8))
        fm = QFontMetrics(f)
        p.drawText(QPointF(w - pad - fm.horizontalAdvance(txt), h - pad), txt)
        # corner brackets
        pen = QPen(c); pen.setWidthF(2.5)
        p.setPen(pen)
        L = int(h / 14)
        m = pad - 6
        for (x, y, dx, dy) in ((m, m, 1, 1), (w - m, m, -1, 1), (m, h - m, 1, -1), (w - m, h - m, -1, -1)):
            p.drawLine(x, y, x + dx * L, y)
            p.drawLine(x, y, x, y + dy * L)

    def _draw_noise(self, p, w, h, t):
        # heavy static at the start, a faint flicker afterwards
        if t < 900:
            a = 0.9 if t < 700 else (900 - t) / 200 * 0.9
        elif T_JUMP <= t < T_JUMP + 350:
            a = 0.55
        else:
            a = 0.07 + (0.12 if int(t) % 900 < 60 else 0.0)
        p.setOpacity(a)
        ox = self._rnd.randint(0, 255)
        oy = self._rnd.randint(0, 255)
        p.drawTiledPixmap(0, 0, w, h, self._static, ox, oy)
        p.setOpacity(1.0)

    @staticmethod
    def _mix(a, b, k):
        k = max(0.0, min(1.0, k))
        return QColor(int(a.red() + (b.red() - a.red()) * k),
                      int(a.green() + (b.green() - a.green()) * k),
                      int(a.blue() + (b.blue() - a.blue()) * k))


# ----------------------------------------------------------------------
# Public entry point (call on the GUI thread)
# ----------------------------------------------------------------------
_active = []


def show_scare(text: str):
    text = (text or "").strip()[:300] or "الجهاز ده متراقب. اتركه."
    # replace any overlay that is still up
    for ov in list(_active):
        try:
            ov.dismiss()
        except Exception:
            pass
    _active.clear()

    total = int(max(9000, min(17000, 6500 + len(text) * 90)))
    clock = _Clock()
    screens = QGuiApplication.screens()
    primary = QGuiApplication.primaryScreen()

    def done():
        # closing one closes all, and kills the sound
        for ov in list(_active):
            if not ov._closing:
                ov.dismiss()
        _active.clear()
        _stop_sound()

    if primary not in screens:
        primary = screens[0]
    for s in screens:
        ov = ScareOverlay(s, text, clock, total, primary=(s == primary), on_done=done)
        _active.append(ov)
    for ov in _active:
        ov.start()
    _play_sound()
