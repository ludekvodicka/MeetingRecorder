"""The update state in the status bar, and the dialog behind it."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
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
from audiorecorder.ui import icons, theme
from audiorecorder.updates import AppUpdates
from audiorecorder.version import __version__

# Token, soft background and icon for each update tone.
TONES = {
    "muted": ("text.tertiary", None, None),
    "working": ("accent", "accent.soft", "loader-circle"),
    "attention": ("warning", "warning.soft", "download"),
    "ready": ("success", "success.soft", "refresh-cw"),
    "failed": ("error", "error.soft", "circle-alert"),
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
        theme.notifier.changed.connect(self._render)
        self._render(updates.view())

    def _render(self, view: UpdateViewModel | None = None) -> None:
        view = view or self._updates.view()
        # Off, idle and up to date are not news: the plain version stays where it always was.
        quiet = view.tone == "muted"
        tint, background, glyph = TONES[view.tone]
        self.setText(f"v{__version__}" if quiet else view.text)
        self.setToolTip("\n".join(part for part in (view.text, view.detail) if part))
        self.setIcon(icons.icon(glyph, theme.solid(tint), 12) if glyph else QIcon())
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        family = f'font-family: "{theme.MONO}"; font-size: 11px;' if quiet else "font-size: 12px;"
        fill = f"background-color: {theme.token(background)};" if background else ""
        self.setStyleSheet(
            f"QToolButton {{ color: {theme.token(tint)}; {fill} {family} font-weight: 500;"
            " border: none; border-radius: 10px; padding: 0px 8px; min-height: 20px;"
            " max-height: 20px; }")

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
        self._release.setObjectName("hint")
        # Plain text only: release notes come from the network and are never rendered as HTML.
        self._notes = QPlainTextEdit()
        self._notes.setReadOnly(True)
        self._refusal = QLabel()
        self._refusal.setWordWrap(True)
        self._refusal.setStyleSheet(f"color: {theme.token('error')};")
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
