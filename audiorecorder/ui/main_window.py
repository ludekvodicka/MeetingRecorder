import logging
import os
import shutil
import sys
import threading
import time

from PyQt6.QtCore import QEvent, QObject, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QDesktopServices
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
from audiorecorder.audio.backend import CaptureError, create_backend
from audiorecorder.audio.encoder import mix_and_encode
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
from audiorecorder.ui.meeting_prompt import MeetingPrompt
from audiorecorder.ui.overlay import OverlayWidget
from audiorecorder.ui.recording_list import RecordingListWidget
from audiorecorder.ui.settings_dialog import SettingsDialog
from audiorecorder.ui.speaker_mapping import SpeakerMappingDialog
from audiorecorder.ui.subtitles import SubtitleView
from audiorecorder.version import __version__

log = logging.getLogger(__name__)

AUTO_START_SECONDS = 10
AUTO_START_SOURCES = frozenset({"native-window", "browser-extension"})

# Both header toggles look the same when they are on: green means this is costing you
# a live Soniox session.
ACTIVE_TOGGLE_STYLE = """
    QPushButton {
        background-color: #2e7d32; color: white; font-weight: bold;
        border-radius: 4px; border: none; padding: 4px;
    }
    QPushButton:hover { background-color: #388e3c; }
"""


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
        return bool(event.key() == Qt.Key.Key_Space
                    and event.modifiers() & Qt.KeyboardModifier.ControlModifier)


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
            mix_and_encode(
                self.system_path, self.mic_path, self.output_path,
                system_volume=self.system_vol, mic_volume=self.mic_vol,
            )
            self.finished.emit(self.output_path)
        except Exception as e:
            self.error.emit(str(e))


class MainWindow(QMainWindow):
    _system_level_signal = pyqtSignal(float)
    _mic_level_signal = pyqtSignal(float)
    _capture_error_signal = pyqtSignal(str)
    _update_available_signal = pyqtSignal(str)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self._capture = None
        self._recording_start_time = None
        self._is_recording = False

        self._manager = RecordingManager(config["output_dir"])
        self._active_encoders = []
        self._busy_paths = set()
        self._transcribe_thread = None
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
        self._meeting_detector = CombinedMeetingDetector([
            create_meeting_detector(),
            self._browser_meeting_bridge,
            self._calendar_meeting_detector,
        ])
        self._meeting_prompt_policy = MeetingPromptPolicy()
        self._meeting_prompt = None
        self._overlay = OverlayWidget()
        self._hotkey_filter = DictationHotkeyFilter(lambda: self._dictation_active, self)
        QApplication.instance().installEventFilter(self._hotkey_filter)

        self.setWindowTitle(f"Meeting Recorder - v{__version__}")
        self.setMinimumSize(600, 650)
        self._build_ui()
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
        show_action = QAction("Show Meeting Recorder", self)
        show_action.triggered.connect(self._show_from_tray)
        menu.addAction(show_action)
        menu.addSeparator()
        quit_action = QAction("Quit Meeting Recorder", self)
        quit_action.triggered.connect(self._quit_from_tray)
        menu.addAction(quit_action)

        tray = QSystemTrayIcon(QApplication.instance().windowIcon(), self)
        tray.setToolTip("Meeting Recorder · meeting detection is active")
        tray.setContextMenu(menu)
        tray.activated.connect(self._tray_activated)
        tray.show()
        self._tray_icon = tray

    def _show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._show_from_tray()

    def _quit_from_tray(self):
        self._quitting = True
        QApplication.instance().quit()

    def _start_update_check(self):
        """Ask GitHub for a newer release, off the UI thread, failing silently."""
        def check():
            latest = update_check.fetch_latest_version()
            if latest and update_check.is_newer(latest, __version__):
                self._update_available_signal.emit(latest)

        threading.Thread(target=check, daemon=True).start()

    def _on_update_available(self, latest):
        self._status_bar.showMessage(
            f"Version {latest} is available at {update_check.RELEASES_PAGE}", 15000)

    def _sync_meeting_detection(self):
        if self.config.get("meeting_detection", True):
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
        self._update_browser_status()
        calendar_notice = self._calendar_meeting_detector.pop_notice()
        if calendar_notice:
            self._status_bar.showMessage(calendar_notice, 8000)
        candidates = self._meeting_detector.scan()
        if (self._meeting_prompt is not None
                and self._meeting_prompt.is_automatic
                and self._meeting_prompt.candidate.key
                not in {candidate.key for candidate in candidates}):
            self._meeting_prompt_policy.next_prompt(candidates, blocked=True)
            self._meeting_prompt.reject()
            return
        candidate = self._meeting_prompt_policy.next_prompt(
            candidates,
            blocked=self._is_recording or self._meeting_prompt is not None,
        )
        if candidate is None:
            return
        automatic = (
            self.config.get("automatic_recording", False)
            and candidate.source in AUTO_START_SOURCES
        )
        prompt = MeetingPrompt(
            candidate,
            self,
            auto_start_seconds=AUTO_START_SECONDS if automatic else None,
        )
        self._meeting_prompt = prompt
        prompt.accepted.connect(self._start_detected_meeting)
        prompt.finished.connect(
            lambda result, shown=prompt: self._clear_meeting_prompt(shown))
        prompt.show()
        prompt.raise_()
        prompt.activateWindow()
        QApplication.alert(prompt, 0)

    def _clear_meeting_prompt(self, prompt):
        if self._meeting_prompt is prompt:
            self._meeting_prompt = None
        prompt.deleteLater()

    def _start_detected_meeting(self):
        prompt = self._meeting_prompt
        if prompt is not None and prompt.countdown_elapsed:
            active = {candidate.key for candidate in self._meeting_detector.scan()}
            if prompt.candidate.key not in active:
                self._status_bar.showMessage(
                    "Automatic recording cancelled because the meeting ended", 5000)
                return
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if not self._is_recording:
            self._start_recording()

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
            "Costs Soniox realtime minutes for the length of the call.")
        self._btn_rts.setChecked(bool(self.config.get("rts_translate")))
        self._btn_rts.setStyleSheet(
            ACTIVE_TOGGLE_STYLE if self._btn_rts.isChecked() else "")
        self._btn_rts.clicked.connect(self._toggle_subtitles)
        header.addWidget(self._btn_rts)

        self._btn_live_speakers = QPushButton("Live Speakers")
        self._btn_live_speakers.setCheckable(True)
        self._btn_live_speakers.setFixedWidth(105)
        self._btn_live_speakers.setToolTip(
            "Label microphone speech as Me and diarize system audio as Remote 1, "
            "Remote 2, etc. Adds up to two Soniox realtime sessions while recording.")
        self._btn_live_speakers.setChecked(bool(self.config.get("live_speakers")))
        self._btn_live_speakers.setStyleSheet(
            ACTIVE_TOGGLE_STYLE if self._btn_live_speakers.isChecked() else "")
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
        self._subtitles.set_speaker_mapping_visible(
            self._btn_live_speakers.isChecked())
        self._subtitles.setVisible(
            self._btn_rts.isChecked() or self._btn_live_speakers.isChecked())
        layout.addWidget(self._subtitles, stretch=1)

        # --- Status bar ---
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._browser_status_label = QLabel()
        self._browser_status_label.setToolTip(
            "The browser companion sends a local heartbeat when it is installed and enabled.")
        self._status_bar.addPermanentWidget(self._browser_status_label)
        # Permanent (right-aligned) version label, not cleared by transient messages.
        version_label = QLabel(f"v{__version__}")
        version_label.setStyleSheet("color: #888; padding: 0 6px;")
        self._status_bar.addPermanentWidget(version_label)

        # --- Timer ---
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_timer)

    def _toggle_recording(self):
        if self._is_recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        if self._dictation_active:
            self._stop_dictation()
        self._player.stop_playback()
        try:
            self._capture = create_backend()
            self._capture.set_level_callbacks(
                lambda lvl: self._system_level_signal.emit(lvl),
                lambda lvl: self._mic_level_signal.emit(lvl),
            )
            self._capture.set_error_callback(
                lambda message: self._capture_error_signal.emit(message)
            )
            self._capture.start(
                tmp_dir=self.config["output_dir"],
                system_source=self.config.get("system_source_name"),
                mic_source=self.config.get("mic_source_name"),
            )
        except CaptureError as e:
            QMessageBox.critical(self, "Recording Error", str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, "Recording Error", f"Failed to start recording:\n{e}")
            return

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
                "Recording microphone only, no system audio source is configured")
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
        if not self._capture:
            return

        system_path, mic_path = self._capture.stop()
        self._capture.set_system_tap(None)
        self._capture.set_mic_tap(None)
        self._capture.close()
        self._capture = None
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
            output_path = os.path.join(self.config["output_dir"], output_name)
            pending = {
                "subtitles": self._finish_subtitles(),
                "speakers": self._speaker_sidecar_payload(),
            }
            if any(pending.values()):
                self._pending_sidecars[output_path] = pending

            self._status_bar.showMessage("Encoding to M4A...")

            thread = QThread()
            worker = EncoderWorker(
                system_path, mic_path, output_path,
                self.config.get("system_volume", 1.0),
                self.config.get("mic_volume", 1.0),
            )
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.finished.connect(self._on_encode_finished)
            worker.error.connect(self._on_encode_error)
            worker.error.connect(
                lambda error, path=output_path: self._pending_sidecars.pop(path, None))
            worker.finished.connect(thread.quit)
            worker.error.connect(thread.quit)
            entry = (thread, worker)
            self._active_encoders.append(entry)
            thread.finished.connect(lambda e=entry: self._cleanup_encoder(e))
            thread.start()
        else:
            self._finish_subtitles()
            self._status_bar.showMessage("Recording cancelled (no audio)")

    def _on_capture_error(self, message):
        if not self._is_recording:
            return
        log.error("capture failed while recording: %s", message)
        self._stop_recording()
        QMessageBox.warning(
            self,
            "Recording Stopped",
            f"{message}\n\nRecording stopped immediately; a partial recording is being saved.",
        )

    def _cleanup_encoder(self, entry):
        if entry in self._active_encoders:
            self._active_encoders.remove(entry)

    def _on_encode_finished(self, path):
        companions = self._write_pending_sidecars(path)
        suffix = f" with {len(companions)} live transcript files" if companions else ""
        self._status_bar.showMessage(
            f"Saved: {os.path.basename(path)}{suffix}", 5000)
        self._refresh_list()
        self._recording_list.highlight_path(path)

    def _on_encode_error(self, error):
        self._status_bar.showMessage("Encoding failed")
        QMessageBox.warning(self, "Encoding Error", f"Failed to encode M4A:\n{error}")

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
                self, "Busy", "This recording is being processed. Wait until it finishes.")
            return
        if self._player.current_path == path:
            self._player.stop_playback()
            self._recording_list.clear_playing()

        last_move_dir = self.config.get("last_move_dir", os.path.dirname(path))
        dest_dir = QFileDialog.getExistingDirectory(
            self, "Move recording to...", last_move_dir,
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
                self, "Busy", "This recording is being processed. Wait until it finishes.")
            return
        name = os.path.basename(path)
        reply = QMessageBox.question(
            self, "Delete Recording",
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
        if path in self._busy_paths:
            QMessageBox.warning(self, "Busy", "This recording is already being processed.")
            return
        api_key = secrets.get_api_key()
        if not api_key:
            QMessageBox.warning(
                self, "API Key Missing",
                "Soniox API key not configured.\nEnter it in Settings, or set the "
                "SONIOX_API_KEY environment variable.",
            )
            return

        lang = self.config.get("language", "en")
        # Auto sends no hints, so Soniox identifies the language without being nudged.
        hints = [] if lang == "auto" else [lang]
        translate = self.config.get("enable_translation", False) and lang != "auto"

        self._transcribe_thread = QThread()
        worker = SonioxWorker(
            api_key=api_key,
            audio_path=path,
            language_hints=hints,
            primary_language=lang,
            enable_translation=translate,
        )
        worker.moveToThread(self._transcribe_thread)
        self._transcribe_thread.started.connect(worker.run)
        worker.progress.connect(lambda msg: self._status_bar.showMessage(msg))
        worker.completed.connect(self._on_transcribe_finished)
        worker.error.connect(self._on_transcribe_error)
        worker.completed.connect(self._transcribe_thread.quit)
        worker.error.connect(self._transcribe_thread.quit)
        self._transcribe_worker = worker
        self._transcribe_path = path
        self._busy_paths.add(path)
        self._transcribe_thread.start()

        self._status_bar.showMessage("Starting transcription...")

    def _on_transcribe_finished(self, transcript_path):
        self._busy_paths.discard(self._transcribe_path)
        self._status_bar.showMessage(f"Transcript saved: {os.path.basename(transcript_path)}", 5000)
        self._refresh_list()

    def _on_transcribe_error(self, error):
        self._busy_paths.discard(self._transcribe_path)
        self._status_bar.showMessage("Transcription failed")
        QMessageBox.warning(self, "Transcription Error", f"Soniox transcription failed:\n{error}")

    def _cleanup_recording(self, path):
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
                self, "Already Processed",
                "A summary file already exists. Overwrite?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._cleanup_thread = QThread()
        worker = CleanupWorker(
            transcript_path, self.config.get("summary_prompt", DEFAULT_SUMMARY_PROMPT))
        worker.moveToThread(self._cleanup_thread)
        self._cleanup_thread.started.connect(worker.run)
        worker.progress.connect(lambda msg: self._status_bar.showMessage(msg))
        worker.completed.connect(self._on_cleanup_finished)
        worker.error.connect(self._on_cleanup_error)
        worker.completed.connect(self._cleanup_thread.quit)
        worker.error.connect(self._cleanup_thread.quit)
        self._cleanup_worker = worker
        self._cleanup_path = path
        self._busy_paths.add(path)
        self._cleanup_thread.start()

        self._cleanup_start_time = time.time()
        self._cleanup_timer = QTimer(self)
        self._cleanup_timer.timeout.connect(self._update_cleanup_status)
        self._cleanup_timer.start(1000)
        self._status_bar.showMessage("Running Claude cleanup... (0s)")

    def _update_cleanup_status(self):
        elapsed = int(time.time() - self._cleanup_start_time)
        self._status_bar.showMessage(f"Running Claude cleanup... ({elapsed}s)")

    def _on_cleanup_finished(self, path):
        self._busy_paths.discard(self._cleanup_path)
        self._cleanup_timer.stop()
        elapsed = int(time.time() - self._cleanup_start_time)
        self._status_bar.showMessage(f"Cleanup done in {elapsed}s: {os.path.basename(path)}", 8000)

    def _on_cleanup_error(self, error):
        self._busy_paths.discard(self._cleanup_path)
        self._cleanup_timer.stop()
        self._status_bar.showMessage("Cleanup failed")
        QMessageBox.warning(self, "Cleanup Error", f"Claude cleanup failed:\n{error}")

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
            self._status_bar.showMessage(
                "Subtitles are off: no Soniox API key is configured", 8000)
            return

        try:
            # Both configured languages are offered as candidates for what is being
            # spoken, and the translation goes into the one you asked to read. A call
            # can turn out to be in either of them, and naming both is what stops the
            # model reaching for a third.
            self._subtitle_engine = SubtitleEngine(
                api_key=api_key,
                translate_to=self.config.get("translation_target"),
                source_languages=self._source_languages())
        except ValueError as e:
            self._subtitles.set_status(str(e))
            return

        target = self._subtitle_engine.target_language
        self._subtitles.set_status(
            f"Live subtitles, translated into {language_name(target)}" if target
            else "Live subtitles, not translated")
        self._subtitle_engine.line_finalized.connect(self._subtitles.append_line)
        self._subtitle_engine.line_updated.connect(self._subtitles.set_pending)
        self._subtitle_engine.notice.connect(
            lambda message: self._status_bar.showMessage(message, 8000))
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
        self._subtitles.setVisible(
            self._btn_rts.isChecked() or self._btn_live_speakers.isChecked())

    def _start_speaker_recognition(self, system_source_name):
        api_key = secrets.get_api_key()
        if not api_key:
            self._subtitles.set_status("Live speaker labels need a Soniox API key")
            self._status_bar.showMessage(
                "Speaker labels are off: no Soniox API key is configured", 8000)
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

        engines = [engine for engine in
                   (self._mic_speaker_engine, self._remote_speaker_engine)
                   if engine is not None]
        for engine in engines:
            engine.speaker_line_finalized.connect(self._on_speaker_line)
            engine.notice.connect(
                lambda message: self._status_bar.showMessage(message, 8000))
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
            self._subtitles.set_speaker_mapping_available(
                len(self._observed_remote_speakers))
        self._subtitles.append_speaker_line(
            timestamp, resolve_speaker(speaker_id, self._speaker_name_map), text)

    def _name_speakers(self):
        if not self._observed_remote_speakers:
            return
        dialog = SpeakerMappingDialog(
            self._observed_remote_speakers, self._speaker_name_map, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        for speaker_id in self._observed_remote_speakers:
            self._speaker_name_map.pop(speaker_id, None)
        self._speaker_name_map.update(dialog.mappings())
        self._status_bar.showMessage(
            "Speaker names will be used for new live lines and the complete sidecar", 6000)

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
                self, "Recording Active", "Stop recording before enabling dictation.")
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
            "Dictation ON - Ctrl+Space to dictate, Ctrl+Shift+Space to translate")

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
            self, "Add audio files",
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
                    self, "File Exists",
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
        )
        if dialog.exec():
            self.config = dialog.get_config()
            save_config(self.config)
            os.makedirs(self.config["output_dir"], exist_ok=True)
            self._refresh_list()
            self._sync_meeting_detection()
            self._status_bar.showMessage("Settings saved", 3000)

    def closeEvent(self, event):
        if (self._tray_icon is not None and self._tray_icon.isVisible()
                and event.spontaneous() and not self._quitting):
            self.hide()
            self._tray_icon.showMessage(
                "Meeting Recorder is still running",
                "Meeting detection remains active in the menu bar. Choose Quit there to exit.",
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )
            event.ignore()
            return
        if self._is_recording:
            reply = QMessageBox.question(
                self, "Recording in Progress",
                "A recording is in progress. Stop and exit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                self._quitting = False
                event.ignore()
                return
            self._stop_recording()
        if self._active_encoders or self._busy_paths:
            self._quitting = False
            self._status_bar.showMessage(
                "Finishing background work before exit. Close again when it is done.", 8000)
            event.ignore()
            return
        if self._dictation_active:
            self._stop_dictation()
        self._meeting_timer.stop()
        self._browser_meeting_bridge.stop()
        self._calendar_meeting_detector.stop()
        if self._tray_icon is not None:
            self._tray_icon.hide()
        # The filter is installed on the whole application, so it goes on being called
        # after this window is gone. Qt then calls it on a half-destroyed object and
        # aborts the process.
        QApplication.instance().removeEventFilter(self._hotkey_filter)
        event.accept()
