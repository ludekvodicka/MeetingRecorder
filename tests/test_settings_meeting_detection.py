import pytest
from PyQt6.QtWidgets import QApplication

from audiorecorder.ui import settings_dialog as settings_module
from audiorecorder.ui.settings_dialog import SettingsDialog


class FakeBackend:
    def list_system_sources(self):
        return []

    def list_microphones(self):
        return []

    def close(self):
        pass


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def dialog(app, monkeypatch, tmp_path):
    monkeypatch.setattr(settings_module, "create_backend", FakeBackend)
    monkeypatch.setattr(settings_module.secrets, "has_stored_key", lambda: False)
    built = SettingsDialog({
        "output_dir": str(tmp_path),
        "meeting_detection": False,
        "language": "en",
        "translation_target": "en",
    })
    yield built
    built.deleteLater()


def test_the_saved_setting_is_shown(dialog):
    assert not dialog._meeting_detection.isChecked()


def test_the_popup_can_be_enabled(dialog):
    dialog._meeting_detection.setChecked(True)
    dialog._save_and_close()
    assert dialog.get_config()["meeting_detection"] is True
