"""The subtitle area keeps settled lines and the sentence still being spoken apart.

The pending text changes several times a second for the length of a call. Appending it to
the document each time would fight the scrollbar and grow without end, so it lives in its
own label and is replaced wholesale.
"""

import pytest
from PyQt6.QtWidgets import QApplication

from audiorecorder.ui.subtitles import SubtitleView


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def view(app):
    widget = SubtitleView()
    yield widget
    widget.deleteLater()


class TestSettledLines:
    def test_both_languages_are_shown(self, view):
        view.append_line("10:14:02", "Hello everyone.", "Ahoj všichni.")
        text = view._settled.toPlainText()
        assert "Hello everyone." in text
        assert "Ahoj všichni." in text

    def test_lines_accumulate_rather_than_replace(self, view):
        view.append_line("10:00:01", "One.", "Jedna.")
        view.append_line("10:00:02", "Two.", "Dva.")
        text = view._settled.toPlainText()
        assert "One." in text and "Two." in text

    def test_a_pair_with_no_translation_still_shows_what_was_said(self, view):
        view.append_line("10:00:03", "Dobrý den.", "")
        assert "Dobrý den." in view._settled.toPlainText()

    def test_an_empty_pair_adds_nothing(self, view):
        view.append_line("10:00:04", "", "")
        assert view._settled.toPlainText().strip() == ""

    def test_markup_in_speech_is_shown_not_interpreted(self, view):
        view.append_line("10:00:05", "<b>not bold</b>", "")
        assert "<b>not bold</b>" in view._settled.toPlainText()


class TestPendingLine:
    def test_the_pending_line_is_replaced_not_appended(self, view):
        view.set_pending("Hel", "Ah")
        view.set_pending("Hello", "Ahoj")
        assert view._pending.text() == "Ahoj"

    def test_the_pending_line_stays_out_of_the_settled_history(self, view):
        view.set_pending("Hello", "Ahoj")
        assert "Ahoj" not in view._settled.toPlainText()

    def test_without_a_translation_the_original_is_shown(self, view):
        view.set_pending("Hello", "")
        assert view._pending.text() == "Hello"


class TestStatusAndClearing:
    def test_the_heading_can_explain_itself(self, view):
        view.set_status("Live subtitles need a system audio source")
        assert "system audio source" in view._heading.text()

    def test_clearing_empties_everything(self, view):
        view.append_line("10:00:01", "One.", "Jedna.")
        view.set_pending("Two", "Dva")
        view.set_status("something")

        view.clear()

        assert view._settled.toPlainText().strip() == ""
        assert view._pending.text() == ""
        assert view._heading.text() == "Live subtitles"


class TestLinesDoNotMerge:
    """Styling alone could not decide where a line ends.

    QTextEdit merges adjacent block elements when HTML is inserted, so the Czech of one
    line and the English of the next appeared glued together on one row. Each settled line
    is its own text block now, and the two languages of one line share it.
    """

    def test_each_sentence_gets_its_own_row(self, view):
        view.append_line("10:00:01", "One.", "Jedna.")
        view.append_line("10:00:02", "Two.", "Dva.")

        rows = [line for line in view._settled.toPlainText().splitlines() if line.strip()]
        assert sum("One." in r for r in rows) == 1
        assert sum("Jedna." in r for r in rows) == 1
        assert not any("One." in r and "Jedna." in r for r in rows)

    def test_a_translation_is_never_glued_to_the_next_original(self, view):
        view.append_line("10:00:01", "One.", "Jedna.")
        view.append_line("10:00:02", "Two.", "Dva.")

        for line in view._settled.toPlainText().splitlines():
            assert not ("Jedna." in line and "Two." in line)

    def test_the_newest_line_is_at_the_top(self, view):
        """During a call the eye belongs at the top, not chasing a scrollbar."""
        view.append_line("10:00:01", "First.", "První.")
        view.append_line("10:00:02", "Second.", "Druhá.")

        text = view._settled.toPlainText()
        assert text.index("Second.") < text.index("First.")

    def test_the_time_is_shown_with_the_line(self, view):
        view.append_line("10:14:02", "Hello.", "Ahoj.")
        assert "10:14:02" in view._settled.toPlainText()

    def test_speech_already_in_the_target_language_keeps_its_time(self, view):
        """It has no original above it, and the time used to hang off the original."""
        view.append_line("10:32:17", "", "Cau, jak se mate?")

        text = view._settled.toPlainText()
        assert "10:32:17" in text
        assert "Cau, jak se mate?" in text
