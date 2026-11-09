"""
splash.py
Full-screen "ARGUS is watching" intro animation shown once when the app
starts. Pure QPainter (no image assets), themed to match app_icon.ico:
ice-cyan eye on near-black, with the same concentric radar rings as the
icon. Click, press any key, or wait ~4.5s to continue into the app.
"""
import math
import random

from PySide6.QtCore import Qt, QTimer, Signal, QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QFont, QRadialGradient, QPen, QPainterPath
from PySide6.QtWidgets import QWidget

ICE = QColor("#7EE8FF")
ICE_DIM = QColor("#2f7d91")
VOID = QColor("#02030a")

DURATION_MS = 4600
FRAME_MS = 16


class ArgusSplash(QWidget):
    """Frameless, full-screen, always-on-top intro. Emits `finished` once."""

    finished = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Window
        )
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setCursor(Qt.BlankCursor)
        self.setStyleSheet("background-color: #02030a;")
        self.showFullScreen()

        self._t0 = 0.0
        self._elapsed = 0.0
        self._done = False
        self._glitch_seed = random.random() * 1000

        # pre-generate static-noise flecks for the opening darkness
        random.seed(7)
        self._flecks = [
            (random.random(), random.random(), random.random())
            for _ in range(140)
        ]

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(FRAME_MS)

    # -- input: anything skips straight to the app -----------------
    def mousePressEvent(self, e):
        self._finish()

    def keyPressEvent(self, e):
        self._finish()

    def _tick(self):
        self._elapsed += FRAME_MS
        if self._elapsed >= DURATION_MS and not self._done:
            self._finish()
            return
        self.update()

    def _finish(self):
        if self._done:
            return
        self._done = True
        self._timer.stop()
        self.close()
        self.finished.emit()

    # -- drawing ------------------------------------------------------
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        t = self._elapsed

        p.fillRect(self.rect(), VOID)

        # phase timings (ms)
        STATIC_END = 700
        OPEN_END = 2100
        HOLD_END = 3300
        FADE_END = DURATION_MS

        # --- phase 0: dead static / flicker, something is waking up ---
        if t < STATIC_END + 250:
            self._draw_static(p, w, h, t)

        if t < STATIC_END:
            p.end()
            return

        # eyelid-open progress 0..1
        if t < OPEN_END:
            open_p = self._ease_out(min(1.0, (t - STATIC_END) / (OPEN_END - STATIC_END)))
        else:
            open_p = 1.0

        # overall intensity pulse used for glow / rings
        pulse = 0.55 + 0.45 * math.sin(t / 420.0)

        self._draw_rings(p, cx, cy, w, h, t, open_p, pulse)
        self._draw_eye(p, cx, cy, w, h, t, open_p, pulse)

        if t > OPEN_END - 300:
            title_p = self._ease_out(min(1.0, (t - (OPEN_END - 300)) / 900.0))
            self._draw_title(p, cx, cy, h, t, title_p)

        # --- final fade to black before handing off ---
        if t > HOLD_END:
            fade = self._ease_in(min(1.0, (t - HOLD_END) / (FADE_END - HOLD_END)))
            p.setOpacity(1.0)
            overlay = QColor(2, 3, 10, int(255 * fade))
            p.fillRect(self.rect(), overlay)

        p.end()

    def _draw_static(self, p, w, h, t):
        fade = 1.0 - max(0.0, (t - 700) / 250.0)
        if fade <= 0:
            return
        p.setOpacity(min(1.0, fade))
        for (fx, fy, fr) in self._flecks:
            shimmer = (math.sin(t / 40.0 + fr * 97.0) + 1) / 2
            if shimmer < 0.55:
                continue
            v = int(30 + 90 * shimmer)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(v, v + 10, v + 14))
            x, y = fx * w, fy * h
            s = 1 + 2 * shimmer
            p.drawRect(QRectF(x, y, s, s))
        p.setOpacity(1.0)

    def _draw_rings(self, p, cx, cy, w, h, t, open_p, pulse):
        base = min(w, h)
        p.setOpacity(0.35 * open_p)
        for i in range(4):
            frac = (t / 2600.0 + i / 4.0) % 1.0
            r = frac * base * 0.62
            alpha = (1.0 - frac) * 0.8
            pen = QPen(ICE_DIM)
            pen.setWidthF(1.2)
            col = QColor(ICE)
            col.setAlphaF(max(0.0, alpha) * open_p)
            pen.setColor(col)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(cx, cy), r, r)
        p.setOpacity(1.0)

    def _draw_eye(self, p, cx, cy, w, h, t, open_p, pulse):
        eye_w = min(w, h) * 0.5
        eye_h = eye_w * 0.46 * open_p

        # subtle horizontal jitter/glitch near the start of the open
        jitter = 0.0
        if 700 < t < 1400:
            jitter = math.sin(t / 18.0) * (1400 - t) / 700.0 * 3.0

        # glow behind the eye
        glow = QRadialGradient(QPointF(cx + jitter, cy), eye_w * 0.95)
        glow_col = QColor(ICE)
        glow_col.setAlphaF(0.22 * open_p * pulse)
        glow.setColorAt(0.0, glow_col)
        transparent = QColor(ICE)
        transparent.setAlphaF(0.0)
        glow.setColorAt(1.0, transparent)
        p.setBrush(glow)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(cx + jitter, cy), eye_w * 0.95, eye_w * 0.95)

        if eye_h < 1:
            return

        # eye outline (almond shape) via two bezier curves
        path = QPainterPath()
        left = QPointF(cx - eye_w / 2 + jitter, cy)
        right = QPointF(cx + eye_w / 2 + jitter, cy)
        path.moveTo(left)
        path.cubicTo(
            QPointF(cx - eye_w * 0.18 + jitter, cy - eye_h),
            QPointF(cx + eye_w * 0.18 + jitter, cy - eye_h),
            right,
        )
        path.cubicTo(
            QPointF(cx + eye_w * 0.18 + jitter, cy + eye_h),
            QPointF(cx - eye_w * 0.18 + jitter, cy + eye_h),
            left,
        )
        path.closeSubpath()

        p.setClipPath(path)

        # sclera: near-black with a faint cold tint
        p.fillPath(path, QColor(6, 10, 16))

        # iris
        iris_r = eye_h * 1.05
        iris_grad = QRadialGradient(QPointF(cx + jitter, cy), iris_r)
        iris_grad.setColorAt(0.0, QColor(20, 40, 46))
        iris_grad.setColorAt(0.55, ICE_DIM)
        iris_core = QColor(ICE)
        iris_core.setAlphaF(0.9)
        iris_grad.setColorAt(0.78, iris_core)
        iris_grad.setColorAt(1.0, QColor(4, 8, 10))
        p.setBrush(iris_grad)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(cx + jitter, cy), iris_r, iris_r)

        # fine iris striations
        p.setOpacity(0.5)
        pen = QPen(QColor(2, 6, 8))
        pen.setWidthF(0.8)
        p.setPen(pen)
        for i in range(28):
            ang = i * (2 * math.pi / 28) + t / 3000.0
            x1 = cx + jitter + math.cos(ang) * iris_r * 0.3
            y1 = cy + math.sin(ang) * iris_r * 0.3
            x2 = cx + jitter + math.cos(ang) * iris_r * 0.98
            y2 = cy + math.sin(ang) * iris_r * 0.98
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        p.setOpacity(1.0)

        # pupil: slit, breathing slightly
        breathe = 0.16 + 0.05 * math.sin(t / 500.0)
        pupil_w = iris_r * breathe
        pupil_h = iris_r * 1.65
        p.setBrush(QColor(0, 0, 0))
        p.setPen(Qt.NoPen)
        p.save()
        p.translate(cx + jitter, cy)
        p.drawEllipse(QRectF(-pupil_w / 2, -pupil_h / 2, pupil_w, pupil_h))
        p.restore()

        # a cold highlight
        p.setBrush(QColor(255, 255, 255, 160))
        p.drawEllipse(QPointF(cx + jitter - iris_r * 0.3, cy - iris_r * 0.35), iris_r * 0.08, iris_r * 0.08)

        p.setClipping(False)

        # eye outline stroke
        pen = QPen(ICE)
        pen.setWidthF(2.0)
        glow_alpha = 0.75 * open_p
        pen_col = QColor(ICE)
        pen_col.setAlphaF(glow_alpha)
        pen.setColor(pen_col)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

    def _draw_title(self, p, cx, cy, h, t, title_p):
        text = "ARGUS"
        font = QFont("Consolas", 34, QFont.Bold)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 10)
        p.setFont(font)

        # glitchy reveal: show a growing, occasionally-scrambled prefix
        n_chars = max(0, min(len(text), int(title_p * (len(text) + 1))))
        shown = text[:n_chars]
        if n_chars < len(text) and random.random() < 0.3:
            glitch_chars = "#$%&XZQ01"
            shown += random.choice(glitch_chars)

        col = QColor(ICE)
        col.setAlphaF(min(1.0, title_p * 1.3))
        p.setPen(col)
        rect = QRectF(0, cy + cy * 0.42, 2 * cx, 60)
        p.drawText(rect, Qt.AlignHCenter | Qt.AlignTop, shown)

        sub_alpha = max(0.0, title_p - 0.5) * 2
        if sub_alpha > 0:
            sub_font = QFont("Consolas", 10)
            sub_font.setLetterSpacing(QFont.AbsoluteSpacing, 3)
            p.setFont(sub_font)
            sub_col = QColor(140, 160, 160)
            sub_col.setAlphaF(sub_alpha * 0.9)
            p.setPen(sub_col)
            sub_rect = QRectF(0, cy + cy * 0.42 + 54, 2 * cx, 30)
            p.drawText(sub_rect, Qt.AlignHCenter | Qt.AlignTop, "ALL EYES ARE OPEN")

    @staticmethod
    def _ease_out(x):
        return 1 - (1 - x) ** 3

    @staticmethod
    def _ease_in(x):
        return x ** 3
