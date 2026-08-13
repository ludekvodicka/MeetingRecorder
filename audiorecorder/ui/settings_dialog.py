import sys

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

from audiorecorder import macos_login, secrets
from audiorecorder.audio.backend import SYSTEM_SOURCE_NONE, create_backend
from audiorecorder.meetings.browser_companion import (
    companion_available,
    companion_directory,
)


class SettingsDialog(QDialog):
    def __init__(self, config, parent=None, browser_bridge=None, meeting_detector=None):
        super().__init__(parent)
        self.config = dict(config)
        self._browser_bridge = browser_bridge
        self._meeting_detector = meeting_detector
        self.setWindowTitle("Settings")
        self.setMinimumWidth(450)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # --- General ---
        general_group = QGroupBox("General")
        general_layout = QFormLayout(general_group)

        dir_row = QHBoxLayout()
        self._dir_edit = QLineEdit(self.config.get("output_dir", ""))
        dir_row.addWidget(self._dir_edit)
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self._browse_dir)
        dir_row.addWidget(btn_browse)
        general_layout.addRow("Output directory:", dir_row)

        self._meeting_detection = QCheckBox(
            "Show a recording prompt when a meeting is detected")
        self._meeting_detection.setChecked(
            self.config.get("meeting_detection", True))
        general_layout.addRow("", self._meeting_detection)

        self._calendar_detection = QCheckBox(
            "Use macOS Calendar for scheduled meeting prompts")
        self._calendar_detection.setChecked(
            self.config.get("calendar_detection", False))
        self._calendar_detection.setEnabled(self._meeting_detection.isChecked())
        self._calendar_detection.setVisible(sys.platform == "darwin")
        self._meeting_detection.toggled.connect(self._calendar_detection.setEnabled)
        general_layout.addRow("", self._calendar_detection)

        self._automatic_recording = QCheckBox(
            "Auto-start app/browser calls (10-second countdown)")
        self._automatic_recording.setChecked(
            self.config.get("automatic_recording", False))
        self._automatic_recording.setEnabled(self._meeting_detection.isChecked())
        self._meeting_detection.toggled.connect(self._automatic_recording.setEnabled)
        self._automatic_recording.setToolTip(
            "Off by default. A visible countdown always allows cancellation. "
            "Calendar-only signals never start recording automatically.")
        general_layout.addRow("", self._automatic_recording)

        self._launch_at_login = QCheckBox(
            "Launch at login and keep meeting detection available")
        self._launch_at_login.setVisible(sys.platform == "darwin")
        self._launch_at_login.setChecked(self.config.get("launch_at_login", False))
        if sys.platform == "darwin":
            try:
                self._launch_at_login.setChecked(macos_login.login_item_registered())
            except macos_login.LoginItemUnavailable as err:
                self._launch_at_login.setEnabled(False)
                self._launch_at_login.setToolTip(str(err))
        general_layout.addRow("", self._launch_at_login)

        layout.addWidget(general_group)

        # --- Browser companion ---
        browser_group = QGroupBox("Browser Meeting Detection")
        browser_layout = QFormLayout(browser_group)
        self._browser_status = QLabel()
        self._refresh_browser_status()
        browser_layout.addRow("Status:", self._browser_status)
        reveal_companion = QPushButton("Reveal Companion Folder…")
        reveal_companion.setEnabled(companion_available())
        reveal_companion.clicked.connect(self._reveal_browser_companion)
        browser_layout.addRow("", reveal_companion)
        browser_help = QLabel(
            "In Brave, open brave://extensions, enable Developer mode, choose Load unpacked, "
            "and select the revealed folder.")
        browser_help.setWordWrap(True)
        browser_layout.addRow("", browser_help)
        layout.addWidget(browser_group)

        # --- Native detector diagnostics ---
        native_group = QGroupBox("Native Meeting Detection")
        native_layout = QVBoxLayout(native_group)
        self._native_status = QLabel()
        self._native_status.setWordWrap(True)
        native_layout.addWidget(self._native_status)
        refresh_native = QPushButton("Refresh Diagnostics")
        refresh_native.clicked.connect(self._refresh_native_status)
        native_layout.addWidget(refresh_native)
        self._refresh_native_status()
        native_group.setVisible(sys.platform == "darwin")
        layout.addWidget(native_group)

        # --- Audio ---
        audio_group = QGroupBox("Audio Devices")
        audio_layout = QFormLayout(audio_group)

        self._system_combo = QComboBox()
        self._mic_combo = QComboBox()
        self._populate_devices()

        audio_layout.addRow("System audio source:", self._system_combo)
        audio_layout.addRow("Microphone:", self._mic_combo)

        self._system_vol = QSlider(Qt.Orientation.Horizontal)
        self._system_vol.setRange(0, 200)
        self._system_vol.setValue(int(self.config.get("system_volume", 1.0) * 100))
        audio_layout.addRow("System volume:", self._system_vol)

        self._mic_vol = QSlider(Qt.Orientation.Horizontal)
        self._mic_vol.setRange(0, 200)
        self._mic_vol.setValue(int(self.config.get("mic_volume", 1.0) * 100))
        audio_layout.addRow("Mic volume:", self._mic_vol)

        layout.addWidget(audio_group)

        # --- Transcription ---
        trans_group = QGroupBox("Transcription")
        trans_layout = QFormLayout(trans_group)

        self._lang_combo = QComboBox()
        self._lang_combo.addItems(["Czech (cs)", "English (en)", "Auto-detect"])
        lang = self.config.get("language", "en")
        if lang == "cs":
            self._lang_combo.setCurrentIndex(0)
        elif lang == "en":
            self._lang_combo.setCurrentIndex(1)
        else:
            self._lang_combo.setCurrentIndex(2)
        trans_layout.addRow("Language:", self._lang_combo)

        self._translate_check = QCheckBox("Enable translation")
        self._translate_check.setChecked(self.config.get("enable_translation", False))
        trans_layout.addRow("", self._translate_check)

        self._translate_target = QComboBox()
        self._translate_target.addItems(["English (en)", "Czech (cs)"])
        target = self.config.get("translation_target", "en")
        self._translate_target.setCurrentIndex(0 if target == "en" else 1)
        trans_layout.addRow("Translate to:", self._translate_target)

        layout.addWidget(trans_group)

        # --- API Key ---
        # The key is held by the OS keyring, never by config.json, so the field starts empty
        # and an empty field on save means "keep what is stored".
        api_group = QGroupBox("Soniox API")
        api_layout = QFormLayout(api_group)

        self._api_key_edit = QLineEdit()
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        if secrets.has_stored_key():
            self._api_key_edit.setPlaceholderText("Stored in the system keyring")
        else:
            self._api_key_edit.setPlaceholderText(f"Not set, or use the {secrets.ENV_VAR} variable")
        api_layout.addRow("API Key:", self._api_key_edit)

        layout.addWidget(api_group)

        # --- Buttons ---
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_ok = QPushButton("OK")
        btn_ok.clicked.connect(self._save_and_close)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select output directory", self._dir_edit.text())
        if d:
            self._dir_edit.setText(d)

    def _refresh_browser_status(self):
        if self._browser_bridge is None:
            message = "Available after Meeting Recorder starts"
        else:
            status = self._browser_bridge.companion_status()
            if status.connected:
                message = "Connected — Brave/Chrome companion heartbeat received"
            elif status.listening:
                message = "Not connected — install or enable the companion"
            else:
                message = f"Unavailable — {status.error or 'local bridge is not running'}"
        self._browser_status.setText(message)

    def _reveal_browser_companion(self):
        directory = companion_directory()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
        QMessageBox.information(
            self,
            "Install Browser Companion",
            "In Brave:\n\n"
            "1. Open brave://extensions\n"
            "2. Enable Developer mode\n"
            "3. Choose Load unpacked\n"
            f"4. Select this folder:\n{directory}\n\n"
            "Keep Meeting Recorder running. Settings will then show Connected.",
        )

    def _refresh_native_status(self):
        if self._meeting_detector is None:
            message = "Available after Meeting Recorder starts"
        else:
            status = self._meeting_detector.diagnostics()
            if not status.scanned:
                message = "Waiting for the first scan"
            else:
                native = "OK" if status.native_available else "unavailable"
                visible = ", ".join(status.visible_apps) or "none"
                matched = ", ".join(status.matched_apps) or "none"
                message = (
                    f"Native window scan: {native}. Visible supported apps: {visible}. "
                    f"Matched calls: {matched}. Teams media monitor: {status.teams_media}."
                )
                if status.error:
                    message += f" Last error: {status.error}"
        self._native_status.setText(message)

    def _populate_devices(self):
        try:
            backend = create_backend()

            self._system_combo.addItem("Default", None)
            self._system_combo.addItem("None (microphone only)", SYSTEM_SOURCE_NONE)
            for dev in backend.list_system_sources():
                self._system_combo.addItem(dev.name, dev.name)

            self._mic_combo.addItem("Default", None)
            for dev in backend.list_microphones():
                self._mic_combo.addItem(dev.name, dev.name)

            self._select_stored(self._system_combo, self.config.get("system_source_name"))
            self._select_stored(self._mic_combo, self.config.get("mic_source_name"))

            backend.close()
        except Exception as e:
            self._system_combo.addItem(f"Error: {e}", None)
            self._mic_combo.addItem(f"Error: {e}", None)

    @staticmethod
    def _select_stored(combo, stored):
        """Preselect the stored device, keeping Default when it is gone or never set."""
        if stored is None:
            return
        idx = combo.findData(stored)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _save_and_close(self):
        launch_at_login = self._launch_at_login.isChecked()
        if sys.platform == "darwin" and self._launch_at_login.isEnabled():
            try:
                if launch_at_login != macos_login.login_item_registered():
                    macos_login.set_login_item_enabled(launch_at_login)
            except (macos_login.LoginItemUnavailable, RuntimeError) as err:
                QMessageBox.warning(self, "Launch at login", str(err))
                return
        self.config["output_dir"] = self._dir_edit.text()
        self.config["meeting_detection"] = self._meeting_detection.isChecked()
        self.config["calendar_detection"] = self._calendar_detection.isChecked()
        self.config["automatic_recording"] = self._automatic_recording.isChecked()
        self.config["launch_at_login"] = launch_at_login

        lang_map = {0: "cs", 1: "en", 2: "auto"}
        self.config["language"] = lang_map.get(self._lang_combo.currentIndex(), "en")

        self.config["enable_translation"] = self._translate_check.isChecked()

        target_map = {0: "en", 1: "cs"}
        self.config["translation_target"] = target_map.get(
            self._translate_target.currentIndex(), "en")

        self.config["system_source_name"] = self._system_combo.currentData()
        self.config["mic_source_name"] = self._mic_combo.currentData()
        self.config["system_volume"] = self._system_vol.value() / 100.0
        self.config["mic_volume"] = self._mic_vol.value() / 100.0

        entered_key = self._api_key_edit.text().strip()
        if entered_key:
            try:
                secrets.set_api_key(entered_key)
            except secrets.SecretsError as e:
                QMessageBox.warning(self, "API Key", str(e))
                return

        self.accept()

    def get_config(self):
        return self.config
