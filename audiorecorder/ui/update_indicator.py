"""The update state in the status bar, and the dialog behind it."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from audiorecorder.shared.desktop.autoupdate.view import UpdateViewModel
from audiorecorder.updates import AppUpdates
from audiorecorder.version import __version__

TONE_COLORS = {
    "muted": "#888",
    "working": "#2a7ab0",
    "attention": "#b07a00",
    "ready": "#2e8b57",
    "failed": "#c0392b",
}


class UpdateIndicator(QToolButton):
    """Flat status-bar button: the version while nothing happens, the update state otherwise."""

    _updates: AppUpdates

    def __init__(self, updates: AppUpdates, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._updates = updates
        self.setAutoRaise(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self._open)
        updates.changed.connect(self._render)
        self._render(updates.view())

    def _render(self, view: UpdateViewModel) -> None:
        # Off, idle and up to date are not news: the plain version stays where it always was.
        quiet = view.tone == "muted"
        self.setText(f"v{__version__}" if quiet else view.text)
        self.setToolTip("\n".join(part for part in (view.text, view.detail) if part))
        self.setStyleSheet(f"color: {TONE_COLORS[view.tone]}; padding: 0 6px;")

    def _open(self) -> None:
        UpdateDialog(self._updates, self.window()).exec()


class UpdateDialog(QDialog):
    """State, release notes as plain text, and the actions the state allows."""

    _updates: AppUpdates

    def __init__(self, updates: AppUpdates, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._updates = updates
        self.setWindowTitle("Updates")
        self.setMinimumWidth(460)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        self._text = QLabel()
        self._text.setStyleSheet("font-size: 15px; font-weight: bold;")
        self._detail = QLabel()
        self._detail.setWordWrap(True)
        self._release = QLabel()
        self._release.setStyleSheet("color: #888;")
        # Plain text only: release notes come from the network and are never rendered as HTML.
        self._notes = QPlainTextEdit()
        self._notes.setReadOnly(True)
        self._refusal = QLabel()
        self._refusal.setWordWrap(True)
        self._refusal.setStyleSheet(f"color: {TONE_COLORS['failed']};")
        self._refusal.setVisible(False)

        self._check = QPushButton("Check now")
        self._check.clicked.connect(self._on_check)
        self._install = QPushButton("Restart and install")
        self._install.clicked.connect(self._on_install)
        self._open_release = QPushButton("View on GitHub")
        self._open_release.clicked.connect(updates.open_release_page)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addWidget(self._check)
        buttons.addWidget(self._install)
        buttons.addWidget(self._open_release)
        buttons.addStretch()
        buttons.addWidget(close)

        layout = QVBoxLayout(self)
        for widget in (self._text, self._detail, self._release, self._notes, self._refusal):
            layout.addWidget(widget)
        layout.addLayout(buttons)

        updates.changed.connect(self._render)
        self._render(updates.view())

    def _render(self, view: UpdateViewModel) -> None:
        self._text.setText(view.text)
        self._detail.setText(view.detail or "")
        self._detail.setVisible(view.detail is not None)
        release = view.release
        heading = " - ".join(part for part in (
            release.name if release else None,
            release.date[:10] if release and release.date else None) if part)
        self._release.setText(heading)
        self._release.setVisible(bool(heading))
        notes = release.notes if release else None
        self._notes.setPlainText(notes or "")
        self._notes.setVisible(notes is not None)
        self._check.setVisible("check" in view.actions)
        self._install.setVisible("install" in view.actions)
        self._open_release.setVisible("open_release" in view.actions)
        self.adjustSize()

    def _on_check(self) -> None:
        self._refusal.setVisible(False)
        self._updates.check()

    def _on_install(self) -> None:
        reason = self._updates.install()
        self._refusal.setText(f"Not now: {reason}" if reason else "")
        self._refusal.setVisible(reason is not None)
