from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

from audiorecorder import secrets
from audiorecorder.audio.backend import SYSTEM_SOURCE_NONE, create_backend


class SettingsDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = dict(config)
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

        layout.addWidget(general_group)

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
        self.config["output_dir"] = self._dir_edit.text()

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
