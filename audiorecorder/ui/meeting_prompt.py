"""Non-modal prompt offered when a likely call window appears."""

import sys

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from audiorecorder.ui import icons, theme
from audiorecorder.ui.macos_spaces import show_on_all_spaces
from audiorecorder.ui.widgets import CountdownRing, IconLabel, spaced


def record_button(text, glyph="circle"):
    """The red pill that starts or stops a recording from a small window."""
    button = QPushButton(spaced(text, True))
    button.setProperty("variant", "danger")
    button.setProperty("pill", True)
    size = 9 if glyph == "square" else 8
    button.setIcon(icons.icon(glyph, theme.solid("text.onFill"), size))
    button.setIconSize(QSize(size, size))
    return button


class MeetingPrompt(QDialog):
    def __init__(self, candidate, parent=None, auto_start_seconds=None, cost_note=None):
        super().__init__(parent)
        self.candidate = candidate
        self._remaining = auto_start_seconds
        self._countdown_elapsed = False
        self.setWindowTitle("Meeting detected")
        if sys.platform == "darwin":
            # A parentless macOS Tool becomes an NSPanel. Unlike an ordinary dialog,
            # it remains eligible to overlay another application's full-screen Space.
            self.setWindowFlag(Qt.WindowType.Tool, True)
            self.setAttribute(
                Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setModal(False)
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        top = QHBoxLayout()
        top.setSpacing(12)
        if self.is_automatic:
            self._ring = CountdownRing(36)
            self._ring.set_remaining(auto_start_seconds, auto_start_seconds)
            top.addWidget(self._ring, 0, Qt.AlignmentFlag.AlignTop)
        else:
            tile = QFrame()
            tile.setFixedSize(36, 36)
            tile.setStyleSheet(
                f"background-color: {theme.token('accent.soft')}; border-radius: 8px;")
            tile_layout = QHBoxLayout(tile)
            tile_layout.setContentsMargins(0, 0, 0, 0)
            tile_layout.addWidget(IconLabel("video", "accent", 18), 0, Qt.AlignmentFlag.AlignCenter)
            top.addWidget(tile, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(4)
        self._title = QLabel("Meeting detected")
        self._title.setStyleSheet("font-size: 14px; font-weight: 600;")
        text.addWidget(self._title)
        self._message = QLabel()
        self._message.setTextFormat(Qt.TextFormat.PlainText)
        self._message.setWordWrap(True)
        self._message.setStyleSheet(f"color: {theme.token('text.secondary')};")
        text.addWidget(self._message)
        top.addLayout(text, 1)
        layout.addLayout(top)

        if cost_note:
            cost = QFrame()
            cost.setObjectName("cost")
            cost.setStyleSheet(
                f"QFrame#cost {{ background-color: {theme.token('live.soft')};"
                " border-radius: 6px; }"
                f"QLabel {{ color: {theme.token('live')}; font-size: 12px; font-weight: 500;"
                " background: transparent; }")
            cost_layout = QHBoxLayout(cost)
            cost_layout.setContentsMargins(10, 6, 10, 6)
            cost_layout.setSpacing(6)
            cost_layout.addWidget(IconLabel("radio", "live", 14))
            cost_layout.addWidget(QLabel(cost_note), 1)
            layout.addWidget(cost)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch()
        later = QPushButton("Cancel" if self.is_automatic else "Not now")
        start = record_button("Start now" if self.is_automatic else "Start recording")
        later.setAutoDefault(False)
        start.setDefault(True)
        later.clicked.connect(self.reject)
        start.clicked.connect(self.accept)
        buttons.addWidget(later)
        buttons.addWidget(start)
        layout.addLayout(buttons)

        self._countdown = QTimer(self)
        self._countdown.setInterval(1000)
        self._countdown.timeout.connect(self._tick)
        self._update_message()
        if self.is_automatic:
            self._countdown.start()

    @property
    def is_automatic(self):
        return self._remaining is not None

    @property
    def countdown_elapsed(self):
        return self._countdown_elapsed

    def _tick(self):
        if not self.is_automatic:
            return
        self._remaining -= 1
        if self._remaining <= 0:
            self._countdown.stop()
            self._countdown_elapsed = True
            self.accept()
            return
        self._update_message()

    def _update_message(self):
        detected = (f"{self.candidate.app_name} appears to have an active meeting: "
                    f"“{self.candidate.title}”.")
        if not self.is_automatic:
            self._message.setText(f"{detected} Start Meeting Recorder now?")
            return
        unit = "second" if self._remaining == 1 else "seconds"
        self._title.setText(f"Recording starts in {self._remaining} {unit}")
        self._ring.set_remaining(self._remaining)
        self._message.setText(
            f"{detected} Recording starts automatically in {self._remaining} {unit}. "
            "Choose Cancel to stop it.")

    def showEvent(self, event):
        super().showEvent(event)
        show_on_all_spaces(self)

    def done(self, result):
        self._countdown.stop()
        super().done(result)
