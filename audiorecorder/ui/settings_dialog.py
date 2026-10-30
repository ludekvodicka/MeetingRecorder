import sys

from PyQt6.QtCore import QEvent, Qt, QTimer, QUrl
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
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from audiorecorder import macos_login, secrets
from audiorecorder.audio.backend import SYSTEM_SOURCE_NONE, create_backend
from audiorecorder.meetings.native_calls import PROFILES, qualification_key
from audiorecorder.readiness import CredentialProbe, microphone_permission

# Wide enough for the readiness lines to wrap once at most.
CONTENT_WIDTH = 640
# Share of the screen the dialog may take before it scrolls.
SCREEN_SHARE = 0.9


class SettingsDialog(QDialog):
    def __init__(
        self, config, parent=None, browser_agent=None, meeting_detector=None, lifecycle=None
    ):
        super().__init__(parent)
        self.config = dict(config)
        self._lifecycle = lifecycle
        self._login_original = self.config.get("launch_at_login", False)
        self._credential_status = "Unknown"
        self._credential_probe = CredentialProbe(self)
        self._credential_probe.finished.connect(self._credential_result)
        self._browser_agent = browser_agent
        self._meeting_detector = meeting_detector
        self.setWindowTitle("Settings")
        self.setMinimumWidth(450)
        self._build_ui()
        if self._lifecycle is not None:
            self._credential_probe.start()

    def _page(self, title):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        contents = QWidget()
        scroll.setWidget(contents)
        self._tabs.addTab(scroll, title)
        return scroll, QVBoxLayout(contents)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        self._tabs = QTabWidget()
        outer.addWidget(self._tabs)
        general_page = self._page("General")
        readiness_page = self._page("Readiness")
        layout = general_page[1]

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

        self._meeting_detection = QCheckBox("Show a recording prompt when a meeting is detected")
        self._meeting_detection.setChecked(self.config.get("meeting_detection", False))
        general_layout.addRow("", self._meeting_detection)

        self._calendar_detection = QCheckBox("Use macOS Calendar for scheduled meeting prompts")
        self._calendar_detection.setChecked(self.config.get("calendar_detection", False))
        self._calendar_detection.setEnabled(self._meeting_detection.isChecked())
        self._calendar_detection.setVisible(sys.platform == "darwin")
        self._meeting_detection.toggled.connect(self._calendar_detection.setEnabled)
        general_layout.addRow("", self._calendar_detection)

        self._automatic_recording = QCheckBox("Automatically start qualified native calls")
        self._automatic_recording.setChecked(self.config.get("automatic_recording", False))
        self._automatic_recording.setEnabled(self._meeting_detection.isChecked())
        self._meeting_detection.toggled.connect(self._automatic_recording.setEnabled)
        self._automatic_recording.setToolTip(
            "Off by default. A visible countdown always allows cancellation. "
            "Current window/media signals are advisory until validated. "
            "Browser automation is deferred."
        )
        general_layout.addRow("", self._automatic_recording)

        self._auto_stop = QCheckBox("Automatically stop recordings started by automation")
        self._auto_stop.setChecked(self.config.get("auto_stop_recording", False))
        general_layout.addRow("", self._auto_stop)
        self._start_delay = QSpinBox()
        self._stop_delay = QSpinBox()
        self._end_grace = QSpinBox()
        for control, key, low, high in (
            (self._start_delay, "auto_start_delay_seconds", 5, 120),
            (self._stop_delay, "auto_stop_delay_seconds", 5, 120),
            (self._end_grace, "meeting_end_grace_seconds", 15, 180),
        ):
            control.setRange(low, high)
            control.setSuffix(" seconds")
            control.setValue(self.config.get(key, 45 if key == "meeting_end_grace_seconds" else 5))
        general_layout.addRow("Start countdown:", self._start_delay)
        general_layout.addRow("Stop countdown:", self._stop_delay)
        general_layout.addRow("Absence grace (advanced):", self._end_grace)
        self._notification_style = QComboBox()
        self._notification_style.addItem("Native macOS notifications", "native")
        self._notification_style.addItem("In-app controls", "in-app")
        self._notification_style.setCurrentIndex(
            max(
                0,
                self._notification_style.findData(self.config.get("notification_style", "native")),
            )
        )
        general_layout.addRow("Meeting prompts:", self._notification_style)

        self._launch_at_login = QCheckBox("Launch at login and keep meeting detection available")
        self._launch_at_login.setVisible(sys.platform == "darwin")
        self._launch_at_login.setChecked(self.config.get("launch_at_login", False))
        self._login_status = QLabel()
        self._login_status.setWordWrap(True)
        general_layout.addRow("Login status:", self._login_status)
        login_buttons = QHBoxLayout()
        for title, enabled in (("Register", True), ("Disable", False)):
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, value=enabled: self._set_login(value))
            button.setVisible(sys.platform == "darwin")
            login_buttons.addWidget(button)
        open_login = QPushButton("Open Login Items")
        open_login.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("x-apple.systempreferences:com.apple.LoginItems-Settings.extension")
            )
        )
        open_login.setVisible(sys.platform == "darwin")
        login_buttons.addWidget(open_login)
        general_layout.addRow("", login_buttons)
        general_layout.addRow("", self._launch_at_login)
        # Automatic start, native prompts and login items exist only on macOS.
        for row in (
            self._automatic_recording, self._auto_stop, self._start_delay, self._stop_delay,
            self._end_grace, self._notification_style, self._login_status, login_buttons,
        ):
            general_layout.setRowVisible(row, sys.platform == "darwin")

        layout.addWidget(general_group)
        readiness = QGroupBox("Readiness")
        ready_layout = QVBoxLayout(readiness)
        self._readiness = QLabel()
        self._readiness.setWordWrap(True)
        ready_layout.addWidget(self._readiness)
        row = QHBoxLayout()
        for title, callback, everywhere in (
            ("Refresh", self._refresh_readiness, True),
            ("Request Notifications", self._request_notifications, False),
            ("Test Notification", self._test_notification, False),
            ("Test Audio (3 seconds)", self._test_audio, True),
        ):
            button = QPushButton(title)
            button.clicked.connect(callback)
            button.setVisible(everywhere or sys.platform == "darwin")
            row.addWidget(button)
        ready_layout.addLayout(row)
        privacy = QPushButton("Open Audio Privacy Settings")
        privacy.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone")
            )
        )
        ready_layout.addWidget(privacy)
        system_privacy = QPushButton("Open System Audio Privacy Settings")
        system_privacy.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl(
                    "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
                )
            )
        )
        ready_layout.addWidget(system_privacy)
        mic_request = QPushButton("Request Microphone Access")
        mic_request.clicked.connect(self._request_microphone)
        ready_layout.addWidget(mic_request)
        for button in (privacy, system_privacy, mic_request):
            button.setVisible(sys.platform == "darwin")
        readiness_page[1].addWidget(readiness)
        readiness_page[1].addStretch()
        self._ready_timer = QTimer(self)
        self._ready_timer.setInterval(1000)
        self._ready_timer.timeout.connect(self._refresh_readiness)
        self._ready_timer.start()
        self._refresh_readiness()

        browser_group = QGroupBox("Browser Agent")
        browser_layout = QFormLayout(browser_group)
        self._browser_status = QLabel()
        self._browser_status.setWordWrap(True)
        self._browser_status.setTextFormat(Qt.TextFormat.PlainText)
        self._refresh_browser_status()
        self._ready_timer.timeout.connect(self._refresh_browser_status)
        self._meeting_detection.toggled.connect(self._refresh_browser_status)
        browser_layout.addRow("Status:", self._browser_status)
        browser_help = QLabel(
            "Install Browser Agent (github.com/ludekvodicka/BrowserAgentExtension) and enable "
            "Agent access and its Meetings tools."
        )
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
        self._native_calls = QCheckBox("Inspect native Teams/Zoom call controls (Accessibility)")
        self._native_calls.setChecked(self.config.get("native_call_detection", False))
        native_layout.addWidget(self._native_calls)
        native_help = QLabel(
            "Reads call-control names only, not chat text or participants. Current matchers "
            "need supervised validation. Permission and app-version approval are separate."
        )
        native_help.setWordWrap(True)
        native_layout.addWidget(native_help)
        access = QPushButton("Open Accessibility Settings")
        access.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl(
                    "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
                )
            )
        )
        native_layout.addWidget(access)
        request_access = QPushButton("Request Accessibility Access")
        request_access.clicked.connect(self._request_native_accessibility)
        native_layout.addWidget(request_access)
        self._native_app = QComboBox()
        for bundle, name in PROFILES.items():
            self._native_app.addItem(name, bundle)
        native_layout.addWidget(self._native_app)
        self._native_call_status = QLabel()
        self._native_call_status.setWordWrap(True)
        native_layout.addWidget(self._native_call_status)
        self._test_absence = QCheckBox(
            "Include window-absence auto-stop in supervised test (45-second grace by default)"
        )
        if self._lifecycle:
            self._test_absence.setChecked(self._lifecycle.native.test_absence)
        self._test_absence.setToolTip(
            "Experimental: first verify that minimizing/switching Spaces preserves native "
            "call windows. Stay present; unknown or unreadable controls never count as absence."
        )
        native_layout.addWidget(self._test_absence)
        test_row = QHBoxLayout()
        start_test = QPushButton("Start supervised automation test (30 minutes)")
        start_test.clicked.connect(self._arm_native_test)
        test_row.addWidget(start_test)
        stop_test = QPushButton("End test")
        stop_test.clicked.connect(self._end_native_test)
        test_row.addWidget(stop_test)
        native_layout.addLayout(test_row)
        export = QPushButton("Export detector diagnostics…")
        export.clicked.connect(self._export_native_diagnostics)
        native_layout.addWidget(export)
        self._native_checks = QCheckBox(
            "I verified lobby, join/leave, minimize, full-screen/Spaces,\n"
            "reconnect and mute behavior"
        )
        native_layout.addWidget(self._native_checks)
        approve = QPushButton("Approve this tested app version")
        approve.clicked.connect(self._approve_native_version)
        native_layout.addWidget(approve)
        revoke = QPushButton("Remove approval (apply on Save)")
        revoke.clicked.connect(self._revoke_native_version)
        native_layout.addWidget(revoke)
        self._refresh_native_calls()
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
        self._api_key_edit.setPlaceholderText(
            f"Leave blank to keep the stored key; {secrets.ENV_VAR} overrides it"
        )
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
        outer.addLayout(btn_row)
        self._fit_to_contents((general_page, readiness_page), btn_row)

    def _fit_to_contents(self, pages, buttons):
        """Open large enough to show every tab; scroll only on a screen too small for it."""
        available = self.screen().availableGeometry()
        margins = self.layout().contentsMargins()
        scroll = pages[0][0]
        frame = 2 * scroll.frameWidth() + 2 * self._tabs.style().pixelMetric(
            self._tabs.style().PixelMetric.PM_DefaultFrameWidth)
        chrome_width = margins.left() + margins.right() + frame
        chrome_height = (margins.top() + margins.bottom() + frame + 2 * self.layout().spacing()
                         + self._tabs.tabBar().sizeHint().height() + buttons.sizeHint().height())
        contents = [page.widget() for page, _ in pages]
        widest = max(content.minimumSizeHint().width() for content in contents)
        width = min(max(CONTENT_WIDTH, widest),
                    int(available.width() * SCREEN_SHARE) - chrome_width)
        # Wrapped labels need more height the narrower they are.
        height = max(self._content_height(content, width) for content in contents)
        limit = int(available.height() * SCREEN_SHARE) - chrome_height
        if height > limit:
            height = limit
            width += scroll.verticalScrollBar().sizeHint().width()
        self.resize(width + chrome_width, height + chrome_height)

    @staticmethod
    def _content_height(contents, width):
        height = contents.layout().totalHeightForWidth(width)
        return height if height >= 0 else contents.sizeHint().height()

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select output directory", self._dir_edit.text())
        if d:
            self._dir_edit.setText(d)

    def _refresh_browser_status(self):
        if self._browser_agent is None:
            message = "Available after Meeting Recorder starts"
        elif not self._browser_agent.status().active:
            message = (
                "Meeting detection is off. Turn on \"Show a recording prompt when a meeting is "
                "detected\" above and press OK; the browser profiles then appear here."
                if not self._meeting_detection.isChecked()
                else "Starts when you press OK."
            )
        else:
            status = self._browser_agent.status()
            lines = [status.summary]
            for profile in status.profiles:
                tools = ", ".join(profile.tools) or "no meeting tools enabled"
                suffix = f" ({profile.error})" if profile.error else ""
                lines.append(f"{profile.label}: {tools}{suffix}")
            lines.extend(status.errors)
            message = "\n".join(lines)
        self._browser_status.setText(message)

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
        if idx < 0:
            combo.addItem(f"Unavailable — {stored}", stored)
            idx = combo.count() - 1
        combo.setCurrentIndex(idx)

    def _save_and_close(self):
        launch_at_login = self._launch_at_login.isChecked()
        # Only an explicit change to the preference may register; a refresh or unrelated
        # Save must never reverse external revocation.
        if launch_at_login != self._login_original and not self._set_login(launch_at_login):
            return
        self.config["output_dir"] = self._dir_edit.text()
        self.config["meeting_detection"] = self._meeting_detection.isChecked()
        self.config["calendar_detection"] = self._calendar_detection.isChecked()
        self.config["automatic_recording"] = self._automatic_recording.isChecked()
        self.config["native_call_detection"] = self._native_calls.isChecked()
        self.config["launch_at_login"] = launch_at_login
        self.config["auto_stop_recording"] = self._auto_stop.isChecked()
        self.config["auto_start_delay_seconds"] = self._start_delay.value()
        self.config["auto_stop_delay_seconds"] = self._stop_delay.value()
        self.config["meeting_end_grace_seconds"] = self._end_grace.value()
        self.config["notification_style"] = self._notification_style.currentData()

        lang_map = {0: "cs", 1: "en", 2: "auto"}
        self.config["language"] = lang_map.get(self._lang_combo.currentIndex(), "en")

        self.config["enable_translation"] = self._translate_check.isChecked()

        target_map = {0: "en", 1: "cs"}
        self.config["translation_target"] = target_map.get(
            self._translate_target.currentIndex(), "en"
        )

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

    def _set_login(self, enabled):
        try:
            macos_login.set_login_item_enabled(enabled)
        except RuntimeError as error:
            QMessageBox.warning(self, "Launch at login", str(error))
            return False
        self._launch_at_login.setChecked(enabled)
        self._login_original = enabled
        self.config["launch_at_login"] = enabled
        self._refresh_readiness()
        return True

    def _refresh_readiness(self):
        status = macos_login.login_item_status()
        self._login_status.setText(status.state + (f" — {status.error}" if status.error else ""))
        lines = [
            "Window/media/browser signals are advisory. Native call controls require "
            "Accessibility access and tested app-version approval (or supervised test mode)."
        ]
        if self._lifecycle:
            native = self._lifecycle.notifications.status
            lines.append(
                f"Notifications: {native.authorization}; alerts: {native.alerts}; "
                f"sound: {native.sound}. {native.error}"
            )
            ready, reason = self._lifecycle.window._automatic_capture_ready()
            lines.append(f"Automatic capture ready: {ready}; {reason}")
            lines.append(
                f"Recording: {self._lifecycle.controller.state}; "
                f"countdown: {self._lifecycle.controller.remaining}"
            )
            test = self._lifecycle.window._last_capture_test
            if test:
                matches = all(
                    test["config"].get(key) == self.config.get(key)
                    for key in ("system_source_name", "mic_source_name")
                )
                lines.append(
                    f"Last capture: {test['status']}; time {test['time']:.0f}; "
                    f"resolved system: {test['system']}; mic: {test['mic']}; "
                    f"matches saved devices: {matches}; frames: {test.get('frames', 'unknown')}"
                )
            else:
                lines.append(
                    "System audio: not tested. OS permission does not prove received audio."
                )
        else:
            lines.append("Notifications / capture: not tested in this process.")
        lines.append("Microphone: " + self._microphone_status())
        lines.append(f"Soniox (optional): {self._credential_status}. Recording needs no key.")
        lines.append(
            "Calendar: opt-in; Accessibility is used only for native call controls. "
            "Focus may suppress banners."
        )
        if self._lifecycle:
            native = self._lifecycle.native
            lines.append(
                f"Native call-control access: {native.permission}; "
                f"supervised test: {native.testing}"
            )
        self._readiness.setText("\n".join(lines))
        if hasattr(self, "_native_call_status"):
            self._refresh_native_calls()

    @staticmethod
    def _microphone_status():
        return microphone_permission()

    def _refresh_native_calls(self):
        if not self._lifecycle:
            self._native_call_status.setText("Available in the running recorder")
            return
        info = self._lifecycle.native.diagnostics()
        age = f"{info['age_seconds']:.1f}s" if info["age_seconds"] is not None else "not sampled"
        parts = [
            f"Accessibility: {info['permission']}; enabled: {info['enabled']}; "
            f"probe busy: {info['busy']}; sample age: {age}"
        ]
        if info["testing"]:
            parts.append(
                f"Window-absence stopping: {'armed' if info.get('testing_absence') else 'off'}"
            )
            parts.append(
                "SUPERVISED TEST: automatic start/stop temporarily enabled; "
                "stay present and use Cancel/Keep Recording/Stop if needed. "
                "Ending or expiring the test leaves an active recording running."
            )
        for app in info["apps"]:
            parts.append(
                f"{PROFILES[app['bundle']]} {app['version']}: {app['state']}; "
                f"complete: {app['complete']}; controls: {', '.join(app['controls'])}; "
                f"tracked state: {', '.join(app['tracked_states']) or 'none'}; "
                f"approved: {app['approved']}; reason: {app['reason']}"
                f"; closure watch: {app.get('closure_watch', 'unavailable')}; "
                f"closed call windows: {app.get('closed_window_count', 0)}; "
                f"empty inventory confirmed: {app.get('empty_windows_confirmed', False)}"
            )
        if info["error"]:
            parts.append(info["error"])
        self._native_call_status.setText("\n".join(parts))

    def _request_native_accessibility(self):
        if sys.platform == "darwin":
            import ApplicationServices as AX

            AX.AXIsProcessTrustedWithOptions({AX.kAXTrustedCheckOptionPrompt: True})
            if self._lifecycle:
                self._lifecycle.native.request()

    def _arm_native_test(self):
        if not self._lifecycle:
            return
        if self._lifecycle.native.permission != "granted":
            QMessageBox.information(
                self,
                "Accessibility required",
                "Enable native call inspection, save Settings, then grant "
                "the installed recorder Accessibility access and refresh.",
            )
            return
        ready, reason = self._lifecycle.window._automatic_capture_ready()
        if not ready:
            QMessageBox.information(self, "Test audio first", reason)
            return
        if not self._lifecycle.arm_native_test(
            self._native_app.currentData(), self._test_absence.isChecked()
        ):
            QMessageBox.information(self, "Audio busy", "Finish the current audio operation first.")
        self._refresh_native_calls()

    def _end_native_test(self):
        if self._lifecycle:
            self._lifecycle.end_native_test()
        self._refresh_native_calls()

    def _export_native_diagnostics(self):
        if not self._lifecycle:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export call-control diagnostics", "native-call-diagnostics.json", "JSON (*.json)"
        )
        if path:
            try:
                controller = self._lifecycle.controller
                self._lifecycle.native.export_diagnostics(
                    path,
                    {
                        "state": controller.state,
                        "origin": controller.origin,
                        "remaining": controller.remaining,
                        "events": list(self._lifecycle.events),
                    },
                )
            except OSError as error:
                QMessageBox.warning(self, "Could not export diagnostics", str(error))

    def _approve_native_version(self):
        if not self._lifecycle:
            return
        native = self._lifecycle.native
        bundle = self._native_app.currentData()
        if not self._native_checks.isChecked() or not native.can_approve(bundle):
            QMessageBox.information(
                self,
                "Complete supervised checks first",
                "Approval requires observed joined and confirmed ended states in this session "
                "plus the full real-call checklist. Unknown/hidden alone does not prove an end.",
            )
            return
        stored = dict(self.config.get("native_call_qualifications", {}))
        stored[bundle] = qualification_key(
            bundle, native.versions[bundle], native.signers[bundle]
        )
        self.config["native_call_qualifications"] = stored
        absence = dict(self.config.get("native_call_absence_qualifications", {}))
        if self._test_absence.isChecked() and "absence-stop" in native.transitions[bundle]:
            absence[bundle] = qualification_key(
                bundle, native.versions[bundle], native.signers[bundle]
            )
        else:
            absence.pop(bundle, None)
        self.config["native_call_absence_qualifications"] = absence
        QMessageBox.information(
            self,
            "Approval prepared",
            "Save Settings to apply approval for this app version. "
            "App or matcher updates require testing again.",
        )

    def _revoke_native_version(self):
        stored = dict(self.config.get("native_call_qualifications", {}))
        stored.pop(self._native_app.currentData(), None)
        self.config["native_call_qualifications"] = stored
        absence = dict(self.config.get("native_call_absence_qualifications", {}))
        absence.pop(self._native_app.currentData(), None)
        self.config["native_call_absence_qualifications"] = absence

    def _credential_result(self, status):
        self._credential_status = status
        self._refresh_readiness()

    def _request_microphone(self):
        if sys.platform == "darwin":
            import AVFoundation

            AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                AVFoundation.AVMediaTypeAudio, lambda granted: None
            )

    def _request_notifications(self):
        if self._lifecycle:
            self._lifecycle.notifications.request()

    def _test_notification(self):
        if self._lifecycle:
            self._lifecycle.notify("error", "Meeting Recorder notification test")

    def _test_audio(self):
        if self._lifecycle:
            self._lifecycle.window._test_audio()

    def event(self, event):
        if event.type() == QEvent.Type.WindowActivate and hasattr(self, "_readiness"):
            self._refresh_readiness()
            if self._lifecycle:
                self._lifecycle.notifications.refresh()
        return super().event(event)

    def get_config(self):
        return self.config
