"""
gui_app.py
Desktop GUI (PySide6) for the device monitor.

Features:
- Sidebar navigation: Dashboard / Window Activity / Activity Level /
  Browser Activity / Away Mode & Alerts
- Start/Stop button that controls all background trackers
- Real-time updates: window switches are pushed to the UI the instant
  they happen (via Qt signals from the tracker's on_event callback),
  not on the next timed refresh - this removes the lag between actually
  switching windows and it showing up here
- Away Mode: set a normal password, tell the app "I'm leaving", and get
  a clearly visible red alert (with time + app name) for anything that
  happens on the device until you come back and unlock it with the
  password

- Phone app (v2): push alerts + remote lock / siren / screenshot via a
  private PWA (see remote_server.py and README)
- While away mode is armed, stopping, quitting, clearing logs or changing
  passwords all require the away password

Run with:
    python gui_app.py
"""
import sys
import re
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QFont, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QPlainTextEdit, QListWidget,
    QListWidgetItem, QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
    QStackedWidget, QInputDialog, QMessageBox, QSizePolicy,
    QGraphicsDropShadowEffect, QSystemTrayIcon, QMenu, QCheckBox, QFileDialog,
)

import window_tracker
import activity_logger
import browser_history
import away_mode
import presence
import startup
import tailscale_helper
import updater
from away_mode import AwayMode
from remote_server import RemoteService

from paths import APP_DIR, LOG_DIR, ICON_PATH
from splash import ArgusSplash

BASE_DIR = APP_DIR

WINDOW_LOG = LOG_DIR / "window_activity.log"
ACTIVITY_LOG = LOG_DIR / "activity_level.log"
BROWSER_LOG = LOG_DIR / "browser_activity.log"

MAX_DISPLAY_LINES = 500

_AWAY = None  # set by MainWindow so any widget can ask for the away password


def guard(parent, title="Password required") -> bool:
    """While away mode is armed, anything destructive (clearing logs,
    quitting, changing passwords...) needs the away password."""
    if _AWAY is None or not _AWAY.armed:
        return True
    wait = _AWAY.lockout_remaining()
    if wait:
        QMessageBox.warning(parent, "Locked",
                            f"Too many wrong attempts. Try again in {wait} seconds.")
        return False
    pw, ok = QInputDialog.getText(parent, title, "Away-mode password:", QLineEdit.Password)
    if not ok:
        return False
    if _AWAY.check_password(pw):
        return True
    QMessageBox.critical(parent, "Wrong password", "That password is not correct.")
    return False

# ----------------------------------------------------------------------
# Theme - true black, security/monitoring console look
# ----------------------------------------------------------------------
MONO_FONT = "'JetBrains Mono', 'Cascadia Code', 'Consolas', monospace"

ACCENT = "#7EE8FF"       # icon ice-cyan
ACCENT_DIM = "#2f7d91"
ACCENT_HOVER = "#b3f2ff"
DANGER = "#ff3b4e"
DANGER_HOVER = "#ff6470"
SAFE = "#7EE8FF"
SAFE_HOVER = "#b3f2ff"

STYLESHEET = f"""
* {{
    outline: none;
}}
QMainWindow, QWidget {{
    background-color: #000000;
    color: #d4d8d6;
    font-family: {MONO_FONT};
    font-size: 12.5px;
}}
QFrame#topbar {{
    background-color: #050606;
    border-bottom: 1px solid #171c1a;
}}
QLabel#topbarBrand {{
    color: #ffffff;
    font-weight: 700;
    font-size: 13px;
    letter-spacing: 2px;
}}
QLabel#topbarClock {{
    color: #6f7a76;
    font-size: 12px;
    letter-spacing: 1px;
}}
QLabel#ledOn {{
    color: {ACCENT};
    font-weight: 700;
    font-size: 11.5px;
    letter-spacing: 1px;
}}
QLabel#ledOff {{
    color: #4a4f4d;
    font-weight: 700;
    font-size: 11.5px;
    letter-spacing: 1px;
}}
QLabel#ledAlert {{
    color: {DANGER};
    font-weight: 700;
    font-size: 11.5px;
    letter-spacing: 1px;
}}
QFrame#sidebar {{
    background-color: #000000;
    border-right: 1px solid #141816;
}}
QLabel#brandSub {{
    font-size: 10px;
    color: #4a4f4d;
    padding: 0 20px 14px 20px;
    letter-spacing: 1px;
}}
QListWidget#navList {{
    background-color: transparent;
    border: none;
    outline: none;
    padding: 6px 8px;
}}
QListWidget#navList::item {{
    padding: 11px 12px;
    border-radius: 3px;
    margin-bottom: 2px;
    color: #767c7a;
    font-weight: 600;
    letter-spacing: 0.5px;
}}
QListWidget#navList::item:selected {{
    background-color: #0a0f0d;
    color: {ACCENT};
    border-left: 2px solid {ACCENT};
    font-weight: 700;
}}
QListWidget#navList::item:hover:!selected {{
    background-color: #080a09;
    color: #a8aeac;
}}
QPushButton {{
    background-color: {ACCENT};
    color: #031219;
    border: none;
    padding: 9px 18px;
    border-radius: 3px;
    font-weight: 700;
    letter-spacing: 0.6px;
}}
QPushButton:hover {{
    background-color: {ACCENT_HOVER};
}}
QPushButton:pressed {{
    background-color: {ACCENT_DIM};
}}
QPushButton#stopBtn, QPushButton#dangerBtn {{
    background-color: transparent;
    color: {DANGER};
    border: 1px solid {DANGER};
}}
QPushButton#stopBtn:hover, QPushButton#dangerBtn:hover {{
    background-color: {DANGER};
    color: #1a0002;
}}
QPushButton#safeBtn {{
    background-color: {SAFE};
    color: #031219;
}}
QPushButton#safeBtn:hover {{
    background-color: {SAFE_HOVER};
}}
QPushButton#ghostBtn {{
    background-color: transparent;
    border: 1px solid #222825;
    color: #9aa19e;
}}
QPushButton#ghostBtn:hover {{
    background-color: #0a0d0c;
    border: 1px solid {ACCENT_DIM};
    color: {ACCENT};
}}
QLineEdit {{
    background-color: #060707;
    border: 1px solid #1c211f;
    border-radius: 3px;
    padding: 9px 11px;
    color: #d4d8d6;
    selection-background-color: {ACCENT_DIM};
}}
QLineEdit:focus {{
    border: 1px solid {ACCENT};
}}
QPlainTextEdit {{
    background-color: #030404;
    color: #bfeeff;
    border: 1px solid #141816;
    border-radius: 4px;
    font-family: {MONO_FONT};
    font-size: 12px;
    padding: 8px;
}}
QTableWidget {{
    background-color: #030404;
    alternate-background-color: #07090a;
    gridline-color: #14181a;
    border: 1px solid #14181a;
    border-radius: 4px;
    selection-background-color: #0a1512;
    selection-color: {ACCENT};
}}
QTableWidget::item {{
    padding: 7px 8px;
    border-bottom: 1px solid #101312;
}}
QHeaderView::section {{
    background-color: #07090a;
    color: #6f7a76;
    padding: 10px 8px;
    border: none;
    border-bottom: 1px solid #171c1a;
    font-weight: 700;
    font-size: 10.5px;
    letter-spacing: 1px;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 9px;
}}
QScrollBar::handle:vertical {{
    background: #1c211f;
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: #2a3230;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QLabel#statusPillRunning {{
    color: {ACCENT};
    font-weight: 700;
    font-size: 11.5px;
    letter-spacing: 0.8px;
}}
QLabel#statusPillStopped {{
    color: #55605c;
    font-weight: 700;
    font-size: 11.5px;
    letter-spacing: 0.8px;
}}
QLabel#pageTitle {{
    font-size: 19px;
    font-weight: 700;
    color: #ffffff;
    letter-spacing: 1px;
}}
QLabel#pageSubtitle {{
    font-size: 12px;
    color: #6f7a76;
}}
QLabel#sectionTitle {{
    font-size: 11.5px;
    font-weight: 700;
    color: #9aa19e;
    letter-spacing: 1.5px;
}}
QFrame#card {{
    background-color: #030404;
    border: 1px solid #14181a;
    border-radius: 4px;
    border-left: 2px solid {ACCENT_DIM};
}}
QFrame#divider {{
    background-color: #14181a;
    max-height: 1px;
    min-height: 1px;
}}
QFrame#awayBanner {{
    background-color: #170406;
    border: 1px solid {DANGER};
    border-radius: 4px;
}}
QLabel#awayBannerText {{
    color: {DANGER};
    font-weight: 700;
    font-size: 13px;
    letter-spacing: 0.5px;
}}
"""


def tail_lines(path: Path, max_lines: int = MAX_DISPLAY_LINES):
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        return lines[-max_lines:]
    except Exception:
        return []


# ----------------------------------------------------------------------
# Thread -> GUI bridge
# ----------------------------------------------------------------------
class EventBus(QObject):
    """Background tracker threads call these via Qt signals, which are
    thread-safe and get delivered on the GUI thread - this is what makes
    the UI update instantly instead of waiting for a timer to re-read the
    log files."""
    window_switched = Signal(str, str, str)      # process, title, time
    activity_tick = Signal(int, int)              # keys, clicks
    browser_visit = Signal(str, str, str, str)     # browser, url, title, time
    alert_added = Signal(dict)


class MonitorController:
    """Owns the background threads and lets the GUI start/stop them."""

    def __init__(self, bus: EventBus, away: AwayMode):
        self.bus = bus
        self.away = away
        self.stop_event = None
        self.threads = []
        self.running = False

    def _on_window_event(self, process_name, title, ts):
        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
        self.bus.window_switched.emit(process_name or "unknown", title, ts_str)
        self.away.report_app_switch(process_name, title)

    def _on_activity_event(self, keys, clicks):
        self.bus.activity_tick.emit(keys, clicks)

    def _on_browser_event(self, browser_name, url, title, visited_at):
        ts_str = visited_at.strftime("%Y-%m-%d %H:%M:%S")
        self.bus.browser_visit.emit(browser_name, url, title, ts_str)
        self.away.report_event("BROWSER", f"{browser_name}: {title or url}")

    def start(self):
        if self.running:
            return
        self.stop_event = threading.Event()
        self.threads = [
            threading.Thread(
                target=window_tracker.run,
                args=(self.stop_event,), kwargs={"on_event": self._on_window_event},
                daemon=True,
            ),
            threading.Thread(
                target=activity_logger.run,
                args=(self.stop_event,), kwargs={"on_event": self._on_activity_event},
                daemon=True,
            ),
            threading.Thread(
                target=browser_history.run,
                args=(self.stop_event, 60), kwargs={"on_event": self._on_browser_event},
                daemon=True,
            ),
            threading.Thread(
                target=presence.run, args=(self.stop_event, self.away), daemon=True,
            ),
        ]
        for t in self.threads:
            t.start()
        self.running = True

    def stop(self):
        if not self.running:
            return
        self.stop_event.set()
        self.threads = []
        self.running = False


# ----------------------------------------------------------------------
# Reusable widgets
# ----------------------------------------------------------------------
class StatCard(QFrame):
    def __init__(self, title: str, value: str = "0"):
        super().__init__()
        self.setObjectName("card")
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(18, 16, 18, 16)
        vbox.setSpacing(6)
        self.value_lbl = QLabel(value)
        self.value_lbl.setStyleSheet(f"font-size: 30px; font-weight: 700; color: {ACCENT}; border: none;")
        title_lbl = QLabel(title.upper())
        title_lbl.setStyleSheet("color: #6f6f7a; font-size: 11px; font-weight: 600; letter-spacing: 0.5px; border: none;")
        vbox.addWidget(self.value_lbl)
        vbox.addWidget(title_lbl)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 4)
        shadow.setColor(Qt.black)
        self.setGraphicsEffect(shadow)

    def set_value(self, value):
        self.value_lbl.setText(str(value))


class LogViewerWidget(QWidget):
    """Search box + read-only text view for one log file, refreshed
    frequently for a near-live feel."""

    def __init__(self, log_path: Path, refresh_ms: int = 800, parent=None,
                 title: str = None, subtitle: str = None):
        super().__init__(parent)
        self.log_path = log_path

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        if title:
            title_lbl = QLabel(title)
            title_lbl.setObjectName("pageTitle")
            layout.addWidget(title_lbl)
        if subtitle:
            subtitle_lbl = QLabel(subtitle)
            subtitle_lbl.setObjectName("pageSubtitle")
            layout.addWidget(subtitle_lbl)

        search_row = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search in this log...")
        self.search_box.textChanged.connect(self.refresh)
        search_row.addWidget(self.search_box)

        self.clear_btn = QPushButton("CLEAR LOG")
        self.clear_btn.setObjectName("dangerBtn")
        self.clear_btn.clicked.connect(self.clear_log)
        search_row.addWidget(self.clear_btn)
        layout.addLayout(search_row)

        self.text_view = QPlainTextEdit()
        self.text_view.setReadOnly(True)
        layout.addWidget(self.text_view)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(refresh_ms)
        self.refresh()

    def refresh(self):
        lines = tail_lines(self.log_path)
        query = self.search_box.text().strip().lower()
        if query:
            lines = [ln for ln in lines if query in ln.lower()]

        scrollbar = self.text_view.verticalScrollBar()
        was_at_bottom = scrollbar.value() >= scrollbar.maximum() - 4

        self.text_view.setPlainText("".join(lines))
        if was_at_bottom:
            scrollbar.setValue(scrollbar.maximum())

    def prepend_live_line(self, line: str):
        """Called instantly from the event bus for a live feel, on top of
        the periodic refresh."""
        cursor = self.text_view.textCursor()
        was_at_bottom = True
        self.text_view.appendPlainText(line)

    def clear_log(self):
        if not guard(self, "Clear log"):
            return
        confirm = QMessageBox.question(
            self, "Clear log",
            f"This permanently deletes everything in {self.log_path.name}. Continue?",
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            open(self.log_path, "w", encoding="utf-8").close()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not clear the log: {e}")
            return
        self.text_view.setPlainText("")


WINDOW_LINE_RE = re.compile(
    r"^\[(?P<ts>[^\]]+)\]\s*APP=(?P<app>.*?)\s*\|\s*TITLE=(?P<title>.*)$"
)
ACTIVITY_LINE_RE = re.compile(
    r"^\[(?P<ts>[^\]]+)\]\s*KEYSTROKES=(?P<keys>\d+)\s*CLICKS=(?P<clicks>\d+)$"
)


class WindowActivityWidget(QWidget):
    """Structured, searchable table of app/window switches - replaces the
    raw text log with Time / Application / Window Title columns, newest
    first, updated live."""

    def __init__(self, log_path: Path, refresh_ms: int = 1000, parent=None):
        super().__init__(parent)
        self.log_path = log_path
        self._loaded_line_count = 0

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel("WINDOW ACTIVITY")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        subtitle = QLabel("Every app/window switch, most recent first.")
        subtitle.setObjectName("pageSubtitle")
        layout.addWidget(subtitle)

        search_row = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search by app or window title...")
        self.search_box.textChanged.connect(self.apply_filter)
        search_row.addWidget(self.search_box)

        self.clear_btn = QPushButton("CLEAR LOG")
        self.clear_btn.setObjectName("dangerBtn")
        self.clear_btn.clicked.connect(self.clear_log)
        search_row.addWidget(self.clear_btn)
        layout.addLayout(search_row)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Time", "Application", "Window Title"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_from_disk)
        self.timer.start(refresh_ms)
        self.refresh_from_disk()

    def _insert_row(self, ts: str, app: str, title_text: str, at_top: bool = True):
        row = 0 if at_top else self.table.rowCount()
        self.table.insertRow(row)

        time_item = QTableWidgetItem(ts)
        time_item.setForeground(Qt.gray)

        app_item = QTableWidgetItem(app)
        app_item.setForeground(Qt.white)
        f = app_item.font()
        f.setBold(True)
        app_item.setFont(f)

        title_item = QTableWidgetItem(title_text)

        self.table.setItem(row, 0, time_item)
        self.table.setItem(row, 1, app_item)
        self.table.setItem(row, 2, title_item)

    def add_live_row(self, process_name: str, title_text: str, ts_str: str):
        self._insert_row(ts_str, process_name, title_text, at_top=True)
        self.apply_filter()

    def refresh_from_disk(self):
        """Loads the log file once on startup (or if it grew from outside
        the running session) without duplicating already-added live rows."""
        lines = tail_lines(self.log_path)
        if len(lines) <= self._loaded_line_count and self.table.rowCount() > 0:
            return
        self._loaded_line_count = len(lines)

        self.table.setRowCount(0)
        for line in reversed(lines[-MAX_DISPLAY_LINES:]):
            m = WINDOW_LINE_RE.match(line.strip())
            if m:
                self._insert_row(m.group("ts"), m.group("app"), m.group("title"), at_top=False)
        self.apply_filter()

    def apply_filter(self):
        query = self.search_box.text().strip().lower()
        for row in range(self.table.rowCount()):
            if not query:
                self.table.setRowHidden(row, False)
                continue
            app_text = self.table.item(row, 1).text().lower()
            title_text = self.table.item(row, 2).text().lower()
            self.table.setRowHidden(row, query not in app_text and query not in title_text)

    def clear_log(self):
        if not guard(self, "Clear log"):
            return
        confirm = QMessageBox.question(
            self, "Clear log",
            "This permanently deletes the entire window/app activity log. Continue?",
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            open(self.log_path, "w", encoding="utf-8").close()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not clear the log: {e}")
            return
        self.table.setRowCount(0)
        self._loaded_line_count = 0


class ActivityLevelWidget(QWidget):
    """Summary cards + a table of keystroke/click activity counts over
    time. Never shows what was typed - only how active you were."""

    def __init__(self, log_path: Path, refresh_ms: int = 1000, parent=None):
        super().__init__(parent)
        self.log_path = log_path
        self._loaded_line_count = 0
        self.total_keys = 0
        self.total_clicks = 0

        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        title = QLabel("ACTIVITY LEVEL")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "How actively the keyboard/mouse are used, in counts only - "
            "never what was typed or clicked on."
        )
        subtitle.setObjectName("pageSubtitle")
        layout.addWidget(subtitle)

        cards_row = QHBoxLayout()
        cards_row.setSpacing(14)
        self.keys_card = StatCard("Total keystrokes")
        self.clicks_card = StatCard("Total clicks")
        self.last_update_card = StatCard("Last update", "—")
        for c in (self.keys_card, self.clicks_card, self.last_update_card):
            cards_row.addWidget(c)
        layout.addLayout(cards_row)

        table_title_row = QHBoxLayout()
        table_title = QLabel("ACTIVITY BY INTERVAL")
        table_title.setObjectName("sectionTitle")
        table_title_row.addWidget(table_title)
        table_title_row.addStretch()
        self.clear_btn = QPushButton("CLEAR LOG")
        self.clear_btn.setObjectName("dangerBtn")
        self.clear_btn.clicked.connect(self.clear_log)
        table_title_row.addWidget(self.clear_btn)
        layout.addLayout(table_title_row)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Time", "Keystrokes", "Clicks"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_from_disk)
        self.timer.start(refresh_ms)
        self.refresh_from_disk()

    def _insert_row(self, ts: str, keys: str, clicks: str, at_top: bool = True):
        row = 0 if at_top else self.table.rowCount()
        self.table.insertRow(row)
        time_item = QTableWidgetItem(ts)
        time_item.setForeground(Qt.gray)
        keys_item = QTableWidgetItem(str(keys))
        clicks_item = QTableWidgetItem(str(clicks))
        self.table.setItem(row, 0, time_item)
        self.table.setItem(row, 1, keys_item)
        self.table.setItem(row, 2, clicks_item)

    def add_live_row(self, keys: int, clicks: int):
        ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._insert_row(ts_str, keys, clicks, at_top=True)
        self.total_keys += keys
        self.total_clicks += clicks
        self.keys_card.set_value(self.total_keys)
        self.clicks_card.set_value(self.total_clicks)
        self.last_update_card.set_value(datetime.now().strftime("%H:%M:%S"))

    def refresh_from_disk(self):
        lines = tail_lines(self.log_path)
        if len(lines) <= self._loaded_line_count and self.table.rowCount() > 0:
            return
        self._loaded_line_count = len(lines)

        self.table.setRowCount(0)
        total_keys = 0
        total_clicks = 0
        last_ts = "—"
        for line in reversed(lines[-MAX_DISPLAY_LINES:]):
            m = ACTIVITY_LINE_RE.match(line.strip())
            if m:
                keys, clicks = int(m.group("keys")), int(m.group("clicks"))
                total_keys += keys
                total_clicks += clicks
                if last_ts == "—":
                    last_ts = m.group("ts").split(" ")[-1]
                self._insert_row(m.group("ts"), keys, clicks, at_top=False)

        self.total_keys = total_keys
        self.total_clicks = total_clicks
        self.keys_card.set_value(self.total_keys)
        self.clicks_card.set_value(self.total_clicks)
        self.last_update_card.set_value(last_ts)

    def clear_log(self):
        if not guard(self, "Clear log"):
            return
        confirm = QMessageBox.question(
            self, "Clear log",
            "This permanently deletes the entire activity-level log. Continue?",
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            open(self.log_path, "w", encoding="utf-8").close()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not clear the log: {e}")
            return
        self.table.setRowCount(0)
        self._loaded_line_count = 0
        self.total_keys = 0
        self.total_clicks = 0
        self.keys_card.set_value(0)
        self.clicks_card.set_value(0)
        self.last_update_card.set_value("—")


class DashboardWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        title = QLabel("OVERVIEW")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        subtitle = QLabel("Live snapshot of activity since monitoring started.")
        subtitle.setObjectName("pageSubtitle")
        layout.addWidget(subtitle)

        counts_row = QHBoxLayout()
        counts_row.setSpacing(14)
        self.window_card = StatCard("Window events")
        self.activity_card = StatCard("Activity ticks (keys/clicks)")
        self.browser_card = StatCard("Browser entries")
        for c in (self.window_card, self.activity_card, self.browser_card):
            counts_row.addWidget(c)
        layout.addLayout(counts_row)

        top_apps_title = QLabel("MOST USED APPLICATIONS")
        top_apps_title.setObjectName("sectionTitle")
        layout.addWidget(top_apps_title)

        self.top_apps_table = QTableWidget(0, 2)
        self.top_apps_table.setHorizontalHeaderLabels(["Application", "Times switched to"])
        self.top_apps_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.top_apps_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.top_apps_table.verticalHeader().setVisible(False)
        self.top_apps_table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.top_apps_table)

        self.window_events = 0
        self.activity_events = 0
        self.browser_events = 0

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_top_apps)
        self.timer.start(1500)
        self.refresh_top_apps()

    def bump_window(self):
        self.window_events += 1
        self.window_card.set_value(self.window_events)

    def bump_activity(self):
        self.activity_events += 1
        self.activity_card.set_value(self.activity_events)

    def bump_browser(self):
        self.browser_events += 1
        self.browser_card.set_value(self.browser_events)

    def refresh_top_apps(self):
        window_lines = tail_lines(WINDOW_LOG, max_lines=100000)
        app_counter = Counter()
        pattern = re.compile(r"APP=([^\|]+)\|")
        for line in window_lines:
            m = pattern.search(line)
            if m:
                app_counter[m.group(1).strip()] += 1

        top_apps = app_counter.most_common(10)
        self.top_apps_table.setRowCount(len(top_apps))
        for row, (app_name, count) in enumerate(top_apps):
            self.top_apps_table.setItem(row, 0, QTableWidgetItem(app_name))
            self.top_apps_table.setItem(row, 1, QTableWidgetItem(str(count)))


class AwayModeWidget(QWidget):
    """Set a normal password, arm/disarm away mode, and show red alerts
    for anything that happened while it was armed."""

    def __init__(self, away: AwayMode, parent=None):
        super().__init__(parent)
        self.away = away

        layout = QVBoxLayout(self)
        title = QLabel("AWAY MODE & ALERTS")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        desc = QLabel(
            "Tell the app when you're leaving. After a 15-second grace "
            "period, any keyboard or mouse use is flagged (and pushed to "
            "your phone if the phone app is set up). App switches and "
            "browsing only count when someone actually touched the device."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #9a9bab;")
        layout.addWidget(desc)

        # banner
        self.banner = QFrame()
        self.banner.setObjectName("awayBanner")
        banner_layout = QHBoxLayout(self.banner)
        self.banner_label = QLabel()
        self.banner_label.setObjectName("awayBannerText")
        banner_layout.addWidget(self.banner_label)
        layout.addWidget(self.banner)
        self.banner.hide()

        # controls
        controls_row = QHBoxLayout()
        self.set_password_btn = QPushButton("SET / CHANGE PASSWORD")
        self.set_password_btn.clicked.connect(self.handle_set_password)
        controls_row.addWidget(self.set_password_btn)

        self.leave_btn = QPushButton("I'M LEAVING")
        self.leave_btn.setObjectName("dangerBtn")
        self.leave_btn.clicked.connect(self.handle_leave)
        controls_row.addWidget(self.leave_btn)

        self.back_btn = QPushButton("I'M BACK (UNLOCK)")
        self.back_btn.setObjectName("safeBtn")
        self.back_btn.clicked.connect(self.handle_back)
        controls_row.addWidget(self.back_btn)

        controls_row.addStretch()
        layout.addLayout(controls_row)

        # alerts table
        alerts_title_row = QHBoxLayout()
        alerts_title = QLabel("ALERTS")
        alerts_title.setObjectName("sectionTitle")
        alerts_title_row.addWidget(alerts_title)
        alerts_title_row.addStretch()
        self.clear_alerts_btn = QPushButton("CLEAR LOG")
        self.clear_alerts_btn.setObjectName("dangerBtn")
        self.clear_alerts_btn.clicked.connect(self.clear_alerts_log)
        alerts_title_row.addWidget(self.clear_alerts_btn)
        layout.addLayout(alerts_title_row)

        self.alerts_table = QTableWidget(0, 3)
        self.alerts_table.setHorizontalHeaderLabels(["Time", "Type", "Detail"])
        self.alerts_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.alerts_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.alerts_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.alerts_table.verticalHeader().setVisible(False)
        self.alerts_table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.alerts_table)

        for a in self.away.get_alerts():
            self.add_alert_row(a)
        self.refresh_banner()

    def handle_set_password(self):
        if self.away.has_password():
            wait = self.away.lockout_remaining()
            if wait:
                QMessageBox.warning(self, "Locked", f"Too many wrong attempts. Try again in {wait} seconds.")
                return
            old, ok = QInputDialog.getText(
                self, "Current password", "Current password:", QLineEdit.Password
            )
            if not ok:
                return
            if not self.away.check_password(old):
                QMessageBox.critical(self, "Wrong password", "That password is not correct.")
                return
        pw1, ok1 = QInputDialog.getText(
            self, "Set password", "New password (at least 6 characters):", QLineEdit.Password
        )
        if not ok1 or not pw1:
            return
        if len(pw1) < 6:
            QMessageBox.warning(self, "Too short", "Use at least 6 characters.")
            return
        pw2, ok2 = QInputDialog.getText(
            self, "Confirm password", "Type it again:", QLineEdit.Password
        )
        if not ok2 or pw1 != pw2:
            QMessageBox.warning(self, "Mismatch", "Passwords did not match. Try again.")
            return
        self.away.set_password(pw1)
        QMessageBox.information(self, "Saved", "Password set successfully.")

    def handle_leave(self):
        if not self.away.has_password():
            QMessageBox.warning(
                self, "No password set",
                "Set a password first so you can unlock away mode later."
            )
            return
        if self.away.armed:
            QMessageBox.information(self, "Already away", "Away mode is already on.")
            return
        confirm = QMessageBox.question(
            self, "Confirm",
            "Arm away mode? You have 15 seconds to step away. After that, "
            "anyone using this device is flagged until you unlock it with "
            "your password (or from the phone app)."
        )
        if confirm == QMessageBox.Yes:
            self.away.arm()
            self.refresh_banner()

    def handle_back(self):
        if not self.away.armed:
            QMessageBox.information(self, "Not away", "Away mode isn't on right now.")
            return
        wait = self.away.lockout_remaining()
        if wait:
            QMessageBox.warning(self, "Locked", f"Too many wrong attempts. Try again in {wait} seconds.")
            return
        pw, ok = QInputDialog.getText(
            self, "Unlock", "Enter your password:", QLineEdit.Password
        )
        if not ok:
            return
        if self.away.disarm(pw):
            self.refresh_banner()
        else:
            QMessageBox.critical(self, "Wrong password", "That password is not correct.")

    def refresh_banner(self):
        if self.away.armed:
            when = self.away.armed_at.strftime("%H:%M:%S") if self.away.armed_at else ""
            self.banner_label.setText(f"⚠ AWAY MODE ON — armed since {when}")
            self.banner.show()
        else:
            self.banner.hide()

    def add_alert_row(self, alert: dict):
        row = self.alerts_table.rowCount()
        self.alerts_table.insertRow(row)
        for col, key in enumerate(("time", "kind", "detail")):
            item = QTableWidgetItem(alert[key])
            item.setForeground(Qt.red)
            self.alerts_table.setItem(row, col, item)
        self.alerts_table.scrollToBottom()

    def clear_alerts_log(self):
        if not guard(self, "Clear alerts"):
            return
        confirm = QMessageBox.question(
            self, "Clear log",
            "This permanently deletes all recorded away-mode alerts. Continue?",
        )
        if confirm != QMessageBox.Yes:
            return
        self.away.clear_alerts()
        self.alerts_table.setRowCount(0)


class SettingsWidget(QWidget):
    """Controls for launching ARGUS like a normal installed app: auto-run
    at Windows startup, and Desktop / Start Menu shortcuts. Everything
    here is a plain, visible Windows shortcut (.lnk) - nothing hidden."""

    tailscale_done = Signal(bool, str, str)   # ok, message, url
    update_checked = Signal(object, str, bool)   # info, error, silent
    update_downloaded = Signal(object, str)      # path, error
    update_progress = Signal(int)

    def __init__(self, away, remote, parent=None):
        super().__init__(parent)
        self.away = away
        self.remote = remote

        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        title = QLabel("SETTINGS")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        subtitle = QLabel("Control how ARGUS launches on this PC.")
        subtitle.setObjectName("pageSubtitle")
        layout.addWidget(subtitle)

        # --- startup card ---
        startup_card = QFrame()
        startup_card.setObjectName("card")
        startup_layout = QVBoxLayout(startup_card)
        startup_layout.setContentsMargins(18, 16, 18, 16)
        startup_layout.setSpacing(10)

        startup_title = QLabel("START AUTOMATICALLY WITH WINDOWS")
        startup_title.setObjectName("sectionTitle")
        startup_layout.addWidget(startup_title)

        startup_desc = QLabel(
            "When enabled, ARGUS launches minimized to the system tray "
            "and starts monitoring automatically every time you log in. "
            "This adds a normal shortcut to your Startup folder - visible "
            "in Task Manager > Startup, and removable any time from here."
        )
        startup_desc.setWordWrap(True)
        startup_desc.setStyleSheet("color: #6f7a76;")
        startup_layout.addWidget(startup_desc)

        row = QHBoxLayout()
        self.startup_status = QLabel()
        row.addWidget(self.startup_status)
        row.addStretch()
        self.startup_toggle_btn = QPushButton()
        self.startup_toggle_btn.clicked.connect(self.toggle_startup)
        row.addWidget(self.startup_toggle_btn)
        startup_layout.addLayout(row)

        layout.addWidget(startup_card)
        layout.addWidget(self._build_phone_card())

        # --- shortcuts card ---
        shortcuts_card = QFrame()
        shortcuts_card.setObjectName("card")
        shortcuts_layout = QVBoxLayout(shortcuts_card)
        shortcuts_layout.setContentsMargins(18, 16, 18, 16)
        shortcuts_layout.setSpacing(10)

        shortcuts_title = QLabel("SHORTCUTS")
        shortcuts_title.setObjectName("sectionTitle")
        shortcuts_layout.addWidget(shortcuts_title)

        shortcuts_desc = QLabel("Add ARGUS to your Desktop or Start Menu so you can open it with a click.")
        shortcuts_desc.setWordWrap(True)
        shortcuts_desc.setStyleSheet("color: #6f7a76;")
        shortcuts_layout.addWidget(shortcuts_desc)

        btn_row = QHBoxLayout()
        desktop_btn = QPushButton("ADD TO DESKTOP")
        desktop_btn.setObjectName("ghostBtn")
        desktop_btn.clicked.connect(self.handle_desktop_shortcut)
        btn_row.addWidget(desktop_btn)

        start_menu_btn = QPushButton("ADD TO START MENU")
        start_menu_btn.setObjectName("ghostBtn")
        start_menu_btn.clicked.connect(self.handle_start_menu_shortcut)
        btn_row.addWidget(start_menu_btn)
        btn_row.addStretch()
        shortcuts_layout.addLayout(btn_row)

        layout.addWidget(shortcuts_card)
        layout.addWidget(self._build_update_card())
        layout.addStretch()

        self.refresh_startup_status()

    # ---------------- updates ----------------
    def _build_update_card(self):
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        t = QLabel("UPDATE ARGUS")
        t.setObjectName("sectionTitle")
        lay.addWidget(t)
        d = QLabel(
            "Got an update file (a .zip)? Choose it here and ARGUS installs it and "
            "restarts itself. Your logs, passwords and phone setup are not touched."
        )
        d.setWordWrap(True)
        d.setStyleSheet("color: #6f7a76;")
        lay.addWidget(d)
        self.update_status = QLabel()
        lay.addWidget(self.update_status)
        row = QHBoxLayout()
        self.check_btn = QPushButton("CHECK FOR UPDATES")
        self.check_btn.clicked.connect(lambda: self.check_updates(False))
        row.addWidget(self.check_btn)
        b = QPushButton("UPDATE FROM FILE...")
        b.setObjectName("ghostBtn")
        b.clicked.connect(self.handle_update)
        row.addWidget(b)
        self.update_reset_btn = QPushButton("RESET TO BUILT-IN VERSION")
        self.update_reset_btn.setObjectName("ghostBtn")
        self.update_reset_btn.clicked.connect(self.handle_update_reset)
        row.addWidget(self.update_reset_btn)
        row.addStretch()
        lay.addLayout(row)
        self.refresh_update_status()
        return card

    def refresh_update_status(self):
        extra = f"  (patch: {updater.current_version()})" if updater.has_updates() else ""
        self.update_status.setText(f"Version {updater.app_version()}{extra}")
        self.update_reset_btn.setEnabled(updater.has_updates())

    # ---- online updates (GitHub releases) ----
    def check_updates(self, silent=False):
        if not silent:
            self.check_btn.setEnabled(False)
            self.update_status.setText("Checking for updates...")
        if not getattr(self, "_upd_wired", False):
            self._upd_wired = True
            self.update_checked.connect(self._on_update_checked)
            self.update_downloaded.connect(self._on_update_downloaded)
            self.update_progress.connect(
                lambda pct: self.update_status.setText(f"Downloading update... {pct}%"))

        def work():
            try:
                self.update_checked.emit(updater.check_latest(), "", silent)
            except updater.UpdateError as e:
                self.update_checked.emit(None, str(e), silent)
            except Exception as e:           # noqa
                self.update_checked.emit(None, f"Update check failed: {e}", silent)
        threading.Thread(target=work, daemon=True).start()

    def _on_update_checked(self, info, err, silent):
        self.check_btn.setEnabled(True)
        self.refresh_update_status()
        if err:
            if not silent:
                self.update_status.setText(err)
            return
        if not info["newer"]:
            if not silent:
                self.update_status.setText(f"You are up to date (version {updater.app_version()}).")
            return
        self._pending_update = info
        self.update_status.setText(f"Update available: version {info['version']}")
        if silent:
            win = self.window()
            if hasattr(win, "tray_icon"):
                win.tray_icon.showMessage("ARGUS update available",
                                          f"Version {info['version']} is ready. Settings > Update ARGUS.",
                                          QSystemTrayIcon.MessageIcon.Information, 6000)
            return
        notes = ("\n\n" + info["notes"][:600]) if info["notes"] else ""
        if QMessageBox.question(
                self, "Update available",
                f"Version {info['version']} is available (you have {updater.app_version()}).{notes}\n\n"
                "Download and install it now? ARGUS will close and restart.") \
                != QMessageBox.StandardButton.Yes:
            return
        if not guard(self, "Update ARGUS"):
            return
        self.check_btn.setEnabled(False)
        self.update_status.setText("Downloading update... 0%")

        def dl():
            last = [-1]

            def prog(f):
                pct = int(f * 100)
                if pct != last[0]:
                    last[0] = pct
                    self.update_progress.emit(pct)
            try:
                self.update_downloaded.emit(updater.download_setup(info, prog), "")
            except updater.UpdateError as e:
                self.update_downloaded.emit(None, str(e))
            except Exception as e:           # noqa
                self.update_downloaded.emit(None, f"Download failed: {e}")
        threading.Thread(target=dl, daemon=True).start()

    def _on_update_downloaded(self, path, err):
        self.check_btn.setEnabled(True)
        self.refresh_update_status()
        if err:
            self.update_status.setText(err)
            QMessageBox.warning(self, "Update failed", err)
            return
        QMessageBox.information(self, "Update", "Downloaded. ARGUS will close now and the installer will finish the update.")
        self.window().install_update(path)

    def handle_update(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose ARGUS update", "", "Update (*.zip)")
        if not path:
            return
        if not guard(self, "Update ARGUS"):
            return
        self.update_status.setText("Installing update... (new libraries can take a minute)")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            ok, msg = updater.apply_zip(path)
        finally:
            QApplication.restoreOverrideCursor()
            self.refresh_update_status()
        if not ok:
            QMessageBox.warning(self, "Update failed", msg)
            return
        QMessageBox.information(self, "Update", msg + "\n\nARGUS will restart now.")
        self.window().restart_app()

    def handle_update_reset(self):
        if not guard(self, "Reset ARGUS"):
            return
        if QMessageBox.question(self, "Reset", "Go back to the version built into ARGUS.exe and restart?") \
                != QMessageBox.StandardButton.Yes:
            return
        updater.reset_to_builtin()
        self.window().restart_app()

    # ---------------- phone app ----------------
    def _build_phone_card(self):
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        t = QLabel("PHONE APP (ALERTS + REMOTE CONTROL)")
        t.setObjectName("sectionTitle")
        lay.addWidget(t)

        d = QLabel(
            "Get a push notification on your phone when away mode catches "
            "someone, then lock this PC, sound an alarm or take a screenshot "
            "from the app. The server only listens on this PC; Tailscale "
            "publishes it privately (HTTPS) to your own devices."
        )
        d.setWordWrap(True)
        d.setStyleSheet("color: #6f7a76;")
        lay.addWidget(d)

        row = QHBoxLayout()
        self.phone_status = QLabel()
        row.addWidget(self.phone_status)
        row.addStretch()
        self.phone_pw_btn = QPushButton()
        self.phone_pw_btn.setObjectName("ghostBtn")
        self.phone_pw_btn.clicked.connect(self.handle_phone_password)
        row.addWidget(self.phone_pw_btn)
        self.phone_toggle_btn = QPushButton()
        self.phone_toggle_btn.clicked.connect(self.toggle_phone_server)
        row.addWidget(self.phone_toggle_btn)
        lay.addLayout(row)

        self.webcam_check = QCheckBox(
            "Allow webcam snapshot from the phone (needs: pip install opencv-python)"
        )
        self.webcam_check.setChecked(bool(self.remote.cfg.get("webcam")))
        self.webcam_check.toggled.connect(self.toggle_webcam)
        lay.addWidget(self.webcam_check)

        row2 = QHBoxLayout()
        self.ts_btn = QPushButton("SET UP PRIVATE LINK (TAILSCALE)")
        self.ts_btn.setObjectName("ghostBtn")
        self.ts_btn.clicked.connect(self.handle_tailscale)
        row2.addWidget(self.ts_btn)
        self.test_btn = QPushButton("SEND TEST NOTIFICATION")
        self.test_btn.setObjectName("ghostBtn")
        self.test_btn.clicked.connect(self.handle_test_push)
        row2.addWidget(self.test_btn)
        row2.addStretch()
        lay.addLayout(row2)

        self.phone_info = QLabel()
        self.phone_info.setWordWrap(True)
        self.phone_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.phone_info.setStyleSheet("color: #9a9bab;")
        lay.addWidget(self.phone_info)

        self.qr_label = QLabel()
        self.qr_label.hide()
        lay.addWidget(self.qr_label)

        self.tailscale_done.connect(self._on_tailscale_done)
        self.refresh_phone_status()
        return card

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "phone_status"):
            self.refresh_phone_status()

    def refresh_phone_status(self):
        running = self.remote.running
        if running:
            self.phone_status.setText(f"●  PHONE SERVER ON  (127.0.0.1:{self.remote.port})")
            self.phone_status.setObjectName("ledOn")
            self.phone_toggle_btn.setText("TURN OFF")
            self.phone_toggle_btn.setObjectName("dangerBtn")
        else:
            self.phone_status.setText("●  PHONE SERVER OFF")
            self.phone_status.setObjectName("ledOff")
            self.phone_toggle_btn.setText("TURN ON")
            self.phone_toggle_btn.setObjectName("")
        self.phone_pw_btn.setText(
            "CHANGE PHONE PASSWORD" if self.remote.has_password() else "SET PHONE PASSWORD"
        )
        for w in (self.phone_status, self.phone_toggle_btn):
            w.style().unpolish(w)
            w.style().polish(w)

        lines = []
        url = self.remote.cfg.get("url")
        if url:
            lines.append(f"Open on your phone (Tailscale installed and signed in): {url}")
        n = len(self.remote.cfg.get("subscriptions") or [])
        lines.append(f"Phones with notifications enabled: {n}")
        if self.remote.last_push_error:
            lines.append(f"Last push error: {self.remote.last_push_error}")
        self.phone_info.setText("\n".join(lines))
        if url and self.qr_label.pixmap() is None:
            self._show_qr(url)

    def _show_qr(self, url):
        try:
            import qrcode
            img = qrcode.make(url).get_image().convert("RGB")
            qimg = QImage(img.tobytes("raw", "RGB"), img.width, img.height,
                          img.width * 3, QImage.Format_RGB888).copy()
            self.qr_label.setPixmap(QPixmap.fromImage(qimg).scaled(
                190, 190, Qt.KeepAspectRatio, Qt.FastTransformation))
            self.qr_label.show()
        except Exception:
            self.qr_label.hide()  # QR is a convenience; the link is still shown

    def handle_phone_password(self):
        if not guard(self, "Change phone password"):
            return
        pw1, ok = QInputDialog.getText(
            self, "Phone password", "New phone password (at least 8 characters):",
            QLineEdit.Password)
        if not ok:
            return
        if len(pw1) < 8:
            QMessageBox.warning(self, "Too short", "Use at least 8 characters.")
            return
        pw2, ok = QInputDialog.getText(self, "Phone password", "Type it again:", QLineEdit.Password)
        if not ok or pw1 != pw2:
            QMessageBox.warning(self, "Mismatch", "Passwords did not match.")
            return
        self.remote.set_password(pw1)
        QMessageBox.information(self, "Saved", "Phone password saved. Any phone that was signed in has been signed out.")
        self.refresh_phone_status()

    def toggle_phone_server(self):
        if not guard(self, "Change phone server"):
            return
        if self.remote.running:
            self.remote.stop()
            self.remote.cfg.set("enabled", False)
        else:
            if not self.remote.has_password():
                QMessageBox.warning(self, "No phone password", "Set a phone password first.")
                return
            try:
                self.remote.start()
                self.remote.cfg.set("enabled", True)
            except OSError as e:
                QMessageBox.critical(
                    self, "Could not start",
                    f"The phone server could not start on port {self.remote.port}: {e}")
        self.refresh_phone_status()

    def toggle_webcam(self, checked):
        if not guard(self, "Change webcam setting"):
            self.webcam_check.blockSignals(True)
            self.webcam_check.setChecked(not checked)
            self.webcam_check.blockSignals(False)
            return
        self.remote.cfg.set("webcam", bool(checked))

    def handle_tailscale(self):
        if not self.remote.running:
            QMessageBox.information(self, "Turn the phone server on first",
                                    "Set a phone password and turn the phone server on, then run this.")
            return
        self.ts_btn.setEnabled(False)
        self.phone_info.setText("Setting up Tailscale... if a Tailscale window or browser tab opens, approve HTTPS there.")
        port = self.remote.port

        def work():
            ok, msg = tailscale_helper.enable_serve(port)
            url = tailscale_helper.phone_url() if ok else ""
            self.tailscale_done.emit(ok, msg, url or "")

        threading.Thread(target=work, daemon=True).start()

    def _on_tailscale_done(self, ok, msg, url):
        self.ts_btn.setEnabled(True)
        if ok and url:
            self.remote.cfg.set("url", url)
            self.qr_label.clear()
            self.refresh_phone_status()
        else:
            self.refresh_phone_status()
            QMessageBox.warning(self, "Tailscale", msg or "Tailscale did not finish. See the README, step 'Phone app'.")

    def handle_test_push(self):
        n = self.remote.send_test()
        if n == 0:
            QMessageBox.information(
                self, "No phone yet",
                "No phone has notifications enabled. Open the link on your phone, "
                "log in, and tap 'Enable notifications'.")
        else:
            QMessageBox.information(self, "Sent", f"Test notification sent to {n} phone(s).")
            QTimer.singleShot(2500, self.refresh_phone_status)

    def refresh_startup_status(self):
        enabled = startup.is_startup_enabled()
        if enabled:
            self.startup_status.setText("●  ENABLED")
            self.startup_status.setObjectName("ledOn")
            self.startup_toggle_btn.setText("DISABLE")
            self.startup_toggle_btn.setObjectName("dangerBtn")
        else:
            self.startup_status.setText("●  DISABLED")
            self.startup_status.setObjectName("ledOff")
            self.startup_toggle_btn.setText("ENABLE")
            self.startup_toggle_btn.setObjectName("")
        for w in (self.startup_status, self.startup_toggle_btn):
            w.style().unpolish(w)
            w.style().polish(w)

    def toggle_startup(self):
        if startup.is_startup_enabled() and not guard(self, "Disable auto-start"):
            return
        try:
            if startup.is_startup_enabled():
                startup.disable_startup()
            else:
                startup.enable_startup()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not update startup settings: {e}")
            return
        self.refresh_startup_status()

    def handle_desktop_shortcut(self):
        try:
            startup.create_desktop_shortcut()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not create the shortcut: {e}")
            return
        QMessageBox.information(self, "Done", "Desktop shortcut created.")

    def handle_start_menu_shortcut(self):
        try:
            startup.create_start_menu_shortcut()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not create the shortcut: {e}")
            return
        QMessageBox.information(self, "Done", "Start Menu shortcut created.")


# ----------------------------------------------------------------------
# Main window
# ----------------------------------------------------------------------
NAV_ITEMS = [
    "Dashboard",
    "Window Activity",
    "Activity Level",
    "Browser Activity",
    "Away Mode & Alerts",
    "Settings",
]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ARGUS")
        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.resize(1040, 680)

        self.bus = EventBus()
        self.away = AwayMode()
        self.controller = MonitorController(self.bus, self.away)
        global _AWAY
        _AWAY = self.away
        self.remote = RemoteService(
            self.away, status_fn=lambda: {"monitoring": self.controller.running})
        # every alert goes to the desktop table AND the phone
        self.away.add_listener(lambda e: self.bus.alert_added.emit(e))
        self.away.add_listener(self.remote.notify_alert)

        central = QWidget()
        self.setCentralWidget(central)
        outer_layout = QVBoxLayout(central)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # --- top status bar ---
        topbar = QFrame()
        topbar.setObjectName("topbar")
        topbar.setFixedHeight(44)
        topbar_layout = QHBoxLayout(topbar)
        topbar_layout.setContentsMargins(20, 0, 20, 0)

        topbar_brand = QLabel("◉ ARGUS")
        topbar_brand.setObjectName("topbarBrand")
        topbar_layout.addWidget(topbar_brand)
        topbar_layout.addSpacing(24)

        self.topbar_rec_led = QLabel("●  MONITORING: OFF")
        self.topbar_rec_led.setObjectName("ledOff")
        topbar_layout.addWidget(self.topbar_rec_led)
        topbar_layout.addSpacing(20)

        self.topbar_away_led = QLabel("●  AWAY MODE: OFF")
        self.topbar_away_led.setObjectName("ledOff")
        topbar_layout.addWidget(self.topbar_away_led)

        topbar_layout.addStretch()

        self.topbar_clock = QLabel("--:--:--")
        self.topbar_clock.setObjectName("topbarClock")
        topbar_layout.addWidget(self.topbar_clock)

        outer_layout.addWidget(topbar)

        # --- sidebar + pages row ---
        root_row = QWidget()
        root_layout = QHBoxLayout(root_row)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        outer_layout.addWidget(root_row)

        # --- sidebar ---
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(228)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 18, 0, 20)
        sidebar_layout.setSpacing(0)

        brand_sub = QLabel("NOTHING GOES UNSEEN")
        brand_sub.setObjectName("brandSub")
        sidebar_layout.addWidget(brand_sub)

        self.nav_list = QListWidget()
        self.nav_list.setObjectName("navList")
        for name in NAV_ITEMS:
            QListWidgetItem(name.upper(), self.nav_list)
        self.nav_list.setCurrentRow(0)
        self.nav_list.currentRowChanged.connect(self.change_page)
        sidebar_layout.addWidget(self.nav_list)
        sidebar_layout.addStretch()

        divider = QFrame()
        divider.setObjectName("divider")
        div_wrap = QVBoxLayout()
        div_wrap.setContentsMargins(16, 0, 16, 14)
        div_wrap.addWidget(divider)
        sidebar_layout.addLayout(div_wrap)

        status_box = QHBoxLayout()
        status_box.setContentsMargins(20, 0, 20, 12)
        self.status_label = QLabel("●  STOPPED")
        self.status_label.setObjectName("statusPillStopped")
        status_box.addWidget(self.status_label)
        status_box.addStretch()
        sidebar_layout.addLayout(status_box)

        self.toggle_btn = QPushButton("START MONITORING")
        self.toggle_btn.clicked.connect(self.toggle_monitoring)
        toggle_wrap = QVBoxLayout()
        toggle_wrap.setContentsMargins(16, 0, 16, 0)
        toggle_wrap.addWidget(self.toggle_btn)
        sidebar_layout.addLayout(toggle_wrap)

        root_layout.addWidget(sidebar)

        # --- pages ---
        pages_wrap = QWidget()
        pages_layout = QVBoxLayout(pages_wrap)
        pages_layout.setContentsMargins(24, 20, 24, 20)

        self.stack = QStackedWidget()
        self.dashboard = DashboardWidget()
        self.window_log_view = WindowActivityWidget(WINDOW_LOG)
        self.activity_log_view = ActivityLevelWidget(ACTIVITY_LOG)
        self.browser_log_view = LogViewerWidget(
            BROWSER_LOG, title="BROWSER ACTIVITY",
            subtitle="URLs and page titles visited across Chrome, Edge, and Firefox.",
        )
        self.away_widget = AwayModeWidget(self.away)
        self.settings_widget = SettingsWidget(self.away, self.remote)

        for w in (self.dashboard, self.window_log_view, self.activity_log_view,
                  self.browser_log_view, self.away_widget, self.settings_widget):
            self.stack.addWidget(w)

        pages_layout.addWidget(self.stack)
        root_layout.addWidget(pages_wrap)

        # --- wire up real-time events ---
        self.bus.window_switched.connect(self.on_window_switched)
        self.bus.activity_tick.connect(self.on_activity_tick)
        self.bus.browser_visit.connect(self.on_browser_visit)
        self.bus.alert_added.connect(self.away_widget.add_alert_row)
        self.away.restore_state()   # alerts if ARGUS came back up armed unexpectedly

        # keep the away banner + top bar LEDs correct even if state changed
        # elsewhere, and drive the live clock
        self.tick_timer = QTimer(self)
        self.tick_timer.timeout.connect(self.tick)
        self.tick_timer.start(1000)
        self.tick()

    def tick(self):
        self.topbar_clock.setText(datetime.now().strftime("%Y-%m-%d  %H:%M:%S"))
        self.away_widget.refresh_banner()

        # armed means monitoring must be on - heal it if anything stopped it
        if self.away.armed and not self.controller.running:
            self.set_monitoring(True)

        if self.away.armed:
            self.topbar_away_led.setText("●  AWAY MODE: ARMED")
            self.topbar_away_led.setObjectName("ledAlert")
        else:
            self.topbar_away_led.setText("●  AWAY MODE: OFF")
            self.topbar_away_led.setObjectName("ledOff")
        self.topbar_away_led.style().unpolish(self.topbar_away_led)
        self.topbar_away_led.style().polish(self.topbar_away_led)

    def change_page(self, index: int):
        self.stack.setCurrentIndex(index)

    def on_window_switched(self, process_name, title, ts_str):
        self.dashboard.bump_window()
        self.window_log_view.add_live_row(process_name, title, ts_str)

    def on_activity_tick(self, keys, clicks):
        self.dashboard.bump_activity()
        self.activity_log_view.add_live_row(keys, clicks)

    def on_browser_visit(self, browser_name, url, title, ts_str):
        self.dashboard.bump_browser()
        self.browser_log_view.text_view.appendPlainText(
            f"[{ts_str}] BROWSER={browser_name} | URL={url} | TITLE={title}"
        )

    def toggle_monitoring(self):
        self.set_monitoring(not self.controller.running)

    def set_monitoring(self, should_run: bool):
        if should_run == self.controller.running:
            return
        if not should_run and self.away.armed:
            QMessageBox.information(
                self, "Away mode is armed",
                "Unlock away mode first (I'M BACK) before stopping monitoring.")
            return
        if should_run:
            self.controller.start()
            self.status_label.setText("●  RUNNING")
            self.status_label.setObjectName("statusPillRunning")
            self.toggle_btn.setText("STOP MONITORING")
            self.toggle_btn.setObjectName("stopBtn")
            self.topbar_rec_led.setText("●  MONITORING: ON")
            self.topbar_rec_led.setObjectName("ledOn")
        else:
            self.controller.stop()
            self.status_label.setText("●  STOPPED")
            self.status_label.setObjectName("statusPillStopped")
            self.toggle_btn.setText("START MONITORING")
            self.toggle_btn.setObjectName("")
            self.topbar_rec_led.setText("●  MONITORING: OFF")
            self.topbar_rec_led.setObjectName("ledOff")

        for widget in (self.status_label, self.toggle_btn, self.topbar_rec_led):
            widget.style().unpolish(widget)
            widget.style().polish(widget)

        if hasattr(self, "tray_toggle_action"):
            self.tray_toggle_action.setText(
                "Stop monitoring" if self.controller.running else "Start monitoring"
            )

    # ---------------- system tray ----------------
    def setup_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        if ICON_PATH.exists():
            self.tray_icon.setIcon(QIcon(str(ICON_PATH)))
        self.tray_icon.setToolTip("ARGUS — personal activity monitor")

        menu = QMenu()
        show_action = menu.addAction("Show ARGUS")
        show_action.triggered.connect(self.show_from_tray)

        self.tray_toggle_action = menu.addAction("Start monitoring")
        self.tray_toggle_action.triggered.connect(self.toggle_monitoring)

        menu.addSeparator()
        quit_action = menu.addAction("Quit")
        quit_action.triggered.connect(self.quit_app)

        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_from_tray()

    def show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self):
        if not guard(self, "Quit ARGUS"):
            return
        self.away.quit_clean()      # tells the next launch this exit was legitimate
        self._really_quit = True
        self.remote.stop()
        self.controller.stop()
        self.tray_icon.hide()
        QApplication.instance().quit()

    def restart_app(self):
        """Quit cleanly (no tamper alert next launch) and start a new ARGUS.
        Callers have already passed guard()."""
        self.away.quit_clean()
        self._really_quit = True
        self.remote.stop()
        self.controller.stop()
        self.tray_icon.hide()
        updater.relaunch()
        QApplication.instance().quit()

    def install_update(self, setup_path):
        """Quit cleanly and hand over to the downloaded Setup (upgrade mode)."""
        self.away.quit_clean()
        self._really_quit = True
        self.remote.stop()
        self.controller.stop()
        self.tray_icon.hide()
        updater.launch_setup(setup_path)
        QApplication.instance().quit()

    def closeEvent(self, event):
        if getattr(self, "_really_quit", False):
            self.controller.stop()
            event.accept()
            return
        # minimize to the tray instead of exiting, so monitoring keeps
        # running in the background - same as any normal tray app
        event.ignore()
        self.hide()
        if hasattr(self, "tray_icon") and not getattr(self, "_tray_notice_shown", False):
            self._tray_notice_shown = True
            self.tray_icon.showMessage(
                "ARGUS is still running",
                "Monitoring continues in the background. Right-click the "
                "tray icon to open ARGUS again or to quit.",
                QSystemTrayIcon.MessageIcon.Information,
                4000,
            )


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    app.setQuitOnLastWindowClosed(False)
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))

    launch_in_tray = "--tray" in sys.argv

    window = MainWindow()
    window.setup_tray()
    app.aboutToQuit.connect(window.remote.stop)

    if window.remote.cfg.get("enabled") and window.remote.has_password():
        try:
            window.remote.start()
        except OSError as e:
            print(f"[phone] could not start the phone server: {e}")

    # quiet update check shortly after start (tray notification only)
    QTimer.singleShot(25000, lambda: window.settings_widget.check_updates(silent=True))

    def _reveal_app():
        if launch_in_tray:
            # launched automatically at Windows startup: stay out of the way
            # in the tray and start monitoring right away
            window.set_monitoring(True)
        else:
            window.show()

    if launch_in_tray:
        # silent/automatic startup: skip the cinematic intro entirely
        _reveal_app()
    else:
        splash = ArgusSplash()
        splash.finished.connect(_reveal_app)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
