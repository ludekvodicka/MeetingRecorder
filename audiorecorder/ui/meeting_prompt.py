"""Non-modal prompt offered when a likely call window appears."""

import sys

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from audiorecorder.ui.macos_spaces import show_on_all_spaces


class MeetingPrompt(QDialog):
    def __init__(self, candidate, parent=None, auto_start_seconds=None):
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
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)
        self._message = QLabel(
            f"{candidate.app_name} appears to have an active meeting:\n\n"
            f"{candidate.title}\n\nStart Meeting Recorder now?")
        self._message.setTextFormat(Qt.TextFormat.PlainText)
        self._message.setWordWrap(True)
        layout.addWidget(self._message)

        buttons = QDialogButtonBox()
        start = buttons.addButton(
            "Start Now" if self.is_automatic else "Start Recording",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        later = buttons.addButton(
            "Cancel" if self.is_automatic else "Not Now",
            QDialogButtonBox.ButtonRole.RejectRole,
        )
        start.setDefault(True)
        later.setAutoDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._countdown = QTimer(self)
        self._countdown.setInterval(1000)
        self._countdown.timeout.connect(self._tick)
        if self.is_automatic:
            self._update_message()
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
        unit = "second" if self._remaining == 1 else "seconds"
        self._message.setText(
            f"{self.candidate.app_name} appears to have an active meeting:\n\n"
            f"{self.candidate.title}\n\nRecording starts automatically in "
            f"{self._remaining} {unit}. Choose Cancel to stop it.")

    def showEvent(self, event):
        super().showEvent(event)
        show_on_all_spaces(self)

    def done(self, result):
        self._countdown.stop()
        super().done(result)
