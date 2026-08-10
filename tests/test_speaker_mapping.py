import pytest
from PyQt6.QtWidgets import QApplication

from audiorecorder.ui.speaker_mapping import SpeakerMappingDialog


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_speakers_are_sorted_naturally(app):
    dialog = SpeakerMappingDialog(["Remote 10", "Remote 2", "Remote 1"])
    try:
        assert list(dialog._edits) == ["Remote 1", "Remote 2", "Remote 10"]
    finally:
        dialog.deleteLater()


def test_existing_names_can_be_edited(app):
    dialog = SpeakerMappingDialog(
        ["Remote 1"], {"Remote 1": "Radek Švarz"})
    try:
        assert dialog._edits["Remote 1"].text() == "Radek Švarz"
        dialog._edits["Remote 1"].setText("Petr Klein")
        assert dialog.mappings() == {"Remote 1": "Petr Klein"}
    finally:
        dialog.deleteLater()


def test_blank_names_are_not_persisted(app):
    dialog = SpeakerMappingDialog(["Remote 1"])
    try:
        assert dialog.mappings() == {}
    finally:
        dialog.deleteLater()
