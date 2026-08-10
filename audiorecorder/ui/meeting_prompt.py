"""Non-modal prompt offered when a likely call window appears."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout


class MeetingPrompt(QDialog):
    def __init__(self, candidate, parent=None):
        super().__init__(parent)
        self.candidate = candidate
        self.setWindowTitle("Meeting detected")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setModal(False)
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)
        message = QLabel(
            f"{candidate.app_name} appears to have an active meeting:\n\n"
            f"{candidate.title}\n\nStart Meeting Recorder now?")
        message.setWordWrap(True)
        layout.addWidget(message)

        buttons = QDialogButtonBox()
        start = buttons.addButton(
            "Start Recording", QDialogButtonBox.ButtonRole.AcceptRole)
        later = buttons.addButton("Not Now", QDialogButtonBox.ButtonRole.RejectRole)
        start.setDefault(True)
        later.setAutoDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
