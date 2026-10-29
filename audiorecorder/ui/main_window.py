import logging
import os
import shutil
import sys
import threading
import time
from collections import deque

from PyQt6.QtCore import QEvent, QObject, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from audiorecorder import secrets, update_check
from audiorecorder.audio.encoder import mix_and_encode
from audiorecorder.audio.runtime import capture_runtime
from audiorecorder.audio.session import CaptureSession
from audiorecorder.config import DEFAULT_SUMMARY_PROMPT, save_config
from audiorecorder.dictation.engine import DictationEngine
from audiorecorder.dictation.hotkeys import HotkeyListener, HotkeyUnsupportedError
from audiorecorder.meetings.browser_bridge import BrowserMeetingBridge
from audiorecorder.meetings.calendar_detection import create_calendar_detector
from audiorecorder.meetings.detection import (
    CombinedMeetingDetector,
    MeetingPromptPolicy,
    create_meeting_detector,
)
from audiorecorder.readiness import capture_readiness
from audiorecorder.recordings.manager import RecordingManager, sidecars
from audiorecorder.subtitles.document import write_live_transcript
from audiorecorder.subtitles.engine import SubtitleEngine
from audiorecorder.subtitles.speaker_document import (
    resolve_speaker,
    write_speaker_sidecars,
)
from audiorecorder.transcription.cleanup import CleanupWorker
from audiorecorder.transcription.markdown import language_name
from audiorecorder.transcription.soniox import SonioxWorker
from audiorecorder.ui.audio_player import AudioPlayerWidget
from audiorecorder.ui.level_bars import DualLevelBars
from audiorecorder.ui.lifecycle_adapter import LifecycleAdapter
from audiorecorder.ui.overlay import OverlayWidget
from audiorecorder.ui.processing import ProcessingQueue
from audiorecorder.ui.recording_list import RecordingListWidget
from audiorecorder.ui.settings_dialog import SettingsDialog
from audiorecorder.ui.speaker_mapping import SpeakerMappingDialog
from audiorecorder.ui.subtitles import SubtitleView
from audiorecorder.version import __version__

log = logging.getLogger(__name__)

AUTO_START_SECONDS = 5
AUTO_START_SOURCES = frozenset()  # Native heuristics await per-source qualification.

# Both header toggles look the same when they are on: green means this is costing you
# a live Soniox session.
ACTIVE_TOGGLE_STYLE = """
    QPushButton {
        background-color: #2e7d32; color: white; font-weight: bold;
        border-radius: 4px; border: none; padding: 4px;
    }
    QPushButton:hover { background-color: #388e3c; }
"""


def _meeting_prompt_parent(window, platform_name=None):
    """Keep macOS prompts independent so they can join another app's Space."""
    platform_name = platform_name or sys.platform
    return None if platform_name == "darwin" else window


def _fanout(consumers):
    """One non-blocking capture callback for zero, one, or several realtime queues."""
    consumers = tuple(consumers)
    if not consumers:
        return None
    if len(consumers) == 1:
        return consumers[0]

    def feed(chunk):
        for consumer in consumers:
            consumer(chunk)

    return feed


class DictationHotkeyFilter(QObject):
    """Keeps Ctrl+Space away from the widgets while dictation is listening for it.

    The dictation hotkey is global, so it fires wherever the user is typing. When that
    happens to be this window, Qt ALSO delivers the keystroke to whatever widget has
    keyboard focus, and a focused button is activated by the spacebar. The Dictation button
    is usually the one, having just been clicked, so starting a dictation would immediately
    switch dictation off again. A button activates on the key RELEASE, which is why the
    recording appeared to start only once the keys were let go.
    """

    def __init__(self, is_listening, parent=None):
        # Parented, so Qt destroys the filter with the window that owns it rather than
        # leaving the application holding one whose owner has already gone.
        super().__init__(parent)
        self._is_listening = is_listening

    def eventFilter(self, obj, event):
        if not self._is_listening():
            return False
        if event.type() not in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            return False
        # bool(), because the modifier test yields a flag and eventFilter must return a bool.
        return bool(
            event.key() == Qt.Key.Key_Space
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        )


class EncoderWorker(QObject):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, system_path, mic_path, output_path, system_vol, mic_vol):
        super().__init__()
        self.system_path = system_path
        self.mic_path = mic_path
        self.output_path = output_path
        self.system_vol = system_vol
        self.mic_vol = mic_vol

    def run(self):
        try:
            self.result = mix_and_encode(
                self.system_path,
                self.mic_path,
                self.output_path,
                system_volume=self.system_vol,
                mic_volume=self.mic_vol,
            )
            self.finished.emit(self.output_path)
        except Exception as e:
            self.error.emit(str(e))


class MainWindow(QMainWindow):
    _system_level_signal = pyqtSignal(float)
    _mic_level_signal = pyqtSignal(float)
    _capture_error_signal = pyqtSignal(str, str)
    _update_available_signal = pyqtSignal(str)

    def __init__(self, config, notifications=None):
        super().__init__()
        self.config = config
        self._capture = None
        self._recording_start_time = None
        self._is_recording = False

        self._manager = RecordingManager(config["output_dir"])
        self._active_encoders = []
        self._encoder_queue = deque()
        self._encoding_ids = {}
        self._recording_config = {}
        self._partial_recordings = set()
        self._last_capture_test = config.get("capture_readiness")
        self._error_dialogs = []
        self._busy_paths = set()
        self._processing = ProcessingQueue(self)
        self._processing.progress.connect(self._on_processing_progress)
        self._processing.completed.connect(self._on_processing_completed)
        self._processing.failed.connect(self._on_processing_failed)
        self._processing.drained.connect(self._finish_pending_quit)
        self._dictation = None
        self._dictation_active = False
        self._subtitle_engine = None
        self._mic_speaker_engine = None
        self._remote_speaker_engine = None
        self._speaker_name_map = {}
        self._observed_remote_speakers = set()
        self._speaker_segments = []
        self._pending_sidecars = {}
        self._quitting = False
        self._tray_icon = None
        self._browser_meeting_bridge = BrowserMeetingBridge()
        self._calendar_meeting_detector = create_calendar_detector()
        self._native_meeting_detector = create_meeting_detector()
        self._meeting_detector = CombinedMeetingDetector(
            [
                self._native_meeting_detector,
                self._browser_meeting_bridge,
                self._calendar_meeting_detector,
            ]
        )
        self._meeting_prompt_policy = MeetingPromptPolicy()
        self._meeting_prompt = None
        self._overlay = OverlayWidget()
        self._hotkey_filter = DictationHotkeyFilter(lambda: self._dictation_active, self)
        QApplication.instance().installEventFilter(self._hotkey_filter)

        self.setWindowTitle(f"Meeting Recorder - v{__version__}")
        self.setMinimumSize(600, 650)
        self._build_ui()
        self._lifecycle = LifecycleAdapter(self, notifications)
        self._capture_session = CaptureSession()
        self._capture_session.started.connect(self._capture_started)
        self._capture_session.stopped.connect(self._capture_stopped)
        self._capture_session.failed.connect(self._on_capture_error)
        self._capture_session.system_level.connect(self._level_bars.set_system_level)
        self._capture_session.mic_level.connect(self._level_bars.set_mic_level)
        self._create_tray_icon()
        self._refresh_list()

        self._system_level_signal.connect(self._level_bars.set_system_level)
        self._mic_level_signal.connect(self._level_bars.set_mic_level)
        self._capture_error_signal.connect(self._on_capture_error)
        self._update_available_signal.connect(self._on_update_available)
        self._start_update_check()
        self._meeting_timer = QTimer(self)
        self._meeting_timer.setInterval(5000)
        self._meeting_timer.timeout.connect(self._poll_for_meeting)
        self._sync_meeting_detection()

    def _create_tray_icon(self):
        """Keep macOS detection reachable after the main window is closed."""
        if sys.platform != "darwin":
            return
        menu = QMenu(self)
        self._tray_start = menu.addAction("Start Recording", self._start_recording)
        self._tray_stop = menu.addAction("Stop Recording", self._stop_recording)
        self._tray_start.setEnabled(False)
        self._tray_stop.setEnabled(False)
        menu.addSeparator()
        menu.addAction("Open Meeting Recorder", self._show_from_tray)
        menu.aboutToShow.connect(self._update_tray_actions)

        tray = QSystemTrayIcon(QApplication.instance().windowIcon(), self)
        tray.setToolTip("Meeting Recorder · meeting detection is active")
        tray.setContextMenu(menu)
        # macOS opens the attached native menu on click. An activation handler
        # that raises the window would also bring the app forward when using it.
        tray.show()
        self._tray_icon = tray
        self._update_tray_actions()

    def _show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self):
        self._quitting = True
        self.close()

    def _start_update_check(self):
        """Ask GitHub for a newer release, off the UI thread, failing silently."""

        def check():
            latest = update_check.fetch_latest_version()
            if latest and update_check.is_newer(latest, __version__):
                self._update_available_signal.emit(latest)

        threading.Thread(target=check, daemon=True).start()

    def _on_update_available(self, latest):
        self._status_bar.showMessage(
            f"Version {latest} is available at {update_check.RELEASES_PAGE}", 15000
        )

    def _sync_meeting_detection(self):
        if hasattr(self, "_lifecycle"):
            self._lifecycle.configure()
        if self.config.get("meeting_detection", True) or self._lifecycle.native.testing:
            if not self._browser_meeting_bridge.start():
                self._status_bar.showMessage(
                    "Browser meeting detection unavailable: localhost port 8765 is busy",
                    8000,
                )
            if self.config.get("calendar_detection", False):
                self._calendar_meeting_detector.start()
            else:
                self._calendar_meeting_detector.stop()
            self._meeting_timer.start()
        else:
            self._meeting_timer.stop()
            self._browser_meeting_bridge.stop()
            self._calendar_meeting_detector.stop()
            if self._meeting_prompt is not None:
                self._meeting_prompt.close()
        self._update_browser_status()

    def _update_browser_status(self):
        if not self.config.get("meeting_detection", True):
            text = "Browser: detection off"
        else:
            status = self._browser_meeting_bridge.companion_status()
            if status.connected:
                text = "Browser: connected"
            elif status.listening:
                text = "Browser: not connected"
            else:
                text = "Browser: bridge unavailable"
        self._browser_status_label.setText(text)

    def _poll_for_meeting(self):
        self._lifecycle.poll()

    def _update_lifecycle_tray(self, text):
        if self._tray_icon is None:
            return
        self._tray_icon.setToolTip(f"Meeting Recorder · {text}")
        self._update_tray_actions()

    def _update_tray_actions(self):
        controller = self._lifecycle.controller
        controller.blocked = self._recording_start_blocked()
        self._tray_start.setEnabled(controller.can_start)
        self._tray_stop.setEnabled(controller.can_stop and not controller.closing)

    def _recording_start_blocked(self):
        return (
            self._dictation_active
            or len(self._encoder_queue) + len(self._active_encoders) >= 3
        )

    def _clear_meeting_prompt(self, prompt):
        if self._meeting_prompt is prompt:
            self._meeting_prompt = None
        prompt.deleteLater()

    def _start_detected_meeting(self):
        prompt = self._meeting_prompt
        if prompt is not None:
            self._lifecycle._manual_candidate(prompt.candidate.key)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(12)

        # --- Header ---
        header = QHBoxLayout()
        title = QLabel("Meeting Recorder")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        header.addWidget(title)
        header.addStretch()

        self._btn_dictation = QPushButton("Dictation")
        self._btn_dictation.setCheckable(True)
        self._btn_dictation.setFixedWidth(80)
        self._btn_dictation.clicked.connect(self._toggle_dictation)
        supported, reason = HotkeyListener.support()
        if not supported:
            self._btn_dictation.setEnabled(False)
            self._btn_dictation.setToolTip(reason)
        header.addWidget(self._btn_dictation)

        self._btn_rts = QPushButton("RTS Translate")
        self._btn_rts.setCheckable(True)
        self._btn_rts.setFixedWidth(105)
        self._btn_rts.setToolTip(
            "Live translated subtitles of the system audio while recording. "
            "Costs Soniox realtime minutes for the length of the call."
        )
        self._btn_rts.setChecked(bool(self.config.get("rts_translate")))
        self._btn_rts.setStyleSheet(ACTIVE_TOGGLE_STYLE if self._btn_rts.isChecked() else "")
        self._btn_rts.clicked.connect(self._toggle_subtitles)
        header.addWidget(self._btn_rts)

        self._btn_live_speakers = QPushButton("Live Speakers")
        self._btn_live_speakers.setCheckable(True)
        self._btn_live_speakers.setFixedWidth(105)
        self._btn_live_speakers.setToolTip(
            "Label microphone speech as Me and diarize system audio as Remote 1, "
            "Remote 2, etc. Adds up to two Soniox realtime sessions while recording."
        )
        self._btn_live_speakers.setChecked(bool(self.config.get("live_speakers")))
        self._btn_live_speakers.setStyleSheet(
            ACTIVE_TOGGLE_STYLE if self._btn_live_speakers.isChecked() else ""
        )
        self._btn_live_speakers.clicked.connect(self._toggle_live_speakers)
        header.addWidget(self._btn_live_speakers)

        btn_add = QPushButton("Add")
        btn_add.clicked.connect(self._add_files)
        header.addWidget(btn_add)

        btn_settings = QPushButton("Settings")
        btn_settings.clicked.connect(self._open_settings)
        header.addWidget(btn_settings)
        layout.addLayout(header)

        # --- Recording list ---
        self._recording_list = RecordingListWidget()
        self._recording_list.play_requested.connect(self._play_recording)
        self._recording_list.stop_requested.connect(self._stop_recording_playback)
        self._recording_list.transcribe_requested.connect(self._transcribe_recording)
        self._recording_list.cleanup_requested.connect(self._cleanup_recording)
        self._recording_list.move_requested.connect(self._move_recording)
        self._recording_list.delete_requested.connect(self._delete_recording)
        self._recording_list.open_transcript_requested.connect(self._open_transcript)
        layout.addWidget(self._recording_list, stretch=1)

        # --- Audio player (progress only) ---
        self._player = AudioPlayerWidget()
        self._player.playback_stopped.connect(self._on_playback_stopped)
        layout.addWidget(self._player)

        # --- Controls ---
        controls = QVBoxLayout()
        controls.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._btn_record = QPushButton("Record")
        self._btn_record.setFixedSize(120, 45)
        self._btn_record.setStyleSheet("""
            QPushButton {
                background-color: #c62828; color: white; font-size: 16px;
                font-weight: bold; border-radius: 22px; border: none;
            }
            QPushButton:hover { background-color: #b71c1c; }
            QPushButton:pressed { background-color: #e53935; }
        """)
        self._btn_record.clicked.connect(self._toggle_recording)

        self._btn_mute = QPushButton("Mute Mic")
        self._btn_mute.setFixedSize(90, 35)
        self._btn_mute.setStyleSheet("""
            QPushButton {
                background-color: #555; color: #ccc; font-size: 12px;
                border-radius: 17px; border: none;
            }
            QPushButton:hover { background-color: #666; }
        """)
        self._btn_mute.clicked.connect(self._toggle_mute)
        self._btn_mute.setVisible(False)

        btn_row = QHBoxLayout()
        btn_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        btn_row.addWidget(self._btn_record)
        btn_row.addWidget(self._btn_mute)
        controls.addLayout(btn_row)

        self._timer_label = QLabel("00:00:00")
        self._timer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._timer_label.setStyleSheet("font-size: 24px; font-family: monospace; color: #aaa;")
        controls.addWidget(self._timer_label)

        # --- Dual level bars ---
        self._level_bars = DualLevelBars()
        controls.addWidget(self._level_bars)

        layout.addLayout(controls)

        # --- Live subtitles, only while they are wanted ---
        self._subtitles = SubtitleView()
        self._subtitles.speaker_names_requested.connect(self._name_speakers)
        self._subtitles.set_speaker_mapping_visible(self._btn_live_speakers.isChecked())
        self._subtitles.setVisible(self._btn_rts.isChecked() or self._btn_live_speakers.isChecked())
        layout.addWidget(self._subtitles, stretch=1)

        # --- Status bar ---
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._browser_status_label = QLabel()
        self._browser_status_label.setToolTip(
            "The browser companion sends a local heartbeat when it is installed and enabled."
        )
        self._status_bar.addPermanentWidget(self._browser_status_label)
        # Permanent (right-aligned) version label, not cleared by transient messages.
        version_label = QLabel(f"v{__version__}")
        version_label.setStyleSheet("color: #888; padding: 0 6px;")
        self._status_bar.addPermanentWidget(version_label)

        # --- Timer ---
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_timer)

    def _toggle_recording(self):
        if self._lifecycle.controller.state == "capture-unresolved":
            self._retry_capture_stop()
        elif self._is_recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        if self._dictation_active:
            self._status_bar.showMessage("Stop dictation before recording", 5000)
            return
        self._lifecycle.controller.blocked = self._recording_start_blocked()
        self._lifecycle.controller.start(manual=True)
        self._lifecycle.process()

    def _automatic_capture_ready(self):
        return capture_readiness(self.config, self._last_capture_test, capture_runtime())

    def _test_audio(self):
        if self._lifecycle.controller.state != "idle" or self._dictation_active:
            self._status_bar.showMessage("Stop the current audio operation before testing", 5000)
            return
        self._start_recording()
        recording = self._lifecycle.controller.recording
        self._diagnostic_recording = recording

    def _begin_capture(self, recording):
        self._player.stop_playback()
        self._recording_config[recording] = dict(self.config)
        if self._lifecycle.controller.origin == "automatic":
            self._recording_config[recording]["_automatic_capture"] = True
            if self._last_capture_test:
                self._recording_config[recording]["_verified_sources"] = (
                    self._last_capture_test.get("system"),
                    self._last_capture_test.get("mic"),
                )
        self._btn_record.setEnabled(False)
        self._status_bar.showMessage("Starting capture…")
        self._capture_session.start(recording, self._recording_config[recording])

    def _capture_started(self, result):
        controller = self._lifecycle.controller
        if result.recording != controller.recording:
            return
        controller.started(result.recording, not result.error, result.unresolved)
        if result.error:
            self.config["capture_readiness"] = None
            self._last_capture_test = None
            save_config(self.config)
            self._btn_record.setEnabled(True)
            if result.unresolved:
                self._btn_record.setText("Retry Stop")
            self._status_bar.showMessage(f"Capture failed: {result.error}")
            self._lifecycle.notify("error", "Recording could not start", body=result.error)
            self._show_recording_error("Recording could not start", result.error)
            if not result.unresolved:
                self._recording_config.pop(result.recording, None)
            self._lifecycle.process()
            self._finish_pending_quit()
            return
        self._capture = result.backend
        self._btn_record.setEnabled(True)
        system_name = self._capture.active_system_source
        self._level_bars.set_device_names(
            system_name or "not recorded",
            self._capture.active_mic_source,
        )
        self._level_bars.start()

        self._is_recording = True
        self._recording_start_time = time.time()
        self._timer.start(200)

        self._btn_record.setText("Stop")
        self._btn_record.setStyleSheet("""
            QPushButton {
                background-color: #424242; color: white; font-size: 16px;
                font-weight: bold; border-radius: 22px; border: none;
            }
            QPushButton:hover { background-color: #555; }
        """)
        self._btn_mute.setVisible(True)
        self._btn_mute.setText("Mute Mic")
        self._btn_mute.setStyleSheet("""
            QPushButton {
                background-color: #555; color: #ccc; font-size: 12px;
                border-radius: 17px; border: none;
            }
            QPushButton:hover { background-color: #666; }
        """)
        if system_name is None:
            self._status_bar.showMessage(
                "Recording microphone only, no system audio source is configured"
            )
        else:
            self._status_bar.showMessage("Recording...")

        if self._btn_rts.isChecked() or self._btn_live_speakers.isChecked():
            self._subtitles.clear()
        if self._btn_rts.isChecked():
            self._start_subtitles(system_name)
        if self._btn_live_speakers.isChecked():
            self._speaker_name_map = {}
            self._observed_remote_speakers = set()
            self._speaker_segments = []
            self._subtitles.set_speaker_mapping_available(0)
            self._start_speaker_recognition(system_name)
        self._last_capture_test = {
            "recording": result.recording,
            "time": time.time(),
            "runtime": capture_runtime(),
            "config": {
                key: self._recording_config[result.recording].get(key)
                for key in ("output_dir", "system_source_name", "mic_source_name")
            },
            "system": self._capture.active_system_source,
            "mic": self._capture.active_mic_source,
            "status": "Capture started; audio not yet verified",
        }
        self._lifecycle.process()
        if getattr(self, "_diagnostic_recording", None) == result.recording:
            QTimer.singleShot(
                3000,
                lambda: (
                    self._stop_recording()
                    if (self._lifecycle.controller.recording == result.recording)
                    else None
                ),
            )

    def _toggle_mute(self):
        if not self._capture:
            return
        muted = not self._capture.mic_muted
        self._capture.mic_muted = muted
        if muted:
            self._btn_mute.setText("Unmute Mic")
            self._btn_mute.setStyleSheet("""
                QPushButton {
                    background-color: #c62828; color: white; font-size: 12px;
                    border-radius: 17px; border: none;
                }
                QPushButton:hover { background-color: #b71c1c; }
            """)
        else:
            self._btn_mute.setText("Mute Mic")
            self._btn_mute.setStyleSheet("""
                QPushButton {
                    background-color: #555; color: #ccc; font-size: 12px;
                    border-radius: 17px; border: none;
                }
                QPushButton:hover { background-color: #666; }
            """)

    def _stop_recording(self):
        self._lifecycle.controller.stop()
        self._lifecycle.process()

    def _retry_capture_stop(self):
        self._lifecycle.controller.retry_stop()
        self._lifecycle.process()

    def _begin_stop(self, recording):
        self._btn_record.setEnabled(False)
        self._status_bar.showMessage("Stopping capture…")
        self._capture_session.stop(recording)

    def _capture_stopped(self, result):
        controller = self._lifecycle.controller
        if result.recording != controller.recording:
            return
        if result.error:
            self.config["capture_readiness"] = None
            if self._last_capture_test:
                self._last_capture_test["status"] = "Capture shutdown unresolved"
            save_config(self.config)
            controller.stopped(result.recording, success=False)
            self._btn_record.setText("Retry Stop")
            self._btn_record.setEnabled(True)
            self._status_bar.showMessage(f"Capture shutdown unresolved: {result.error}")
            self._lifecycle.notify("error", "Capture shutdown needs attention", body=result.error)
            self._show_recording_error(
                "Capture shutdown is unresolved. Use Retry Stop before starting again.",
                result.error,
            )
            self._lifecycle.process()
            return
        if self._last_capture_test and self._last_capture_test.get("recording") == result.recording:
            self._last_capture_test["frames"] = dict(result.frames)
        system_path, mic_path = result.paths
        self._capture = None
        controller.stopped(result.recording, has_audio=bool(mic_path))
        self._btn_record.setEnabled(True)
        self._stop_speaker_recognition()

        self._is_recording = False
        self._timer.stop()
        self._level_bars.stop()
        self._btn_mute.setVisible(False)

        self._btn_record.setText("Record")
        self._btn_record.setStyleSheet("""
            QPushButton {
                background-color: #c62828; color: white; font-size: 16px;
                font-weight: bold; border-radius: 22px; border: none;
            }
            QPushButton:hover { background-color: #b71c1c; }
            QPushButton:pressed { background-color: #e53935; }
        """)
        # system_path is None on a microphone-only recording, which the encoder handles.
        if mic_path:
            output_name = self._manager.generate_filename()
            capture_config = self._recording_config.pop(result.recording)
            output_path = os.path.join(capture_config["output_dir"], output_name)
            pending = {
                "subtitles": self._finish_subtitles(),
                "speakers": self._speaker_sidecar_payload(),
            }
            if any(pending.values()):
                self._pending_sidecars[output_path] = pending

            self._status_bar.showMessage("Encoding to M4A...")

            thread = QThread()
            worker = EncoderWorker(
                system_path,
                mic_path,
                output_path,
                capture_config.get("system_volume", 1.0),
                capture_config.get("mic_volume", 1.0),
            )
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.finished.connect(self._on_encode_finished)
            worker.error.connect(lambda error, path=output_path: self._on_encode_error(error, path))
            worker.finished.connect(thread.quit)
            worker.error.connect(thread.quit)
            entry = (thread, worker)
            self._encoding_ids[output_path] = (result.recording, worker)
            self._encoder_queue.append(entry)
            thread.finished.connect(lambda e=entry: self._cleanup_encoder(e))
            self._start_next_encoder()
        else:
            self._finish_subtitles()
            self._status_bar.showMessage("Recording cancelled (no audio)")
            self._recording_config.pop(result.recording, None)
        self._lifecycle.process()
        self._finish_pending_quit()

    def _on_capture_error(self, recording, message):
        controller = self._lifecycle.controller
        if recording != controller.recording:
            return
        if controller.state == "starting":
            # The native backend can fail before its successful start reaches Qt.
            controller.cancel()
        elif self._is_recording:
            self._partial_recordings.add(recording)
            self._stop_recording()
        self.config["capture_readiness"] = None
        if self._last_capture_test:
            self._last_capture_test["status"] = "Capture failed"
        save_config(self.config)
        self._status_bar.showMessage(f"Capture failed: {message}")
        self._lifecycle.notify("error", "Capture interrupted", body=message)

    def _start_next_encoder(self):
        if not self._active_encoders and self._encoder_queue:
            entry = self._encoder_queue.popleft()
            self._active_encoders.append(entry)
            entry[0].start()

    def _background_threads_running(self):
        return self._processing.is_running()

    def _finish_pending_quit(self):
        if (
            self._quitting
            and not self._active_encoders
            and not self._encoder_queue
            and (self._lifecycle.controller.state == "idle")
        ):
            QTimer.singleShot(0, self.close)

    def _cleanup_encoder(self, entry):
        if entry in self._active_encoders:
            self._active_encoders.remove(entry)
        entry[0].deleteLater()
        self._start_next_encoder()
        self._finish_pending_quit()

    def _on_encode_finished(self, path):
        recording, worker = self._encoding_ids.pop(path)
        self._lifecycle.controller.finalized(recording)
        partial = recording in self._partial_recordings
        self._partial_recordings.discard(recording)
        title = "Partial recording saved" if partial else "Recording saved"
        self._lifecycle.notify("saved", title, path)
        if self._last_capture_test and self._last_capture_test.get("recording") == recording:
            self._last_capture_test["status"] = title + "; encoded audio validated"
            self.config["capture_readiness"] = {
                key: value for key, value in self._last_capture_test.items() if key != "recording"
            }
            save_config(self.config)
        companions = self._write_pending_sidecars(path)
        suffix = f" with {len(companions)} live transcript files" if companions else ""
        self._status_bar.showMessage(f"Saved: {os.path.basename(path)}{suffix}", 5000)
        self._refresh_list()
        if not self._is_recording:
            self._recording_list.highlight_path(path)
        if worker.result.cleanup_errors:
            self._status_bar.showMessage("Saved; temporary audio cleanup needs attention")

    def _on_encode_error(self, error, path):
        self.config["capture_readiness"] = None
        save_config(self.config)
        recording, worker = self._encoding_ids.pop(path)
        if self._last_capture_test and self._last_capture_test.get("recording") == recording:
            self._last_capture_test["status"] = "Encoding failed"
        self._lifecycle.controller.finalized(recording)
        self._partial_recordings.discard(recording)
        # Keep same-run transcript payloads with the recoverable source files.
        detail = f"{error}\nRetained audio: {worker.system_path}, {worker.mic_path}"
        self._lifecycle.notify("error", "Recording was not saved", body=detail)
        self._status_bar.showMessage("Encoding failed; source audio retained")
        self._show_recording_error("Recording was not saved. Source audio was retained.", detail)

    def _show_recording_error(self, message, detail):
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Recording recovery")
        dialog.setText(message)
        dialog.setDetailedText(detail)
        dialog.setModal(False)
        self._error_dialogs.append(dialog)
        dialog.finished.connect(lambda: self._error_dialogs.remove(dialog))
        dialog.show()

    def _open_saved(self, path, reveal=False):
        if not os.path.isfile(path):
            self._status_bar.showMessage("Recording was moved or deleted", 5000)
            return
        if reveal:
            if sys.platform == "darwin":
                from AppKit import NSWorkspace

                NSWorkspace.sharedWorkspace().selectFile_inFileViewerRootedAtPath_(path, "")
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _update_timer(self):
        if self._recording_start_time:
            elapsed = time.time() - self._recording_start_time
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            s = int(elapsed % 60)
            self._timer_label.setText(f"{h:02d}:{m:02d}:{s:02d}")

    def _refresh_list(self):
        self._manager = RecordingManager(self.config["output_dir"])
        recordings = self._manager.scan()
        self._recording_list.set_recordings(recordings)

    def _play_recording(self, path):
        self._player.play_file(path)
        self._recording_list.set_playing_path(path)

    def _stop_recording_playback(self, path):
        self._player.stop_playback()
        self._recording_list.clear_playing()

    def _on_playback_stopped(self):
        self._recording_list.clear_playing()

    def _open_transcript(self, path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _move_recording(self, path):
        if path in self._busy_paths:
            QMessageBox.warning(
                self, "Busy", "This recording is being processed. Wait until it finishes."
            )
            return
        if self._player.current_path == path:
            self._player.stop_playback()
            self._recording_list.clear_playing()

        last_move_dir = self.config.get("last_move_dir", os.path.dirname(path))
        dest_dir = QFileDialog.getExistingDirectory(
            self,
            "Move recording to...",
            last_move_dir,
        )
        if not dest_dir:
            return

        self.config["last_move_dir"] = dest_dir
        save_config(self.config)

        try:
            p = os.path.basename(path)
            shutil.move(path, os.path.join(dest_dir, p))

            for sidecar in sidecars(path):
                shutil.move(str(sidecar), os.path.join(dest_dir, sidecar.name))

            self._status_bar.showMessage(f"Moved to {dest_dir}", 5000)
            self._refresh_list()
        except Exception as e:
            QMessageBox.warning(self, "Move Error", f"Failed to move recording:\n{e}")

    def _delete_recording(self, path):
        if path in self._busy_paths:
            QMessageBox.warning(
                self, "Busy", "This recording is being processed. Wait until it finishes."
            )
            return
        name = os.path.basename(path)
        reply = QMessageBox.question(
            self,
            "Delete Recording",
            f"Delete '{name}' and its transcript?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            if self._player.current_path == path:
                self._player.stop_playback()
                self._recording_list.clear_playing()
            self._manager.delete(path)
            self._refresh_list()

    def _transcribe_recording(self, path):
        if self._quitting:
            return
        if path in self._busy_paths:
            QMessageBox.warning(self, "Busy", "This recording is already being processed.")
            return
        api_key = secrets.get_api_key()
        if not api_key:
            QMessageBox.warning(
                self,
                "API Key Missing",
                "Soniox API key not configured.\nEnter it in Settings, or set the "
                "SONIOX_API_KEY environment variable.",
            )
            return

        lang = self.config.get("language", "en")
        # Auto sends no hints, so Soniox identifies the language without being nudged.
        hints = [] if lang == "auto" else [lang]
        translate = self.config.get("enable_translation", False) and lang != "auto"

        worker = SonioxWorker(
            api_key=api_key, audio_path=path, language_hints=hints,
            primary_language=lang, enable_translation=translate,
        )
        self._busy_paths.add(path)
        self._processing.enqueue("transcribe", path, worker)

    def _on_processing_progress(self, path, message):
        self._recording_list.set_processing(path, message, busy=True)
        self._status_bar.showMessage(f"{os.path.basename(path)}: {message}")

    def _on_processing_completed(self, kind, path, output):
        self._busy_paths.discard(path)
        self._recording_list.set_processing(path, "", busy=False)
        self._refresh_list()
        self._status_bar.showMessage(f"Saved: {os.path.basename(output)}", 8000)

    def _on_processing_failed(self, kind, path, error):
        self._busy_paths.discard(path)
        self._refresh_list()
        self._recording_list.set_processing(path, "Failed — retry or see error details", busy=False)
        self._status_bar.showMessage(f"Processing failed: {os.path.basename(path)}")
        # Non-modal: one failed queued job must not stall other jobs or shutdown.
        title = "Transcription Error" if kind == "transcribe" else "Cleanup Error"
        dialog = QMessageBox(QMessageBox.Icon.Warning, title, str(error), parent=self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._error_dialogs.append(dialog)
        dialog.destroyed.connect(lambda: self._error_dialogs.remove(dialog))
        dialog.show()

    def _cleanup_recording(self, path):
        if self._quitting:
            return
        if path in self._busy_paths:
            QMessageBox.warning(self, "Busy", "This recording is already being processed.")
            return
        transcript_path = os.path.splitext(path)[0] + ".md"
        if not os.path.exists(transcript_path):
            QMessageBox.warning(self, "No Transcript", "Transcribe the recording first.")
            return

        summary_path = os.path.splitext(path)[0] + ".summary.md"
        if os.path.exists(summary_path):
            reply = QMessageBox.question(
                self,
                "Already Processed",
                "A summary file already exists. Overwrite?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        worker = CleanupWorker(
            transcript_path, self.config.get("summary_prompt", DEFAULT_SUMMARY_PROMPT)
        )
        self._busy_paths.add(path)
        self._processing.enqueue("cleanup", path, worker)

    def _toggle_subtitles(self):
        wanted = self._btn_rts.isChecked()
        self.config["rts_translate"] = wanted
        save_config(self.config)
        self._update_live_view_visibility()
        self._btn_rts.setStyleSheet(ACTIVE_TOGGLE_STYLE if wanted else "")
        if wanted:
            # The subtitles read the capture backend, which only exists while recording.
            self._status_bar.showMessage("Subtitles start with the next recording", 5000)
        elif self._subtitle_engine is not None:
            # Switching off mid-recording has to close the session, not just hide it.
            # Soniox bills for the audio streamed to it, and a hidden session that is
            # still being fed is billed exactly like a visible one.
            self._stop_feeding_subtitles()
            self._status_bar.showMessage("Subtitles stopped", 5000)

    def _stop_feeding_subtitles(self):
        """Stop sending audio, but keep what was heard for the sidecar."""
        self._subtitle_engine.stop()
        self._refresh_audio_taps()

    def _start_subtitles(self, system_source_name):
        if system_source_name is None:
            self._subtitles.set_status("Live subtitles need a system audio source")
            return

        api_key = secrets.get_api_key()
        if not api_key:
            self._subtitles.set_status("Live subtitles need a Soniox API key")
            self._status_bar.showMessage("Subtitles are off: no Soniox API key is configured", 8000)
            return

        try:
            # Both configured languages are offered as candidates for what is being
            # spoken, and the translation goes into the one you asked to read. A call
            # can turn out to be in either of them, and naming both is what stops the
            # model reaching for a third.
            self._subtitle_engine = SubtitleEngine(
                api_key=api_key,
                translate_to=self.config.get("translation_target"),
                source_languages=self._source_languages(),
            )
        except ValueError as e:
            self._subtitles.set_status(str(e))
            return

        target = self._subtitle_engine.target_language
        self._subtitles.set_status(
            f"Live subtitles, translated into {language_name(target)}"
            if target
            else "Live subtitles, not translated"
        )
        self._subtitle_engine.line_finalized.connect(self._subtitles.append_line)
        self._subtitle_engine.line_updated.connect(self._subtitles.set_pending)
        self._subtitle_engine.notice.connect(
            lambda message: self._status_bar.showMessage(message, 8000)
        )
        self._subtitle_engine.error_occurred.connect(self._on_subtitle_error)
        self._subtitle_engine.start()
        self._refresh_audio_taps()

    def _toggle_live_speakers(self):
        wanted = self._btn_live_speakers.isChecked()
        self.config["live_speakers"] = wanted
        save_config(self.config)
        self._update_live_view_visibility()
        self._btn_live_speakers.setStyleSheet(ACTIVE_TOGGLE_STYLE if wanted else "")
        self._subtitles.set_speaker_mapping_visible(wanted)
        if wanted:
            self._status_bar.showMessage("Speaker labels start with the next recording", 5000)
        elif self._mic_speaker_engine is not None or self._remote_speaker_engine is not None:
            self._stop_speaker_recognition()
            self._status_bar.showMessage("Live speaker recognition stopped", 5000)

    def _update_live_view_visibility(self):
        self._subtitles.setVisible(self._btn_rts.isChecked() or self._btn_live_speakers.isChecked())

    def _start_speaker_recognition(self, system_source_name):
        api_key = secrets.get_api_key()
        if not api_key:
            self._subtitles.set_status("Live speaker labels need a Soniox API key")
            self._status_bar.showMessage(
                "Speaker labels are off: no Soniox API key is configured", 8000
            )
            return

        languages = self._source_languages()
        self._mic_speaker_engine = SubtitleEngine(
            api_key=api_key,
            translate_to=None,
            source_languages=languages,
            speaker_label="Me",
        )
        if system_source_name is not None:
            self._remote_speaker_engine = SubtitleEngine(
                api_key=api_key,
                translate_to=None,
                source_languages=languages,
                speaker_prefix="Remote",
                enable_speaker_diarization=True,
            )

        engines = [
            engine
            for engine in (self._mic_speaker_engine, self._remote_speaker_engine)
            if engine is not None
        ]
        for engine in engines:
            engine.speaker_line_finalized.connect(self._on_speaker_line)
            engine.notice.connect(lambda message: self._status_bar.showMessage(message, 8000))
            engine.error_occurred.connect(self._on_subtitle_error)
            engine.start()

        if self._btn_rts.isChecked():
            self._subtitles.set_status("Live subtitles and speaker labels")
        elif system_source_name is None:
            self._subtitles.set_status("Live speaker transcript (microphone only)")
        else:
            self._subtitles.set_status("Live speaker transcript")
        self._refresh_audio_taps()

    def _source_languages(self):
        return [self.config.get("language"), self.config.get("translation_target")]

    def _stop_speaker_recognition(self):
        engines = (self._mic_speaker_engine, self._remote_speaker_engine)
        self._mic_speaker_engine = None
        self._remote_speaker_engine = None
        for engine in engines:
            if engine is not None:
                engine.request_stop()
        for engine in engines:
            if engine is not None:
                engine.wait_stopped()
        merged = []
        for engine in engines:
            if engine is not None:
                merged.extend(engine.timed_speaker_transcript())
        self._speaker_segments.extend(merged)
        self._speaker_segments.sort(key=lambda line: line[0])
        self._refresh_audio_taps()

    def _on_speaker_line(self, timestamp, speaker_id, text):
        if speaker_id != "Me":
            self._observed_remote_speakers.add(speaker_id)
            self._subtitles.set_speaker_mapping_available(len(self._observed_remote_speakers))
        self._subtitles.append_speaker_line(
            timestamp, resolve_speaker(speaker_id, self._speaker_name_map), text
        )

    def _name_speakers(self):
        if not self._observed_remote_speakers:
            return
        dialog = SpeakerMappingDialog(self._observed_remote_speakers, self._speaker_name_map, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        for speaker_id in self._observed_remote_speakers:
            self._speaker_name_map.pop(speaker_id, None)
        self._speaker_name_map.update(dialog.mappings())
        self._status_bar.showMessage(
            "Speaker names will be used for new live lines and the complete sidecar", 6000
        )

    def _speaker_sidecar_payload(self):
        lines = [line[1:] for line in self._speaker_segments]
        if not lines:
            return None
        return lines, dict(self._speaker_name_map)

    def _refresh_audio_taps(self):
        """Attach each active realtime consumer without doing work in audio callbacks."""
        if self._capture is None:
            return
        system_consumers = []
        if self._subtitle_engine is not None and self._btn_rts.isChecked():
            system_consumers.append(self._subtitle_engine.feed)
        if self._remote_speaker_engine is not None and self._btn_live_speakers.isChecked():
            system_consumers.append(self._remote_speaker_engine.feed)
        mic_consumers = []
        if self._mic_speaker_engine is not None and self._btn_live_speakers.isChecked():
            mic_consumers.append(self._mic_speaker_engine.feed)

        self._capture.set_system_tap(_fanout(system_consumers))
        self._capture.set_mic_tap(_fanout(mic_consumers))

    def _finish_subtitles(self):
        """End the session and retain its result until the M4A safely exists."""
        if self._subtitle_engine is None:
            return None
        engine = self._subtitle_engine
        self._subtitle_engine = None
        engine.stop()
        self._subtitles.set_pending("", "")
        lines = engine.transcript()
        return (lines, engine.target_language) if lines else None

    def _write_pending_sidecars(self, recording_path):
        pending = self._pending_sidecars.pop(recording_path, None)
        if pending is None:
            return []
        written = []
        subtitle_payload = pending.get("subtitles")
        if subtitle_payload:
            try:
                path = write_live_transcript(recording_path, *subtitle_payload)
                if path is not None:
                    written.append(path)
            except OSError:
                log.exception("the live subtitle sidecar could not be written")
        speaker_payload = pending.get("speakers")
        if speaker_payload:
            try:
                paths = write_speaker_sidecars(recording_path, *speaker_payload)
                if paths is not None:
                    written.extend(paths)
            except OSError:
                log.exception("the live speaker sidecars could not be written")
        return written

    def _on_subtitle_error(self, message):
        log.error("subtitles failed: %s", message)
        self._status_bar.showMessage(f"Subtitles: {message}", 8000)

    def _toggle_dictation(self):
        if self._dictation_active:
            self._stop_dictation()
        else:
            self._start_dictation()

    def _start_dictation(self):
        if self._is_recording:
            QMessageBox.warning(
                self, "Recording Active", "Stop recording before enabling dictation."
            )
            self._btn_dictation.setChecked(False)
            return

        api_key = secrets.get_api_key()
        if not api_key:
            QMessageBox.warning(self, "API Key Missing", "Soniox API key not configured.")
            self._btn_dictation.setChecked(False)
            return

        self._dictation = DictationEngine(
            api_key=api_key,
            language=self.config.get("language", "en"),
            translate_target=self.config.get("translation_target", "en"),
            mic_source_name=self.config.get("mic_source_name"),
            sample_rate=16000,
        )
        self._dictation.state_changed.connect(self._on_dictation_state)
        self._dictation.level_changed.connect(self._level_bars.set_mic_level)
        self._dictation.level_changed.connect(self._overlay.set_level)
        self._dictation.text_pasted.connect(self._on_dictation_pasted)
        self._dictation.error_occurred.connect(self._on_dictation_error)
        try:
            self._dictation.start()
        except HotkeyUnsupportedError as e:
            self._dictation = None
            QMessageBox.warning(self, "Dictation Unavailable", str(e))
            self._btn_dictation.setChecked(False)
            self._btn_dictation.setEnabled(False)
            self._btn_dictation.setToolTip(str(e))
            return
        except Exception as e:
            self._dictation = None
            QMessageBox.warning(self, "Dictation Unavailable", str(e))
            self._btn_dictation.setChecked(False)
            return

        self._dictation_active = True
        self._btn_dictation.setChecked(True)
        self._btn_dictation.setStyleSheet(ACTIVE_TOGGLE_STYLE)
        self._level_bars.start()
        self._level_bars.set_device_names("", "Dictation mic")
        self._overlay.activate()
        self._status_bar.showMessage(
            "Dictation ON - Ctrl+Space to dictate, Ctrl+Shift+Space to translate"
        )

    def _stop_dictation(self):
        if self._dictation:
            self._dictation.stop()
            self._dictation = None

        self._dictation_active = False
        self._btn_dictation.setChecked(False)
        self._btn_dictation.setStyleSheet("")
        self._level_bars.stop()
        self._overlay.deactivate()
        self._status_bar.showMessage("Dictation OFF", 3000)

    def _on_dictation_state(self, state):
        if state == "recording":
            self._overlay.set_state("recording")
            self._status_bar.showMessage("Dictating...")
        elif state == "finishing":
            self._overlay.set_state("idle")
            self._status_bar.showMessage("Processing...")
        elif state == "idle":
            self._overlay.set_state("idle")
            if self._dictation_active:
                self._status_bar.showMessage("Dictation ON - Ctrl+Space to dictate")
        elif state.startswith("notice:"):
            self._overlay.set_state("idle")
            self._status_bar.showMessage(state[7:], 5000)
        elif state.startswith("preview:"):
            preview = state[8:]
            if len(preview) > 60:
                preview = "..." + preview[-57:]
            self._status_bar.showMessage(f"Live: {preview}")

    def _on_dictation_pasted(self, text):
        if len(text) > 60:
            text = text[:57] + "..."
        self._status_bar.showMessage(f"Pasted: {text}", 5000)

    def _on_dictation_error(self, error):
        # Logged as well as shown: the status bar clears itself after a few seconds, and a
        # windowed build has no stderr to print to at all.
        log.error("dictation failed: %s", error)
        self._status_bar.showMessage(f"Dictation error: {error}", 5000)

    def _add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Add audio files",
            self.config.get("last_add_dir", ""),
            "Audio files (*.m4a *.mp3 *.wav *.ogg *.flac *.wma *.aac);;All files (*)",
        )
        if not files:
            return

        self.config["last_add_dir"] = os.path.dirname(files[0])
        save_config(self.config)

        added = 0
        for src in files:
            dest = os.path.join(self.config["output_dir"], os.path.basename(src))
            if os.path.exists(dest):
                reply = QMessageBox.question(
                    self,
                    "File Exists",
                    f"'{os.path.basename(src)}' already exists. Overwrite?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    continue
            shutil.copy2(src, dest)
            added += 1

        if added:
            self._refresh_list()
            self._status_bar.showMessage(f"Added {added} file(s)", 5000)

    def _open_settings(self):
        dialog = SettingsDialog(
            self.config,
            self,
            browser_bridge=self._browser_meeting_bridge,
            meeting_detector=self._native_meeting_detector,
            lifecycle=self._lifecycle,
        )
        if dialog.exec():
            self.config = dialog.get_config()
            save_config(self.config)
            os.makedirs(self.config["output_dir"], exist_ok=True)
            self._refresh_list()
            self._sync_meeting_detection()
            self._status_bar.showMessage("Settings saved", 3000)

    def closeEvent(self, event):
        if (
            not self._quitting
            and event.spontaneous()
            and self._tray_icon is not None
            and self._tray_icon.isVisible()
        ):
            self.hide()
            event.ignore()
            return
        self._quitting = True
        if hasattr(self, "_lifecycle"):
            self._lifecycle.controller.shutdown()
            self._lifecycle.process()
            if self._lifecycle.controller.state != "idle":
                event.ignore()
                return
        if (
            self._active_encoders
            or self._busy_paths
            or getattr(self, "_encoder_queue", ())
            or (hasattr(self, "_processing") and self._background_threads_running())
        ):
            self._status_bar.showMessage("Finishing background work before exit.", 8000)
            event.ignore()
            return
        if self._dictation_active:
            self._stop_dictation()
        if self._meeting_prompt is not None:
            self._meeting_prompt.close()
        self._meeting_timer.stop()
        self._browser_meeting_bridge.stop()
        self._calendar_meeting_detector.stop()
        self._lifecycle.close()
        self._capture_session.close()
        if self._tray_icon is not None:
            self._tray_icon.hide()
        QApplication.instance().removeEventFilter(self._hotkey_filter)
        self._exit_ready = True
        event.accept()
        QTimer.singleShot(0, QApplication.instance().quit)
