"""The dictation hotkey must not also press whatever button has keyboard focus.

Ctrl+Space is a global hotkey, so it also arrives as an ordinary Qt key event when this
window is the one being typed into. A focused QPushButton is activated by the spacebar on
key RELEASE, so without this filter, starting a dictation immediately clicked the Dictation
button and switched dictation straight back off.
"""

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QPushButton

from audiorecorder.ui.main_window import DictationHotkeyFilter


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def key_event(kind, key, modifiers=Qt.KeyboardModifier.NoModifier):
    return QKeyEvent(kind, key, modifiers)


class TestDictationHotkeyFilter:
    def test_swallows_ctrl_space_while_listening(self, app):
        f = DictationHotkeyFilter(lambda: True)
        for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            event = key_event(kind, Qt.Key.Key_Space, Qt.KeyboardModifier.ControlModifier)
            assert f.eventFilter(None, event) is True

    def test_lets_ctrl_space_through_when_not_listening(self, app):
        f = DictationHotkeyFilter(lambda: False)
        event = key_event(QEvent.Type.KeyPress, Qt.Key.Key_Space,
                          Qt.KeyboardModifier.ControlModifier)
        assert f.eventFilter(None, event) is False

    def test_plain_space_still_reaches_the_widgets(self, app):
        """Tab to a button and press space: that must keep working."""
        f = DictationHotkeyFilter(lambda: True)
        event = key_event(QEvent.Type.KeyPress, Qt.Key.Key_Space)
        assert f.eventFilter(None, event) is False

    def test_ctrl_shift_space_is_swallowed_too(self, app):
        f = DictationHotkeyFilter(lambda: True)
        event = key_event(
            QEvent.Type.KeyPress, Qt.Key.Key_Space,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        assert f.eventFilter(None, event) is True

    def test_other_ctrl_combinations_are_untouched(self, app):
        f = DictationHotkeyFilter(lambda: True)
        event = key_event(QEvent.Type.KeyPress, Qt.Key.Key_V,
                          Qt.KeyboardModifier.ControlModifier)
        assert f.eventFilter(None, event) is False

    def test_non_key_events_are_untouched(self, app):
        f = DictationHotkeyFilter(lambda: True)
        assert f.eventFilter(None, QEvent(QEvent.Type.MouseButtonPress)) is False


class TestAgainstARealButton:
    def test_a_focused_button_is_clicked_by_ctrl_space_without_the_filter(self, app):
        """The bug itself, so the filter is never removed as unnecessary."""
        button = QPushButton("Dictation")
        clicks = []
        button.clicked.connect(lambda: clicks.append(1))
        button.show()
        button.setFocus()

        app.sendEvent(button, key_event(QEvent.Type.KeyPress, Qt.Key.Key_Space,
                                        Qt.KeyboardModifier.ControlModifier))
        app.sendEvent(button, key_event(QEvent.Type.KeyRelease, Qt.Key.Key_Space,
                                        Qt.KeyboardModifier.ControlModifier))
        button.close()

        assert clicks == [1], "a focused button really is activated by the spacebar"
