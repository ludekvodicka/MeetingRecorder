import pytest
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
