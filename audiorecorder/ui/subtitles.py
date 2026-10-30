"""The live panel: subtitles or speaker lines while a call is recorded.

Newest first, because during a call the eye belongs at the top: the sentence being spoken
sits above everything, and each settled line pushes the older ones down. Nobody has to
chase a scrollbar to follow a conversation.

Settled lines are inserted as their own text blocks and never rewritten. The two languages
of one line share a block and are separated by a line break, which is the only way to keep
them together: QTextEdit merges adjacent block elements when HTML is inserted, so styling
alone cannot decide where a line ends. The lines are kept as data too, so a theme change
can render them again in the new colours.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from audiorecorder.ui import icons, theme
from audiorecorder.ui.widgets import Chip, IconLabel, ProgressDots, spaced


class SubtitleView(QFrame):
    speaker_names_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("livePanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("liveHeader")
        header.setFixedHeight(44)
        heading_row = QHBoxLayout(header)
        heading_row.setContentsMargins(14, 0, 8, 0)
        heading_row.setSpacing(8)
        self._icon = IconLabel("captions", "live", 16)
        heading_row.addWidget(self._icon)
        self._heading = QLabel("Live subtitles")
        self._heading.setObjectName("liveTitle")
        heading_row.addWidget(self._heading)
        self._detail = Chip("neutral", None, "")
        self._detail.setVisible(False)
        heading_row.addWidget(self._detail)
        heading_row.addStretch()
        self._btn_speaker_names = QPushButton("Name speakers")
        self._btn_speaker_names.setProperty("size", "sm")
        self._btn_speaker_names.setToolTip(
            "Assign participant names to the Remote 1, Remote 2, etc. labels.")
        self._btn_speaker_names.clicked.connect(self.speaker_names_requested.emit)
        self._btn_speaker_names.setVisible(False)
        self._btn_speaker_names.setEnabled(False)
        heading_row.addWidget(self._btn_speaker_names)
        layout.addWidget(header)

        body = QVBoxLayout()
        body.setContentsMargins(14, 10, 14, 0)
        body.setSpacing(4)

        # Above the settled lines, because the newest text is at the top.
        self._in_progress = QFrame()
        self._in_progress.setObjectName("inProgress")
        progress = QHBoxLayout(self._in_progress)
        progress.setContentsMargins(12, 10, 12, 10)
        progress.setSpacing(12)
        dots = QWidget()
        dots.setFixedWidth(52)
        dots_layout = QVBoxLayout(dots)
        dots_layout.setContentsMargins(0, 0, 0, 0)
        dots_layout.addWidget(ProgressDots(), 0, Qt.AlignmentFlag.AlignTop)
        progress.addWidget(dots)
        text_column = QVBoxLayout()
        text_column.setSpacing(2)
        self._pending_who = QLabel("")
        self._pending_who.setObjectName("pendingWho")
        self._pending_who.setVisible(False)
        self._pending = QLabel("")
        self._pending.setObjectName("pending")
        self._pending.setWordWrap(True)
        self._pending.setTextFormat(Qt.TextFormat.PlainText)
        self._pending.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        text_column.addWidget(self._pending_who)
        text_column.addWidget(self._pending)
        progress.addLayout(text_column, 1)
        self._in_progress.setVisible(False)
        body.addWidget(self._in_progress)

        self._settled = QTextEdit()
        self._settled.setObjectName("liveText")
        self._settled.setReadOnly(True)
        self._settled.setMinimumHeight(90)
        self._settled.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._settled.document().setDocumentMargin(0)
        body.addWidget(self._settled)
        layout.addLayout(body)

        self._entries = []
        # The newest line is at the top; stay there unless the reader scrolled away.
        self._follow = True
        bar = self._settled.verticalScrollBar()
        bar.rangeChanged.connect(self._keep_top)
        bar.actionTriggered.connect(lambda _action: self._note_reader(bar))
        bar.sliderReleased.connect(lambda: self._note_reader(bar))
        theme.notifier.changed.connect(self._render_all)

    def _note_reader(self, bar):
        self._follow = bar.sliderPosition() == 0

    def _keep_top(self, *_range):
        if self._follow:
            self._settled.verticalScrollBar().setValue(0)

    def clear(self):
        self._entries = []
        self._follow = True
        self._settled.clear()
        self.set_pending("", "")
        self.set_mode("Live subtitles")

    def has_lines(self):
        return bool(self._entries)

    def set_mode(self, title, detail=None, icon="captions"):
        """The panel's heading: what is shown, and a short detail such as the languages."""
        self._heading.setText(title)
        self._icon.set_icon(icon)
        self._detail.set("neutral", None, detail or "")
        self._detail.setVisible(bool(detail))

    def set_status(self, text):
        """A heading that explains a problem, such as a missing API key."""
        self.set_mode(text)

    def set_speaker_mapping_visible(self, visible):
        self._btn_speaker_names.setVisible(visible)

    def set_speaker_mapping_available(self, count):
        self._btn_speaker_names.setEnabled(count > 0)
        self._btn_speaker_names.setText(
            spaced(f"Name speakers ({count})" if count else "Name speakers"))
        self._btn_speaker_names.setIcon(icons.icon("user-pen", theme.solid(
            "text.primary" if count else "text.disabled"), 14))

    def append_line(self, timestamp, original, translation):
        """A settled line, inserted at the top so the newest is always in view."""
        if not original and not translation:
            return
        self._add(("subtitle", timestamp, original, translation))

    def append_speaker_line(self, timestamp, speaker, text, color_key="speaker.me"):
        """A settled line with an explicit local or remote speaker label."""
        if not text:
            return
        self._add(("speaker", timestamp, speaker, text, color_key))

    def set_pending(self, original, translation, speaker=None, color_key="speaker.me"):
        """The sentence still being spoken, replaced wholesale on every update."""
        text = translation or original
        self._pending.setText(text)
        self._pending_who.setVisible(bool(speaker))
        if speaker:
            self._pending_who.setText(speaker)
            self._pending_who.setStyleSheet(f"color: {theme.token(color_key)};")
        self._in_progress.setVisible(bool(text))

    def _add(self, entry):
        self._entries.append(entry)
        self._insert(entry)
        self._keep_top()

    def _insert(self, entry):
        cursor = self._settled.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.insertHtml(self._html(entry))

    def _render_all(self):
        self._settled.clear()
        for entry in self._entries:
            self._insert(entry)
        self._settled.verticalScrollBar().setValue(0)

    @staticmethod
    def _html(entry):
        """A two-column table: the time in its own column, the text against one left edge.

        The text is proportional, so padding with spaces would line up on no font at all.
        """
        token = theme.token
        if entry[0] == "subtitle":
            _, timestamp, original, translation = entry
            rows = []
            if original:
                rows.append(
                    f'<span style="font-size:13px; color:{token("text.secondary")};">'
                    f'{_escape(original)}</span>')
            if translation:
                rows.append(
                    f'<span style="font-size:15px; color:{token("text.primary")};">'
                    f'{_escape(translation)}</span>')
            content = "<br>".join(rows)
        else:
            _, timestamp, speaker, text, color_key = entry
            colour = token(color_key)
            content = (
                f'<span style="font-size:13px; font-weight:600; color:{colour};">'
                f'&#9679;&nbsp;{_escape(speaker)}</span><br>'
                f'<span style="font-size:15px; color:{token("text.primary")};">'
                f'{_escape(text)}</span>'
            )
        return (
            '<table cellspacing="0" cellpadding="0" width="100%" style="margin-bottom:12px;">'
            f'<tr><td width="64" valign="top" style="padding-top:3px; font-family:\'{theme.MONO}\';'
            f' font-size:11px; color:{token("text.tertiary")};">{_escape(timestamp)}</td>'
            f'<td>{content}</td></tr></table>'
        )


def _escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
