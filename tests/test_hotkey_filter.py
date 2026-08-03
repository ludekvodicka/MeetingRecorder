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


class TestTheFilterDoesNotOutliveItsWindow:
    """An application-wide filter installed by a window that is then destroyed.

    Qt goes on calling it, and calling a Python event filter whose owner has been
    garbage-collected aborts the process. It happened during the v0.2.0 release build:
    constructing one window triggered a collection of an earlier one, and Linux and macOS
    died with SIGABRT inside eventFilter. Windows survived only by luck of timing.
    """

    def test_the_filter_belongs_to_the_window(self, app):
        """Parented, so Qt destroys it with the window instead of leaving it registered."""
        from PyQt6.QtWidgets import QWidget

        owner = QWidget()
        assert DictationHotkeyFilter(lambda: True, owner).parent() is owner

    def test_a_closed_window_stops_filtering(self, app, monkeypatch, tmp_path):
        from audiorecorder.ui import main_window as mw
        from audiorecorder.ui.main_window import MainWindow

        monkeypatch.setattr(mw, "save_config", lambda config: None)
        monkeypatch.setattr(MainWindow, "_start_update_check", lambda self: None)
        window = MainWindow({"output_dir": str(tmp_path), "rts_translate": False,
                             "language": "en", "translation_target": "cs"})
        window._dictation_active = True

        button = QPushButton("elsewhere")
        clicks = []
        button.clicked.connect(lambda: clicks.append(1))
        button.show()
        button.setFocus()

        def press_ctrl_space():
            for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                app.notify(button, key_event(kind, Qt.Key.Key_Space,
                                             Qt.KeyboardModifier.ControlModifier))

        press_ctrl_space()
        assert clicks == [], "installed, so the button is not activated"

        window._overlay.close()
        window.close()

        press_ctrl_space()
        assert clicks == [1], "closed, so the filter is gone and the key gets through"
        button.close()
