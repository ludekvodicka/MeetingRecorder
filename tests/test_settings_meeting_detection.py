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
    monkeypatch.setattr(settings_module.sys, "platform", "darwin")
    monkeypatch.setattr(settings_module.macos_login, "login_item_registered", lambda: False)
    built = SettingsDialog({
        "output_dir": str(tmp_path),
        "meeting_detection": False,
        "calendar_detection": False,
        "automatic_recording": False,
        "launch_at_login": False,
        "language": "en",
        "translation_target": "en",
    })
    yield built
    built.deleteLater()


def test_the_saved_setting_is_shown(dialog):
    assert not dialog._meeting_detection.isChecked()
    assert not dialog._calendar_detection.isEnabled()
    assert not dialog._automatic_recording.isEnabled()


def test_the_popup_can_be_enabled(dialog):
    dialog._meeting_detection.setChecked(True)
    assert dialog._calendar_detection.isEnabled()
    dialog._calendar_detection.setChecked(True)
    assert dialog._automatic_recording.isEnabled()
    dialog._automatic_recording.setChecked(True)
    dialog._save_and_close()
    assert dialog.get_config()["meeting_detection"] is True
    assert dialog.get_config()["calendar_detection"] is True
    assert dialog.get_config()["automatic_recording"] is True


def test_launch_at_login_is_applied_when_settings_are_saved(dialog, monkeypatch):
    applied = []
    monkeypatch.setattr(
        settings_module.macos_login,
        "set_login_item_enabled",
        lambda enabled: applied.append(enabled),
    )
    dialog._launch_at_login.setChecked(True)
    dialog._save_and_close()
    assert applied == [True]
    assert dialog.get_config()["launch_at_login"] is True
