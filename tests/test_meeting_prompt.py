import pytest
from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

from audiorecorder.meetings.detection import MeetingCandidate
from audiorecorder.ui.meeting_prompt import MeetingPrompt


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def prompt(app):
    dialog = MeetingPrompt(
        MeetingCandidate("1:2", "Zoom", "Project sync - Zoom Meeting"))
    yield dialog
    dialog.deleteLater()


def button(prompt, text):
    return next(child for child in prompt.findChildren(QPushButton)
                if child.text() == text)


def test_the_prompt_names_the_detected_app_and_meeting(prompt):
    text = " ".join(label.text() for label in prompt.findChildren(QLabel))
    assert "Zoom" in text
    assert "Project sync" in text


def test_start_recording_accepts_the_prompt(prompt):
    accepted = []
    prompt.accepted.connect(lambda: accepted.append(True))
    button(prompt, "Start Recording").click()
    assert accepted == [True]


def test_not_now_rejects_the_prompt(prompt):
    rejected = []
    prompt.rejected.connect(lambda: rejected.append(True))
    button(prompt, "Not Now").click()
    assert rejected == [True]


def test_showing_the_prompt_configures_macos_space_visibility(prompt, monkeypatch):
    configured = []
    monkeypatch.setattr(
        "audiorecorder.ui.meeting_prompt.show_on_all_spaces",
        lambda widget: configured.append(widget),
    )

    prompt.showEvent(QShowEvent())

    assert configured == [prompt]


def test_automatic_prompt_counts_down_and_accepts(app):
    prompt = MeetingPrompt(
        MeetingCandidate("1:2", "Zoom", "Project sync"),
        auto_start_seconds=2,
    )
    accepted = []
    prompt.accepted.connect(lambda: accepted.append(True))
    try:
        assert prompt.is_automatic
        assert "2 seconds" in prompt._message.text()
        prompt._tick()
        assert "1 second" in prompt._message.text()
        prompt._tick()
        assert accepted == [True]
        assert prompt.countdown_elapsed
    finally:
        prompt.deleteLater()


def test_cancel_stops_the_automatic_timer(app):
    prompt = MeetingPrompt(
        MeetingCandidate("1:2", "Zoom", "Project sync"),
        auto_start_seconds=10,
    )
    try:
        assert prompt._countdown.isActive()
        button(prompt, "Cancel").click()
        assert not prompt._countdown.isActive()
    finally:
        prompt.deleteLater()
