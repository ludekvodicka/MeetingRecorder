"""The subtitle area under the level meters.

Newest first, because during a call the eye belongs at the top: the sentence being spoken
sits above everything, and each settled line pushes the older ones down. Nobody has to
chase a scrollbar to follow a conversation.

Settled lines are inserted as their own text blocks and never rewritten. The two languages
of one line share a block and are separated by a line break, which is the only way to keep
them together: QTextEdit merges adjacent block elements when HTML is inserted, so styling
alone cannot decide where a line ends.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QLabel, QSizePolicy, QTextEdit, QVBoxLayout, QWidget

TIME_COLOUR = "#6f6f6f"
ORIGINAL_COLOUR = "#9a9a9a"
TRANSLATION_COLOUR = "#e8e8e8"


class SubtitleView(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(2)

        self._heading = QLabel("Live subtitles")
        self._heading.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self._heading)

        # Above the settled lines, because the newest text is at the top.
        self._pending = QLabel("")
        self._pending.setWordWrap(True)
        self._pending.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._pending.setStyleSheet("color: #7a7a7a; font-size: 12px; font-style: italic;")
        layout.addWidget(self._pending)

        self._settled = QTextEdit()
        self._settled.setReadOnly(True)
        self._settled.setMinimumHeight(110)
        self._settled.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._settled.setStyleSheet("""
            QTextEdit {
                border: 1px solid #444; border-radius: 4px; background: #2b2b2b;
                color: #e0e0e0; font-size: 13px;
            }
        """)
        layout.addWidget(self._settled)

    def clear(self):
        self._settled.clear()
        self._pending.setText("")
        self._heading.setText("Live subtitles")

    def set_status(self, text):
        self._heading.setText(text)

    def append_line(self, timestamp, original, translation):
        """A settled line, inserted at the top so the newest is always in view.

        A two-column table, because the text is proportional: padding the translation with
        spaces to match the width of a timestamp lines up on no font at all. The table puts
        the time in its own column and both languages against one left edge.
        """
        rows = []
        if original:
            rows.append(f'<span style="color:{ORIGINAL_COLOUR};">{_escape(original)}</span>')
        if translation:
            rows.append(
                f'<span style="color:{TRANSLATION_COLOUR};">{_escape(translation)}</span>')
        if not rows:
            return

        entry = (
            '<table cellspacing="0" cellpadding="0" width="100%">'
            f'<tr><td width="62" valign="top" style="color:{TIME_COLOUR};">'
            f'{_escape(timestamp)}</td>'
            f'<td>{"<br>".join(rows)}</td></tr></table>'
        )

        cursor = self._settled.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.insertHtml(entry)
        self._settled.verticalScrollBar().setValue(0)

    def set_pending(self, original, translation):
        """The sentence still being spoken, replaced wholesale on every update."""
        self._pending.setText(translation or original)


def _escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
