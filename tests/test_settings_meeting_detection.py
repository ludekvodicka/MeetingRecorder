import pytest
from PyQt6.QtWidgets import QApplication

from audiorecorder.meetings.browser_agent import BrowserAgentProfile, BrowserAgentStatus
from audiorecorder.meetings.detection import MeetingDetectionStatus
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
    built = SettingsDialog(
        {
            "output_dir": str(tmp_path),
            "meeting_detection": False,
            "calendar_detection": False,
            "automatic_recording": False,
            "launch_at_login": False,
            "language": "en",
            "translation_target": "en",
        }
    )
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


def test_browser_agent_profiles_and_enabled_tools_are_shown_as_plain_text(dialog):
    class Detector:
        def status(self):
            return BrowserAgentStatus(True, True, (
                BrowserAgentProfile("work", "<b>work</b>", ("Google Meet",), True),
                BrowserAgentProfile("home", "home", (), False, "Agent access off"),
            ))

    dialog._browser_agent = Detector()
    dialog._refresh_browser_status()
    assert "2 profiles, 1 meeting tools" in dialog._browser_status.text()
    assert "<b>work</b>: Google Meet" in dialog._browser_status.text()
    assert "home: no meeting tools enabled (Agent access off)" in dialog._browser_status.text()
    assert dialog._browser_status.textFormat() == settings_module.Qt.TextFormat.PlainText


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


def test_native_diagnostics_explain_the_last_scan(app, monkeypatch, tmp_path):
    monkeypatch.setattr(settings_module, "create_backend", FakeBackend)
    monkeypatch.setattr(settings_module.secrets, "has_stored_key", lambda: False)
    monkeypatch.setattr(settings_module.sys, "platform", "darwin")
    monkeypatch.setattr(settings_module.macos_login, "login_item_registered", lambda: False)

    class Detector:
        def diagnostics(self):
            return MeetingDetectionStatus(
                native_available=True,
                scanned=True,
                visible_apps=("Microsoft Teams",),
                matched_apps=(),
                teams_media="idle",
            )

    built = SettingsDialog({"output_dir": str(tmp_path)}, meeting_detector=Detector())
    assert "Visible supported apps: Microsoft Teams" in built._native_status.text()
    assert "Teams media monitor: idle" in built._native_status.text()
    built.deleteLater()


def test_unrelated_save_does_not_reregister_revoked_login_item(dialog, monkeypatch):
    applied = []
    dialog._login_original = True
    dialog._launch_at_login.setChecked(True)
    monkeypatch.setattr(
        settings_module.macos_login,
        "login_item_status",
        lambda: settings_module.macos_login.LoginStatus("requires approval"),
    )
    monkeypatch.setattr(settings_module.macos_login, "set_login_item_enabled", applied.append)
    dialog._refresh_readiness()
    dialog._save_and_close()
    assert applied == []
    assert dialog.get_config()["launch_at_login"]


def test_missing_saved_device_is_not_silently_replaced(dialog):
    dialog._select_stored(dialog._mic_combo, "Disconnected microphone")
    dialog._save_and_close()
    assert dialog.get_config()["mic_source_name"] == "Disconnected microphone"


def test_independent_countdown_settings_are_saved(dialog):
    dialog._start_delay.setValue(7)
    dialog._stop_delay.setValue(19)
    dialog._save_and_close()
    assert dialog.get_config()["auto_start_delay_seconds"] == 7
    assert dialog.get_config()["auto_stop_delay_seconds"] == 19


def test_macos_only_controls_are_hidden_on_windows(app, monkeypatch, tmp_path):
    monkeypatch.setattr(settings_module, "create_backend", FakeBackend)
    monkeypatch.setattr(settings_module.secrets, "has_stored_key", lambda: False)
    monkeypatch.setattr(settings_module.sys, "platform", "win32")
    built = SettingsDialog({"output_dir": str(tmp_path), "language": "en",
                            "translation_target": "en"})
    try:
        layout = built._automatic_recording.parentWidget().layout()
        for control in (built._automatic_recording, built._notification_style,
                        built._start_delay, built._login_status):
            assert not layout.isRowVisible(control)
        assert not built._meeting_detection.isHidden()
        assert not built._meeting_detection.isChecked()
    finally:
        built.deleteLater()


def test_the_dialog_opens_without_scrolling_when_the_screen_allows(app, monkeypatch, tmp_path):
    monkeypatch.setattr(settings_module, "create_backend", FakeBackend)
    monkeypatch.setattr(settings_module.secrets, "has_stored_key", lambda: False)
    monkeypatch.setattr(settings_module.sys, "platform", "win32")
    built = SettingsDialog({"output_dir": str(tmp_path), "language": "en",
                            "translation_target": "en"})
    scroll = built.findChild(settings_module.QScrollArea)
    available = built.screen().availableGeometry()
    built.show()
    try:
        app.processEvents()
        # The offscreen test screen can be smaller than a real one; scrolling is right there.
        if built.width() < available.width() * settings_module.SCREEN_SHARE - 1:
            assert scroll.horizontalScrollBar().maximum() == 0
        if built.height() < available.height() * settings_module.SCREEN_SHARE - 1:
            assert scroll.verticalScrollBar().maximum() == 0
    finally:
        built.hide()
        built.deleteLater()
