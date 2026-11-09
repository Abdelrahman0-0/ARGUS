"""
setup_ui.py - the ARGUS installer window: dark, eerie, one big watching eye
that opens as the install progresses.
"""
import math
import random
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, QPoint
from PySide6.QtGui import (QPainter, QPainterPath, QColor, QPen, QBrush, QRadialGradient,
                           QCursor, QIcon)
from PySide6.QtWidgets import (QApplication, QWidget, QLabel, QPushButton, QLineEdit, QCheckBox,
                               QStackedWidget, QFileDialog, QMessageBox)

import setup_core as core

ICE = "#7ee8ff"
ICE_DIM = "#2f7d91"
BLOOD = "#ff2e4d"
VOID = "#03040a"
LINE = "#1a1e2e"
TEXT = "#cfd3e6"
DIM = "#6b7190"
FAINT = "#3d425c"
TITLE_FONT = "'Bahnschrift', 'Segoe UI Semibold', 'Segoe UI', sans-serif"
MONO = "'Cascadia Code', 'Consolas', monospace"

QSS = f"""
QWidget {{ color: {TEXT}; font-family: {MONO}; font-size: 12.5px; background: transparent; }}
QLabel#title {{ font-family: {TITLE_FONT}; font-size: 30px; font-weight: 700; color: #ffffff;
               letter-spacing: 9px; }}
QLabel#sub {{ color: {FAINT}; font-size: 10px; letter-spacing: 5px; }}
QLabel#body {{ color: #aab0cc; font-size: 13.5px; }}
QLabel#status {{ color: {ICE}; font-size: 12px; letter-spacing: 1px; }}
QLabel#pct {{ color: {DIM}; font-size: 11px; letter-spacing: 2px; }}
QLabel#err {{ color: {BLOOD}; font-size: 12px; }}
QLabel#close {{ color: {FAINT}; font-size: 15px; }}
QLabel#close:hover {{ color: {BLOOD}; }}
QPushButton {{ background: {ICE}; color: #031219; border: 1px solid {ICE}; padding: 11px 22px;
              font-weight: 700; letter-spacing: 3px; border-radius: 0px; }}
QPushButton:hover {{ background: #b3f2ff; border: 1px solid #b3f2ff; }}
QPushButton:disabled {{ background: transparent; color: {FAINT}; border: 1px solid {LINE}; }}
QPushButton#ghost {{ background: transparent; color: #9aa1bf; border: 1px solid #2a3048; }}
QPushButton#ghost:hover {{ color: {ICE}; border: 1px solid {ICE_DIM}; }}
QPushButton#blood {{ background: transparent; color: {BLOOD}; border: 1px solid {BLOOD}; }}
QPushButton#blood:hover {{ background: {BLOOD}; color: #19000a; }}
QPushButton#small {{ padding: 8px 14px; letter-spacing: 2px; }}
QLineEdit {{ background: #070810; border: 1px solid #2a3048; padding: 9px 10px; color: {TEXT};
            border-radius: 0px; selection-background-color: {ICE_DIM}; }}
QLineEdit:focus {{ border: 1px solid {ICE}; }}
QCheckBox {{ spacing: 11px; color: #aab0cc; padding: 4px 0; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid #2a3048; background: #070810; }}
QCheckBox::indicator:checked {{ background: {ICE}; border: 1px solid {ICE}; }}
QToolTip {{ background: #070810; color: {TEXT}; border: 1px solid #2a3048; }}
"""


class Eye(QWidget):
    """Big watching eye. `target_open` 0..1 drives how open it is."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.target_open = 0.08
        self.openness = 0.0
        self.tint = QColor(ICE)
        self.tint_target = QColor(ICE)
        self._look = QPointF(0, 0)
        self._sweep = 0.0
        self._blink_until = 0.0
        self._next_blink = time.time() + 4
        self._t = QTimer(self)
        self._t.timeout.connect(self._step)
        self._t.start(33)

    def set_tint(self, color: str):
        self.tint_target = QColor(color)

    def _step(self):
        now = time.time()
        target = self.target_open
        if target > 0.5:
            if now >= self._next_blink and now > self._blink_until:
                self._blink_until = now + 0.16
                self._next_blink = now + random.uniform(3.5, 8)
            if now < self._blink_until:
                target = 0.06
        self.openness += (target - self.openness) * (0.45 if target < self.openness else 0.12)
        # colour easing
        c, t = self.tint, self.tint_target
        self.tint = QColor(int(c.red() + (t.red() - c.red()) * 0.1),
                           int(c.green() + (t.green() - c.green()) * 0.1),
                           int(c.blue() + (t.blue() - c.blue()) * 0.1))
        tx = ty = 0.0
        if self.openness > 0.3:
            ctr = self.mapToGlobal(QPoint(self.width() // 2, self.height() // 2))
            d = QCursor.pos() - ctr
            dist = math.hypot(d.x(), d.y()) or 1.0
            k = min(dist / 260.0, 1.0)
            tx, ty = d.x() / dist * k, d.y() / dist * k
        self._look = QPointF(self._look.x() + (tx - self._look.x()) * 0.18,
                             self._look.y() + (ty - self._look.y()) * 0.18)
        self._sweep = (self._sweep + 2.2) % 360
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        s = min(w, h) / 130.0            # everything is drawn in a 130-unit space
        col = QColor(self.tint)
        awake = self.openness > 0.2
        now = time.time()

        ring = QColor(col)
        ring.setAlpha(48 if awake else 24)
        pen = QPen(ring, 1)
        pen.setStyle(Qt.PenStyle.DotLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for r in (47, 59, 71):
            p.drawEllipse(QPointF(cx, cy), r * s, r * s)
        p.setPen(QPen(ring, 1))
        for ang in range(0, 360, 30):
            a = math.radians(ang)
            r1, r2 = 71 * s, (77 if ang % 90 == 0 else 74) * s
            p.drawLine(QPointF(cx + math.cos(a) * r1, cy + math.sin(a) * r1),
                       QPointF(cx + math.cos(a) * r2, cy + math.sin(a) * r2))
        if awake:
            sw = QColor(col)
            sw.setAlpha(170)
            p.setPen(QPen(sw, 2))
            p.drawArc(QRectF(cx - 59 * s, cy - 59 * s, 118 * s, 118 * s),
                      int(-self._sweep * 16), 40 * 16)
            p.drawArc(QRectF(cx - 71 * s, cy - 71 * s, 142 * s, 142 * s),
                      int(self._sweep * 8 * 16), 22 * 16)

        a_half = 74.0 * s
        b = 38.0 * s * self.openness
        path = QPainterPath()
        path.moveTo(cx - a_half, cy)
        path.quadTo(cx, cy - 2 * b, cx + a_half, cy)
        path.quadTo(cx, cy + 2 * b, cx - a_half, cy)
        p.fillPath(path, QColor(2, 3, 7))

        p.save()
        p.setClipPath(path)
        ir = 26.0 * s
        icx = cx + self._look.x() * 22 * s
        icy = cy + self._look.y() * 10 * s * max(self.openness, 0.2)
        g = QRadialGradient(QPointF(icx, icy), ir)
        g.setColorAt(0.0, col.lighter(175))
        g.setColorAt(0.55, col)
        g.setColorAt(1.0, QColor(col.red() // 6, col.green() // 6, col.blue() // 6))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(icx, icy), ir, ir)
        p.setPen(QPen(QColor(0, 0, 0, 85), 1))
        for i in range(36):
            ang = i * 2 * math.pi / 36
            p.drawLine(QPointF(icx + math.cos(ang) * ir * 0.4, icy + math.sin(ang) * ir * 0.4),
                       QPointF(icx + math.cos(ang) * ir, icy + math.sin(ang) * ir))
        pulse = 1.2 * math.sin(now * 2.2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0))
        p.drawEllipse(QPointF(icx, icy), (4.6 + pulse * 0.5) * s, 14 * s)
        p.setBrush(QColor(255, 255, 255, 200))
        p.drawEllipse(QPointF(icx - 8 * s, icy - 9 * s), 2.4 * s, 2.4 * s)
        p.restore()

        lid = QColor(col)
        lid.setAlpha(240 if awake else 130)
        p.setPen(QPen(lid, 1.8 * s))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.end()


P_WELCOME, P_OPTIONS, P_PROGRESS, P_DONE = range(4)

TEXTS = {
    "install": {
        P_WELCOME: ("It never sleeps.\nIt never blinks all at once.\n\n"
                    "Once it opens its eyes on this machine,\nnothing goes unseen."),
        P_OPTIONS: "Where should it live?",
        P_PROGRESS: "Do not look away.",
        P_DONE: "ARGUS now lives on this machine.\nNothing goes unseen.",
    },
    "uninstall": {
        P_WELCOME: ("Once it closes its eyes,\nnothing will watch over this machine.\n\n"
                    "Are you sure you want to let it go?"),
        P_PROGRESS: "Closing its eyes...",
        P_DONE: "ARGUS has been removed.\nThe silence begins.",
    },
}
TITLES = {
    "install": {P_WELCOME: "ARGUS", P_OPTIONS: "ARGUS", P_PROGRESS: "ARGUS", P_DONE: "IT IS AWAKE"},
    "uninstall": {P_WELCOME: "LET IT GO?", P_PROGRESS: "LET IT GO?", P_DONE: "IT SLEEPS"},
}


class SetupWindow(QWidget):
    def __init__(self, uninstall_mode: bool):
        super().__init__()
        self.mode = "uninstall" if uninstall_mode else "install"
        self.res = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
        self.existing = core.existing_install()
        self.target = self.existing or core.default_dir()
        self.page = P_WELCOME
        self.busy = False
        self.version = _version()
        self._drag = None
        self._tw = None
        self._flicker = 0
        self._gen = None

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setFixedSize(820, 520)
        self.setWindowTitle("ARGUS Setup" if not uninstall_mode else "ARGUS Uninstall")
        icon = self.res / "app_icon.ico"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))

        self.eye = Eye(self)
        self.eye.setGeometry(10, 90, 370, 340)
        self.eye.set_tint(BLOOD if uninstall_mode else ICE)

        self.close_lbl = QLabel("✕", self)
        self.close_lbl.setObjectName("close")
        self.close_lbl.setGeometry(780, 12, 28, 24)
        self.close_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_lbl.mousePressEvent = lambda e: self.try_close()

        self.title = QLabel("ARGUS", self)
        self.title.setObjectName("title")
        self.title.setGeometry(400, 56, 400, 50)
        self.sub = QLabel("THE HUNDRED-EYED", self)
        self.sub.setObjectName("sub")
        self.sub.setGeometry(402, 104, 380, 18)
        self.body = QLabel("", self)
        self.body.setObjectName("body")
        self.body.setWordWrap(True)
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.body.setGeometry(402, 146, 390, 110)

        self.stack = QStackedWidget(self)
        self.stack.setGeometry(402, 262, 390, 170)
        self._build_pages()

        self.btn_main = QPushButton("", self)
        self.btn_main.setGeometry(402, 452, 190, 42)
        self.btn_main.clicked.connect(self.on_main)
        self.btn_back = QPushButton("", self)
        self.btn_back.setObjectName("ghost")
        self.btn_back.setGeometry(604, 452, 150, 42)
        self.btn_back.clicked.connect(self.on_back)

        ver = QLabel(f"v{self.version}", self)
        ver.setObjectName("sub")
        ver.setGeometry(24, 488, 120, 18)

        self.tw_timer = QTimer(self)
        self.tw_timer.timeout.connect(self._tw_step)
        self.tw_timer.start(20)
        self.glitch_timer = QTimer(self)
        self.glitch_timer.timeout.connect(self._glitch)
        self.glitch_timer.start(90)
        self.run_timer = QTimer(self)
        self.run_timer.setSingleShot(True)
        self.run_timer.timeout.connect(self._run_step)

        self.upgrade = "--upgrade" in sys.argv and not uninstall_mode and self.existing is not None
        if self.upgrade:
            QTimer.singleShot(300, self.start_run)
        else:
            self.show_page(P_WELCOME)

    # ------------- pages -------------
    def _build_pages(self):
        # 0: welcome - (uninstall: keep-data checkbox)
        p0 = QWidget()
        self.keep_data = QCheckBox("Keep its memories (logs, passwords, phone setup)", p0)
        self.keep_data.setChecked(True)
        self.keep_data.setGeometry(0, 20, 390, 28)
        self.keep_data.setVisible(self.mode == "uninstall")
        self.renew_note = QLabel("", p0)
        self.renew_note.setObjectName("sub")
        self.renew_note.setWordWrap(True)
        self.renew_note.setGeometry(0, 10, 390, 60)
        self.stack.addWidget(p0)
        # 1: options
        p1 = QWidget()
        self.path_edit = QLineEdit(str(self.target), p1)
        self.path_edit.setGeometry(0, 0, 290, 38)
        browse = QPushButton("BROWSE", p1)
        browse.setObjectName("ghost")
        browse.setGeometry(298, 0, 92, 38)
        browse.clicked.connect(self.browse)
        self.cb_desktop = QCheckBox("Leave a mark on the desktop", p1)
        self.cb_start = QCheckBox("Place it in the Start menu", p1)
        self.cb_startup = QCheckBox("Wake with Windows (starts hidden in the tray)", p1)
        for i, cb in enumerate((self.cb_desktop, self.cb_start, self.cb_startup)):
            cb.setChecked(True)
            cb.setGeometry(0, 52 + i * 28, 390, 26)
        self.err_lbl = QLabel("", p1)
        self.err_lbl.setObjectName("err")
        self.err_lbl.setWordWrap(True)
        self.err_lbl.setGeometry(0, 140, 390, 30)
        self.stack.addWidget(p1)
        # 2: progress
        p2 = QWidget()
        self.status = QLabel("", p2)
        self.status.setObjectName("status")
        self.status.setGeometry(0, 40, 300, 20)
        self.pct = QLabel("0%", p2)
        self.pct.setObjectName("pct")
        self.pct.setGeometry(330, 40, 60, 20)
        self.pct.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.bar_bg = QWidget(p2)
        self.bar_bg.setGeometry(0, 70, 390, 4)
        self.bar_bg.setStyleSheet("background:#12151f;")
        self.bar_fg = QWidget(p2)
        self.bar_fg.setGeometry(0, 70, 0, 4)
        self.bar_fg.setStyleSheet(f"background:{BLOOD if self.mode == 'uninstall' else ICE};")
        self.fail_lbl = QLabel("", p2)
        self.fail_lbl.setObjectName("err")
        self.fail_lbl.setWordWrap(True)
        self.fail_lbl.setGeometry(0, 90, 390, 70)
        self.stack.addWidget(p2)
        # 3: done
        p3 = QWidget()
        self.cb_launch = QCheckBox("Open its eyes now", p3)
        self.cb_launch.setChecked(True)
        self.cb_launch.setGeometry(0, 40, 390, 28)
        self.cb_launch.setVisible(self.mode == "install")
        self.stack.addWidget(p3)

    def show_page(self, page):
        self.page = page
        m = self.mode
        self.stack.setCurrentIndex(page)
        self.title.setText(TITLES[m].get(page, "ARGUS"))
        self._title_full = self.title.text()
        self.sub.setText("THE HUNDRED-EYED" if m == "install" else "NOTHING GOES UNSEEN")
        self.type_text(TEXTS[m].get(page, ""))
        main, back = "", ""
        if page == P_WELCOME:
            if m == "install":
                main = "RENEW IT" if self.existing else "LET IT IN"
                back = "TURN BACK"
                self.renew_note.setText("It is already here. Your logs and settings stay untouched."
                                        if self.existing else "")
            else:
                main, back = "CLOSE ITS EYES", "KEEP WATCHING"
            self.eye.target_open = 0.55
        elif page == P_OPTIONS:
            main, back = "INSTALL", "BACK"
            self.err_lbl.setText("")
            self.eye.target_open = 0.7
        elif page == P_PROGRESS:
            self.eye.target_open = 0.12 if m == "install" else 1.0
        elif page == P_DONE:
            main, back = ("OPEN ITS EYES", "") if m == "install" else ("CLOSE", "")
            self.eye.target_open = 1.0 if m == "install" else 0.04
            if m == "install":
                self.eye.set_tint(BLOOD)
                QTimer.singleShot(1300, lambda: self.eye.set_tint(ICE))
        self.btn_main.setText(main)
        self.btn_main.setVisible(bool(main))
        self.btn_main.setObjectName("blood" if (m == "uninstall" and page == P_WELCOME) else "")
        st = self.btn_main.style()
        st.unpolish(self.btn_main)
        st.polish(self.btn_main)
        self.btn_back.setText(back)
        self.btn_back.setVisible(bool(back))

    # ------------- buttons -------------
    def on_main(self):
        if self.busy:
            return
        if self.page == P_WELCOME:
            if self.mode == "install":
                self.show_page(P_OPTIONS)
            else:
                self.start_run()
        elif self.page == P_OPTIONS:
            self.start_run()
        elif self.page == P_DONE:
            if self.mode == "install" and self.cb_launch.isChecked():
                core.launch_argus(self.target)
            if self.mode == "uninstall":
                core.schedule_final_cleanup(self.target)
            QApplication.quit()

    def on_back(self):
        if self.busy:
            return
        if self.page == P_OPTIONS:
            self.show_page(P_WELCOME)
        elif self.page == P_WELCOME:
            QApplication.quit()

    def browse(self):
        d = QFileDialog.getExistingDirectory(self, "Where should ARGUS live?", self.path_edit.text())
        if d:
            self.path_edit.setText(str(Path(d) / "ARGUS") if Path(d).name.lower() != "argus" else d)

    def try_close(self):
        if self.busy:
            return
        if self.page == P_DONE:
            self.on_main()
        else:
            QApplication.quit()

    # ------------- run -------------
    def start_run(self):
        try:
            if self.mode == "install":
                if self.upgrade:
                    self.target = self.existing
                    opts = {}
                else:
                    self.target = Path(self.path_edit.text().strip() or str(core.default_dir()))
                    opts = {"desktop": self.cb_desktop.isChecked(), "start": self.cb_start.isChecked(),
                            "startup": self.cb_startup.isChecked()}
                payload = self.res / "payload" / "ARGUS.exe"
                self._gen = core.install(self.target, opts, payload, self.res / "app_icon.ico",
                                         self.version, sys.executable if getattr(sys, "frozen", False) else None,
                                         wait_close=self.upgrade)
            else:
                self._gen = core.uninstall(self.target, self.keep_data.isChecked())
            first = next(self._gen)           # runs the pre-checks
        except core.SetupError as e:
            self._gen = None
            if self.upgrade:
                QMessageBox.warning(self, "ARGUS", str(e))
                QApplication.quit()
            elif self.mode == "install":
                self.show_page(P_OPTIONS)
                self.err_lbl.setText(str(e))
            else:
                QMessageBox.warning(self, "ARGUS", str(e))
            return
        except Exception as e:                # noqa
            self._gen = None
            QMessageBox.warning(self, "ARGUS", f"Something resisted: {e}")
            return
        self.busy = True
        self.show_page(P_PROGRESS)
        self.fail_lbl.setText("")
        self._apply_progress(*first)
        self.run_timer.start(first[2])

    def _apply_progress(self, frac, msg, delay):
        self.status.setText(msg)
        self.pct.setText(f"{int(frac * 100)}%")
        self.bar_fg.setFixedWidth(int(390 * frac))
        self.eye.target_open = (0.12 + 0.88 * frac) if self.mode == "install" else (1.0 - 0.96 * frac)

    def _run_step(self):
        try:
            step = next(self._gen)
        except StopIteration:
            self.busy = False
            if self.upgrade:                      # updated from inside ARGUS: relaunch and leave
                self.status.setText("It is awake.")
                core.launch_argus(self.target)
                QTimer.singleShot(1200, QApplication.quit)
                return
            self.show_page(P_DONE)
            return
        except Exception as e:                # noqa
            self.busy = False
            self.status.setText("It resisted.")
            self.fail_lbl.setText(str(e))
            self.btn_main.setText("CLOSE")
            self.btn_main.setVisible(True)
            self.page = P_DONE
            self.btn_main.clicked.disconnect()
            self.btn_main.clicked.connect(QApplication.quit)
            return
        self._apply_progress(*step)
        self.run_timer.start(step[2])

    # ------------- eerie text effects -------------
    def type_text(self, text):
        self._tw = [text, 0]
        self.body.setText("")

    def _tw_step(self):
        if self._tw:
            text, i = self._tw
            i = min(i + 2, len(text))
            self._tw[1] = i
            self.body.setText(text[:i] + ("▌" if i < len(text) else ""))
            if i >= len(text):
                self._tw = None

    def _glitch(self):
        full = getattr(self, "_title_full", "ARGUS")
        if self._flicker > 0:
            self._flicker -= 1
            if self._flicker == 0:
                self.title.setText(full)
                self.title.move(400, 56)
            return
        if random.random() < 0.05 and full:
            i = random.randrange(len(full))
            junk = random.choice("█▓▒░#?/\\")
            self.title.setText(full[:i] + junk + full[i + 1:])
            self.title.move(400 + random.choice((-3, 3)), 56)
            self._flicker = 1
        if random.random() < 0.03:
            self.update()

    # ------------- window chrome -------------
    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(VOID))
        g = QRadialGradient(QPointF(190, 260), 520)
        c = QColor(BLOOD if self.mode == "uninstall" else ICE)
        c.setAlpha(26)
        g.setColorAt(0.0, c)
        g.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), QBrush(g))
        v = QRadialGradient(QPointF(self.width() / 2, self.height() / 2), self.width() * 0.62)
        v.setColorAt(0.55, QColor(0, 0, 0, 0))
        v.setColorAt(1.0, QColor(0, 0, 0, 200))
        p.fillRect(self.rect(), QBrush(v))
        p.setPen(QPen(QColor(255, 255, 255, 9), 1))       # faint scanlines
        for y in range(0, self.height(), 3):
            p.drawLine(0, y, self.width(), y)
        p.setPen(QPen(QColor(LINE), 1))
        p.drawRect(0, 0, self.width() - 1, self.height() - 1)
        p.setPen(QPen(QColor(ICE_DIM if self.mode == "install" else BLOOD), 1))
        p.drawLine(400, 130, 470, 130)                    # accent rule under the title
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def closeEvent(self, e):
        if self.busy:
            e.ignore()
        else:
            e.accept()


def _version():
    try:
        import version
        return version.APP_VERSION
    except Exception:
        return "2.1.0"


def run(argv):
    uninstall = "--uninstall" in argv or Path(sys.executable).name.lower().startswith("uninstall")
    app = QApplication(argv)
    app.setStyleSheet(QSS)
    icon = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "app_icon.ico"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    w = SetupWindow(uninstall)
    w.show()
    return app.exec()
