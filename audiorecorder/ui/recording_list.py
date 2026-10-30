"""The library: one row per recording, with its state, outputs and actions.

Each row offers one primary action for its state (Transcribe, Resume, Open or Retry) and
keeps the rest in a menu. Progress and playback stay inside the row they belong to.
"""

import os

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from audiorecorder.ui import icons, theme
from audiorecorder.ui.widgets import BusyBar, Chip, ElidedLabel, fade, set_prop, spaced

BADGES = (
    (".md", "file-text", "Transcript"),
    (".summary.md", "sparkles", "AI summary"),
    (".live.md", "captions", "Live subtitles"),
    (".speakers.md", "users", "Speaker transcript"),
)
QUEUED = "Queued"
BUSY_TIP = "Busy: you can move or delete this recording when the job ends."


def _phase(message):
    """'Uploading audio...' → 'Uploading audio', for a chip."""
    return message.replace("...", "").replace("…", "").strip()


def format_duration(seconds):
    if seconds <= 0:
        return "0:00"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


class RecordingItemWidget(QFrame):
    play_clicked = pyqtSignal(str)
    stop_clicked = pyqtSignal(str)
    transcribe_clicked = pyqtSignal(str)
    cleanup_clicked = pyqtSignal(str)
    move_clicked = pyqtSignal(str)
    delete_clicked = pyqtSignal(str)
    open_transcript_clicked = pyqtSignal(str)
    details_clicked = pyqtSignal(str)

    def __init__(self, recording):
        super().__init__()
        self.recording = recording
        self.setObjectName("row")
        self.setProperty("playing", False)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._is_playing = False
        self._message = ""
        self._busy = False
        self._kind = None
        self._failure = None
        self._build_ui()
        theme.notifier.changed.connect(self._render)
        self._render()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 8, 10)
        layout.setSpacing(12)

        self._btn_play = QToolButton()
        self._btn_play.setObjectName("playButton")
        self._btn_play.setCheckable(True)
        self._btn_play.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_play.clicked.connect(self._on_play_clicked)
        layout.addWidget(self._btn_play, 0, Qt.AlignmentFlag.AlignTop)

        center = QVBoxLayout()
        center.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(12)
        self._name_label = ElidedLabel(self.recording.name)
        self._name_label.setObjectName("name")
        top.addWidget(self._name_label, 1)
        self._duration_label = QLabel(format_duration(self.recording.duration_seconds))
        self._duration_label.setObjectName("duration")
        top.addWidget(self._duration_label)
        center.addLayout(top)

        meta = QHBoxLayout()
        meta.setSpacing(8)
        self._status_chip = Chip()
        meta.addWidget(self._status_chip)
        self._details = QLabel()
        self._details.setObjectName("details")
        self._details.linkActivated.connect(
            lambda _link: self.details_clicked.emit(self.recording.path))
        meta.addWidget(self._details)
        date = QLabel(self.recording.date.strftime("%Y-%m-%d %H:%M"))
        date.setObjectName("date")
        meta.addWidget(date)
        self._badges = []
        outputs = [badge for badge in BADGES if badge[0] in self.recording.outputs]
        if outputs:
            divider = QLabel("|")
            divider.setObjectName("divider")
            meta.addWidget(divider)
            for suffix, glyph, label in outputs:
                badge = QToolButton()
                badge.setAutoRaise(True)
                badge.setToolTip(f"{label}: click to open")
                badge.setCursor(Qt.CursorShape.PointingHandCursor)
                badge.setStyleSheet(
                    "QToolButton { border: none; padding: 0px; background: transparent; }")
                badge.setFixedSize(16, 16)
                target = os.path.splitext(self.recording.path)[0] + suffix
                badge.clicked.connect(
                    lambda _checked=False, path=target: self.open_transcript_clicked.emit(path))
                meta.addWidget(badge)
                self._badges.append((badge, glyph))
        meta.addStretch()
        center.addLayout(meta)

        self._progress = BusyBar()
        self._progress.setVisible(False)
        center.addWidget(self._progress)
        self._player_slot = QVBoxLayout()
        self._player_slot.setContentsMargins(0, 0, 0, 0)
        center.addLayout(self._player_slot)
        layout.addLayout(center, 1)

        right = QWidget()
        right.setFixedWidth(148)
        actions = QHBoxLayout(right)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(4)
        actions.addStretch()
        self._primary = QPushButton()
        self._primary.setProperty("size", "sm")
        self._primary.setCursor(Qt.CursorShape.PointingHandCursor)
        self._primary.clicked.connect(self._on_primary)
        actions.addWidget(self._primary)
        self._more = QToolButton()
        self._more.setProperty("variant", "quiet")
        self._more.setProperty("size", "sm")
        self._more.setToolTip("More actions")
        self._more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu = QMenu(self._more)
        self._menu.aboutToShow.connect(self._fill_menu)
        self._more.setMenu(self._menu)
        actions.addWidget(self._more)
        layout.addWidget(right, 0, Qt.AlignmentFlag.AlignTop)

    # --- state ---

    def set_processing(self, message, busy, kind=None):
        self._message = message
        self._busy = busy
        if kind is not None:
            self._kind = kind
        self._render()

    def set_failure(self, failure):
        """(kind, error) of the last failed job, or None."""
        self._failure = failure
        self._render()

    def _primary_action(self):
        if self._busy:
            return None
        if self._failure is not None:
            return ("Retry", "rotate-ccw", "secondary")
        if self.recording.is_transcribed:
            return ("Open", "file-text", "secondary")
        if self.recording.transcription_status:
            return ("Resume", "audio-lines", "accentSoft")
        return ("Transcribe", "audio-lines", "accentSoft")

    def _status(self):
        """(chip kind, icon, label, spinning, tooltip) for the current state."""
        if self._failure is not None:
            return ("error", "circle-alert", "Failed", False, "")
        if self._busy and self._message == QUEUED:
            return ("neutral", "clock", "Queued", False, "")
        if self._busy and self._kind == "cleanup":
            return ("accent", "sparkles", "Cleaning up", False, self._message)
        if self._busy:
            return ("accent", "loader-circle", _phase(self._message) or "Transcribing", True, "")
        if self.recording.is_transcribed:
            return ("success", "check", "Transcribed", False, "")
        status = self.recording.transcription_status
        if status.startswith("Saved job"):
            return ("warning", "circle-pause", "Interrupted", False, status)
        if status:
            return ("warning", "triangle-alert", "Needs review", False, status)
        return ("neutral", "circle-dashed", "Not transcribed", False, "")

    def _render(self):
        kind, glyph, label, spinning, tip = self._status()
        self._status_chip.set(kind, glyph, label, spinning)
        self._status_chip.setToolTip(tip)
        self._details.setVisible(self._failure is not None)
        self._details.setText(
            f'<a href="details" style="color:{theme.token("accent")};">See details</a>')
        self._progress.setVisible(self._busy and self._message != QUEUED)

        action = self._primary_action()
        self._primary.setVisible(action is not None)
        if action is not None:
            text, glyph, variant = action
            self._primary.setText(spaced(text))
            set_prop(self._primary, "variant", variant)
            tint = "accent" if variant == "accentSoft" else "text.primary"
            self._primary.setIcon(icons.icon(glyph, theme.solid(tint), 14))
            self._primary.setIconSize(QSize(14, 14))
        self._more.setEnabled(not self._busy)
        self._more.setToolTip(BUSY_TIP if self._busy else "More actions")
        self._more.setIcon(icons.icon(
            "ellipsis", theme.solid("text.disabled" if self._busy else "text.secondary"), 16))

        self._btn_play.setChecked(self._is_playing)
        self._btn_play.setToolTip("Stop playback" if self._is_playing else "Play")
        if self._is_playing:
            self._btn_play.setIcon(icons.icon("square", "#FFFFFF", 10))
            self._btn_play.setIconSize(QSize(10, 10))
        else:
            self._btn_play.setIcon(icons.icon("play", theme.solid("text.secondary"), 12))
            self._btn_play.setIconSize(QSize(12, 12))
        for badge, badge_glyph in self._badges:
            badge.setIcon(icons.icon(badge_glyph, theme.solid("text.tertiary"), 14))
            badge.setIconSize(QSize(14, 14))

    def _fill_menu(self):
        menu = self._menu
        menu.clear()
        tint = theme.solid("text.secondary")
        cleanup = menu.addAction(icons.icon("sparkles", tint, 16), "Clean up with AI…")
        cleanup.setEnabled(self.recording.is_transcribed)
        cleanup.triggered.connect(lambda: self.cleanup_clicked.emit(self.recording.path))
        if not self.recording.is_transcribed:
            cleanup.setToolTip("Transcribe the recording first")
        move = menu.addAction(icons.icon("folder-input", tint, 16), "Move to folder…")
        move.triggered.connect(lambda: self.move_clicked.emit(self.recording.path))
        menu.addSeparator()
        delete = menu.addAction(icons.icon("trash-2", theme.solid("error"), 16), "Delete…")
        delete.triggered.connect(lambda: self.delete_clicked.emit(self.recording.path))

    def _on_primary(self):
        action = self._primary_action()
        if action is None:
            return
        text = action[0]
        if text == "Open":
            self.open_transcript_clicked.emit(self.recording.transcript_path)
        elif text == "Retry" and self._failure and self._failure[0] == "cleanup":
            self.cleanup_clicked.emit(self.recording.path)
        elif text in ("Retry", "Resume", "Transcribe"):
            self.transcribe_clicked.emit(self.recording.path)
        else:
            raise ValueError(f"unknown row action: {text}")

    def _on_play_clicked(self):
        if self._is_playing:
            self.stop_clicked.emit(self.recording.path)
        else:
            self.play_clicked.emit(self.recording.path)

    def set_playing(self, playing):
        self._is_playing = playing
        set_prop(self, "playing", playing)
        self._render()

    def attach_player(self, player):
        self._player_slot.addWidget(player)
        player.show()

    def highlight(self):
        base = QColor(theme.color("accent.soft"))

        def paint(strength):
            if strength is None or self._is_playing:
                self.setStyleSheet("")
                return
            color = QColor(base)
            color.setAlphaF(base.alphaF() * strength * 1.6)
            self.setStyleSheet(
                f"QFrame#row {{ background-color: rgba({color.red()},{color.green()},"
                f"{color.blue()},{color.alpha()}); }}")

        self._fade = fade(self, paint)


class RecordingListWidget(QWidget):
    play_requested = pyqtSignal(str)
    stop_requested = pyqtSignal(str)
    transcribe_requested = pyqtSignal(str)
    cleanup_requested = pyqtSignal(str)
    move_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)
    open_transcript_requested = pyqtSignal(str)
    details_requested = pyqtSignal(str)
    add_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(24, 0, 20, 0)
        header.setSpacing(8)
        title = QLabel("Recordings")
        title.setObjectName("sectionTitle")
        self._count = QLabel("")
        self._count.setObjectName("sectionCount")
        self._queue_note = QLabel("")
        self._queue_note.setObjectName("queueNote")
        header.addWidget(title)
        header.addWidget(self._count)
        header.addStretch()
        header.addWidget(self._queue_note)
        header_holder = QWidget()
        header_holder.setFixedHeight(32)
        header_holder.setLayout(header)
        layout.addWidget(header_holder)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("library")
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._contents = QWidget()
        self._contents.setObjectName("libraryContents")
        self._rows_layout = QVBoxLayout(self._contents)
        self._rows_layout.setContentsMargins(8, 0, 8, 8)
        self._rows_layout.setSpacing(2)
        # In the scroll area too, so a short library panel scrolls it instead of overlapping.
        self._empty = self._build_empty()
        self._rows_layout.addWidget(self._empty, 1)
        self._rows_layout.addStretch()
        self._scroll.setWidget(self._contents)
        layout.addWidget(self._scroll, 1)

        self._item_widgets = []
        self._processing_states = {}
        self._failures = {}
        self._player = None
        self._playing_path = None
        theme.notifier.changed.connect(self._render_empty_icon)

    def _build_empty(self):
        empty = QWidget()
        layout = QVBoxLayout(empty)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.addStretch()
        self._empty_icon = QLabel()
        self._empty_icon.setObjectName("emptyIcon")
        self._empty_icon.setFixedSize(56, 56)
        self._empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._empty_icon, 0, Qt.AlignmentFlag.AlignHCenter)
        title = QLabel("No recordings yet")
        title.setObjectName("emptyTitle")
        layout.addWidget(title, 0, Qt.AlignmentFlag.AlignHCenter)
        text = QLabel(
            "Press Record to capture your next call,\nor add audio files you already have.")
        text.setObjectName("emptyText")
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(text, 0, Qt.AlignmentFlag.AlignHCenter)
        self._add_button = QPushButton(spaced("Add files…"))
        self._add_button.clicked.connect(self.add_requested.emit)
        layout.addWidget(self._add_button, 0, Qt.AlignmentFlag.AlignHCenter)
        formats = QLabel("m4a, mp3, wav, ogg, flac, wma, aac")
        formats.setObjectName("emptyFormats")
        layout.addWidget(formats, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch()
        self._render_empty_icon()
        return empty

    def _render_empty_icon(self):
        self._empty_icon.setPixmap(icons.pixmap("audio-lines", theme.solid("text.secondary"), 24))
        self._add_button.setIcon(icons.icon("file-plus", theme.solid("text.primary"), 16))

    def set_player(self, player):
        """The one player widget, moved into whichever row plays."""
        self._player = player
        player.setParent(self)
        player.hide()

    def _park_player(self):
        if self._player is not None:
            self._player.hide()
            self._player.setParent(self)

    def set_recordings(self, recordings):
        self._park_player()
        for widget in self._item_widgets:
            widget.setParent(None)
            widget.deleteLater()
        self._item_widgets = []
        self._empty.setVisible(not recordings)
        self._count.setText(str(len(recordings)) if recordings else "")

        for rec in recordings:
            item_widget = RecordingItemWidget(rec)
            if rec.path in self._processing_states:
                item_widget.set_processing(*self._processing_states[rec.path])
            if rec.path in self._failures:
                item_widget.set_failure(self._failures[rec.path])
            item_widget.play_clicked.connect(self.play_requested.emit)
            item_widget.stop_clicked.connect(self.stop_requested.emit)
            item_widget.transcribe_clicked.connect(self.transcribe_requested.emit)
            item_widget.cleanup_clicked.connect(self.cleanup_requested.emit)
            item_widget.move_clicked.connect(self.move_requested.emit)
            item_widget.delete_clicked.connect(self.delete_requested.emit)
            item_widget.open_transcript_clicked.connect(self.open_transcript_requested.emit)
            item_widget.details_clicked.connect(self.details_requested.emit)
            self._rows_layout.insertWidget(self._rows_layout.count() - 1, item_widget)
            self._item_widgets.append(item_widget)
            if rec.path == self._playing_path:
                item_widget.set_playing(True)
                if self._player is not None:
                    item_widget.attach_player(self._player)
        self._update_queue_note()

    def set_processing(self, path, message, busy, kind=None):
        if message:
            self._processing_states[path] = (message, busy, kind)
        else:
            self._processing_states.pop(path, None)
        if busy:
            self._failures.pop(path, None)
        for widget in self._item_widgets:
            if widget.recording.path == path:
                if busy:
                    widget.set_failure(None)
                widget.set_processing(message, busy, kind)
        self._update_queue_note()

    def set_failure(self, path, kind, error):
        self._failures[path] = (kind, error)
        self._processing_states.pop(path, None)
        for widget in self._item_widgets:
            if widget.recording.path == path:
                widget.set_processing("", False)
                widget.set_failure((kind, error))
        self._update_queue_note()

    def failure(self, path):
        return self._failures.get(path)

    def _update_queue_note(self):
        states = [state for state in self._processing_states.values() if state[1]]
        queued = sum(1 for state in states if state[0] == QUEUED)
        running = len(states) - queued
        parts = []
        if running:
            parts.append(f"{running} processing" if any(
                state[2] == "cleanup" for state in states if state[0] != QUEUED)
                else f"{running} transcribing")
        if queued:
            parts.append(f"{queued} queued")
        self._queue_note.setText(" · ".join(parts))

    def set_playing_path(self, path):
        self._park_player()
        self._playing_path = path
        for w in self._item_widgets:
            playing = w.recording.path == path
            w.set_playing(playing)
            if playing and self._player is not None:
                w.attach_player(self._player)

    def clear_playing(self):
        self._park_player()
        self._playing_path = None
        for w in self._item_widgets:
            w.set_playing(False)

    def highlight_path(self, path):
        for w in self._item_widgets:
            if w.recording.path == path:
                w.highlight()
                self._scroll.ensureWidgetVisible(w)
                break
