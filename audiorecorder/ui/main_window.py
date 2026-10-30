import logging
import os
import shutil
import sys
import time
from collections import deque

from PyQt6.QtCore import QEvent, QObject, QRectF, QSize, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QBrush, QDesktopServices, QPainter
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QSplitterHandle,
    QStatusBar,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from audiorecorder import secrets
from audiorecorder.audio.encoder import mix_and_encode
from audiorecorder.audio.runtime import capture_runtime
from audiorecorder.audio.session import CaptureSession
from audiorecorder.config import DEFAULT_SUMMARY_PROMPT, save_config
from audiorecorder.dictation.engine import DictationEngine
from audiorecorder.dictation.hotkeys import HotkeyListener, HotkeyUnsupportedError
from audiorecorder.meetings.browser_agent import BrowserAgentDetector
from audiorecorder.meetings.calendar_detection import create_calendar_detector
from audiorecorder.meetings.detection import (
    CombinedMeetingDetector,
    MeetingPromptPolicy,
    create_meeting_detector,
)
from audiorecorder.readiness import capture_readiness
from audiorecorder.recordings.manager import RecordingManager
from audiorecorder.subtitles.document import write_live_transcript
from audiorecorder.subtitles.engine import SubtitleEngine
from audiorecorder.subtitles.speaker_document import (
    resolve_speaker,
    write_speaker_sidecars,
)
from audiorecorder.transcription.cleanup import CleanupWorker
from audiorecorder.transcription.markdown import language_name
from audiorecorder.transcription.service import TranscriptionOptions
from audiorecorder.transcription.soniox import SonioxWorker
from audiorecorder.ui import icons, theme
from audiorecorder.ui.audio_player import AudioPlayerWidget
from audiorecorder.ui.lifecycle_adapter import LifecycleAdapter
from audiorecorder.ui.overlay import OverlayWidget
from audiorecorder.ui.processing import ProcessingQueue
from audiorecorder.ui.recording_list import RecordingListWidget
from audiorecorder.ui.settings_dialog import SettingsDialog
from audiorecorder.ui.speaker_mapping import SpeakerMappingDialog
from audiorecorder.ui.state_card import StateCard
from audiorecorder.ui.subtitles import SubtitleView
from audiorecorder.ui.widgets import Dot, ElidedLabel, IconLabel, Pulse, spaced
from audiorecorder.version import __version__

log = logging.getLogger(__name__)

AUTO_START_SECONDS = 5
AUTO_START_SOURCES = frozenset()  # Native heuristics await per-source qualification.
# Windows shows its "apps are preventing shutdown" screen after about five seconds.
SESSION_END_STOP_SECONDS = 4

# Speaker colours repeat after the third remote voice.
REMOTE_COLOURS = ("speaker.r1", "speaker.r2", "speaker.r3")


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


class AppMark(QWidget):
    """The red record mark beside the title."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(22, 22)
        theme.notifier.changed.connect(self.update)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(theme.color("rec.fill")))
        painter.drawRoundedRect(QRectF(0, 0, 22, 22), 6, 6)
        painter.setBrush(QBrush(theme.color("text.onFill")))
        painter.drawEllipse(QRectF(7, 7, 8, 8))


class CostStrip(QFrame):
    """A violet strip under the header whenever a paid live function is on."""

    def __init__(self):
        super().__init__()
        self.setObjectName("costStrip")
        self.setFixedHeight(32)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 0, 16, 0)
        row.setSpacing(8)
        self._icon = IconLabel("radio", "live", 15)
        self._lead = QLabel()
        self._lead.setObjectName("lead")
        self._rest = ElidedLabel()
        row.addWidget(self._icon)
        row.addWidget(self._lead)
        row.addWidget(self._rest, 1)
        self._pulse = Pulse(self._icon)

    def show_cost(self, lead, rest, billing):
        self._lead.setText(lead)
        self._rest.setText(rest)
        if billing:
            self._pulse.start()
        else:
            self._pulse.stop()
        self.setVisible(True)

    def text(self):
        return f"{self._lead.text()} {self._rest.text()}"


class GripHandle(QSplitterHandle):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(theme.color("border.default")))
        painter.drawRoundedRect(
            QRectF((self.width() - 36) / 2, (self.height() - 4) / 2, 36, 4), 2, 2)


class GripSplitter(QSplitter):
    def createHandle(self):
        handle = GripHandle(self.orientation(), self)
        handle.setCursor(Qt.CursorShape.SplitVCursor)
        return handle


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
    _browser_updated = pyqtSignal()

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
        self._exit_ready = False
        self._session_ending = False
        self._tray_icon = None
        self._last_capture_error = None
        self._recording_line = None
        self._live_shown = None
        self._busy_kinds = {}
        self._browser_agent = BrowserAgentDetector(on_updated=self._browser_updated.emit)
        self._browser_updated.connect(
            self._browser_detection_updated, Qt.ConnectionType.QueuedConnection,
        )
        self._calendar_meeting_detector = create_calendar_detector()
        self._native_meeting_detector = create_meeting_detector()
        self._meeting_detector = CombinedMeetingDetector(
            [
                self._native_meeting_detector,
                self._browser_agent,
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
        self.resize(720, 800)
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
        self._recover_orphaned_captures()

        self._system_level_signal.connect(self._level_bars.set_system_level)
        self._mic_level_signal.connect(self._level_bars.set_mic_level)
        self._capture_error_signal.connect(self._on_capture_error)
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

    def _sync_meeting_detection(self):
        if hasattr(self, "_lifecycle"):
            self._lifecycle.configure()
        if self.config.get("meeting_detection", False):
            self._browser_agent.start()
        else:
            self._browser_agent.stop()
        if self.config.get("meeting_detection", False) or self._lifecycle.native.testing:
            if self.config.get("calendar_detection", False):
                self._calendar_meeting_detector.start()
            else:
                self._calendar_meeting_detector.stop()
            self._meeting_timer.start()
        else:
            self._meeting_timer.stop()
            self._browser_agent.stop()
            self._calendar_meeting_detector.stop()
            if self._meeting_prompt is not None:
                self._meeting_prompt.close()
        self._update_browser_status()

    def _update_browser_status(self):
        status = self._browser_agent.status()
        self._browser_status_label.setText(status.summary)
        if not status.active or not status.scanned:
            tint = "text.tertiary"
        elif status.profiles and not status.errors and all(
                profile.connected for profile in status.profiles):
            tint = "success"
        else:
            tint = "warning"
        self._browser_dot.set_tint(tint)

    def _browser_detection_updated(self):
        if self.config.get("meeting_detection", False) and not self._quitting:
            self._poll_for_meeting()

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

    def busy_reason(self):
        """Include custom lifecycle and queued work before any update-related quit."""
        # closeEvent sets _exit_ready only after all work has stopped. aboutToQuit runs
        # after that, and a ready update must still install on that normal quit.
        if self._quitting and not self._exit_ready:
            return "the application is closing."
        if self._is_recording or self._lifecycle.controller.state != "idle":
            return "a recording is active or its lifecycle is changing."
        if self._active_encoders or self._encoder_queue:
            return "a recording is still being saved."
        if self._busy_paths or self._processing.is_running():
            return "a transcription or cleanup is queued or running."
        # Dictation stays on all day for some users; it holds no unsaved work, so it never blocks.
        return None

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # --- Header ---
        header = QFrame()
        header.setObjectName("header")
        header.setFixedHeight(56)
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(16, 0, 12, 0)
        header_row.setSpacing(12)
        brand = QHBoxLayout()
        brand.setSpacing(8)
        brand.addWidget(AppMark())
        title = QLabel("Meeting Recorder")
        title.setObjectName("appTitle")
        brand.addWidget(title)
        header_row.addLayout(brand)
        header_row.addStretch()

        toggles = QHBoxLayout()
        toggles.setSpacing(6)
        self._btn_dictation = self._live_toggle(
            "Dictation", "keyboard",
            "Dictation: Ctrl + Space types what you say into any app. "
            "Uses Soniox realtime minutes.")
        self._btn_dictation.clicked.connect(self._toggle_dictation)
        supported, reason = HotkeyListener.support()
        if not supported:
            self._btn_dictation.setEnabled(False)
            self._btn_dictation.setToolTip(reason)
        self._btn_rts = self._live_toggle(
            "Translate", "languages",
            "Live translated subtitles of the system audio while recording. "
            "Costs Soniox realtime minutes for the length of the call.")
        self._btn_rts.setChecked(bool(self.config.get("rts_translate")))
        self._btn_rts.clicked.connect(self._toggle_subtitles)
        self._btn_live_speakers = self._live_toggle(
            "Speakers", "users",
            "Label microphone speech as Me and diarize system audio as Remote 1, "
            "Remote 2, etc. Adds up to two Soniox realtime sessions while recording.")
        self._btn_live_speakers.setChecked(bool(self.config.get("live_speakers")))
        self._btn_live_speakers.clicked.connect(self._toggle_live_speakers)
        for button in (self._btn_dictation, self._btn_rts, self._btn_live_speakers):
            toggles.addWidget(button)
            button.toggled.connect(lambda _on: self._render_header_icons())
        header_row.addLayout(toggles)

        separator = QFrame()
        separator.setObjectName("vsep")
        separator.setFixedSize(1, 20)
        header_row.addWidget(separator)

        self._btn_add = self._quiet_button("file-plus", "Add audio files", self._add_files)
        self._btn_settings = self._quiet_button("settings", "Settings", self._open_settings)
        tools = QHBoxLayout()
        tools.setSpacing(2)
        tools.addWidget(self._btn_add)
        tools.addWidget(self._btn_settings)
        header_row.addLayout(tools)
        layout.addWidget(header)

        # --- What costs money right now ---
        self._cost_strip = CostStrip()
        layout.addWidget(self._cost_strip)

        # --- Recorder state ---
        self._state_card = StateCard()
        self._state_card.record_button.clicked.connect(self._toggle_recording)
        self._state_card.mute_button.clicked.connect(self._toggle_mute)
        self._state_card.details_button.clicked.connect(self._show_last_capture_error)
        self._btn_record = self._state_card.record_button
        self._btn_mute = self._state_card.mute_button
        self._timer_label = self._state_card.timer
        self._level_bars = self._state_card.meters
        card_holder = QVBoxLayout()
        card_holder.setContentsMargins(16, 12, 16, 0)
        card_holder.addWidget(self._state_card)
        layout.addLayout(card_holder)

        # --- Live panel above the library, only while it is wanted ---
        self._splitter = GripSplitter(Qt.Orientation.Vertical)
        self._splitter.setChildrenCollapsible(False)
        self._subtitles = SubtitleView()
        self._subtitles.speaker_names_requested.connect(self._name_speakers)
        self._subtitles.set_speaker_mapping_visible(self._btn_live_speakers.isChecked())
        self._live_holder = QWidget()
        live_layout = QVBoxLayout(self._live_holder)
        live_layout.setContentsMargins(16, 12, 16, 0)
        live_layout.addWidget(self._subtitles)
        self._splitter.addWidget(self._live_holder)

        # --- Recording list, with the player inside the row that plays ---
        self._recording_list = RecordingListWidget()
        self._recording_list.play_requested.connect(self._play_recording)
        self._recording_list.stop_requested.connect(self._stop_recording_playback)
        self._recording_list.transcribe_requested.connect(self._transcribe_recording)
        self._recording_list.cleanup_requested.connect(self._cleanup_recording)
        self._recording_list.move_requested.connect(self._move_recording)
        self._recording_list.delete_requested.connect(self._delete_recording)
        self._recording_list.open_transcript_requested.connect(self._open_transcript)
        self._recording_list.details_requested.connect(self._show_processing_failure)
        self._recording_list.add_requested.connect(self._add_files)
        self._library_holder = QWidget()
        library_layout = QVBoxLayout(self._library_holder)
        library_layout.setContentsMargins(0, 8, 0, 0)
        library_layout.addWidget(self._recording_list)
        self._splitter.addWidget(self._library_holder)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 0)
        layout.addWidget(self._splitter, stretch=1)

        self._player = AudioPlayerWidget()
        self._player.playback_stopped.connect(self._on_playback_stopped)
        self._recording_list.set_player(self._player)

        # --- Status bar ---
        self._status_bar = QStatusBar()
        self._status_bar.setSizeGripEnabled(False)
        self.setStatusBar(self._status_bar)
        browser = QWidget()
        browser_row = QHBoxLayout(browser)
        browser_row.setContentsMargins(0, 0, 8, 0)
        browser_row.setSpacing(6)
        self._browser_dot = Dot("text.tertiary", 6)
        self._browser_status_label = QLabel()
        browser.setToolTip(
            "Browser Agent checks enabled meeting tools every 15 seconds while detection is on."
        )
        browser_row.addWidget(self._browser_dot)
        browser_row.addWidget(self._browser_status_label)
        self._status_bar.addPermanentWidget(browser)

        # --- Timer ---
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_timer)

        theme.notifier.changed.connect(self._render_header_icons)
        self._render_header_icons()
        # Start with the focus on the window itself, not on the first header toggle.
        central.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        central.setFocus()
        self._update_live_view_visibility()
        self._update_cost_strip()

    def _live_toggle(self, text, icon, tooltip):
        button = QPushButton(spaced(text))
        button.setProperty("toggle", "live")
        button.setCheckable(True)
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        # A click must not leave a focus ring on the toggle; Tab still reaches it.
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        button.setProperty("glyph", icon)
        return button

    def _quiet_button(self, icon, tooltip, slot):
        button = QToolButton()
        button.setProperty("variant", "quiet")
        button.setProperty("glyph", icon)
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(slot)
        return button

    def _render_header_icons(self):
        for button in (self._btn_dictation, self._btn_rts, self._btn_live_speakers):
            tint = "text.onFill" if button.isChecked() else (
                "text.secondary" if button.isEnabled() else "text.disabled")
            button.setIcon(icons.icon(button.property("glyph"), theme.solid(tint), 16))
            button.setIconSize(QSize(16, 16))
        for button in (self._btn_add, self._btn_settings):
            button.setIcon(icons.icon(button.property("glyph"), theme.solid("text.secondary"), 18))
            button.setIconSize(QSize(18, 18))

    def _update_cost_strip(self):
        """Say what costs Soniox realtime minutes: running now, or armed for the next call."""
        if self._dictation_active:
            self._cost_strip.show_cost(
                "Dictation on", "Each dictation uses Soniox realtime minutes", billing=True)
            return
        translate = self._subtitle_engine is not None and self._btn_rts.isChecked()
        speakers = [e for e in (self._mic_speaker_engine, self._remote_speaker_engine)
                    if e is not None]
        if self._is_recording and (translate or speakers):
            parts = []
            if translate:
                target = self._subtitle_engine.target_language
                parts.append(
                    f"Translate, {self._source_name()} → {language_name(target)}"
                    if target else "Subtitles, not translated")
            if speakers:
                parts.append("Speakers")
            sessions = int(translate) + len(speakers)
            unit = "session" if sessions == 1 else "sessions"
            self._cost_strip.show_cost(
                "Live, billing now", " and ".join(parts) + f" · {sessions} realtime {unit}",
                billing=True)
            return
        armed = [name for name, button in (("Translate", self._btn_rts),
                                           ("Speakers", self._btn_live_speakers))
                 if button.isChecked()]
        if armed and not self._is_recording:
            self._cost_strip.show_cost(
                "Armed for next recording",
                " and ".join(armed) + " will use Soniox realtime minutes", billing=False)
            return
        self._cost_strip.setVisible(False)

    def armed_cost_note(self):
        """For a meeting prompt: which live functions a recording would start."""
        armed = [name for name, button in (("Translate", self._btn_rts),
                                           ("Speakers", self._btn_live_speakers))
                 if button.isChecked()]
        if not armed:
            return None
        verb = "is" if len(armed) == 1 else "are"
        return f"{' and '.join(armed)} {verb} on and will use realtime minutes"

    def _source_name(self):
        language = self.config.get("language")
        return "Auto" if language in (None, "auto") else language_name(language)

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
        self._state_card.set_state("starting")
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
            self._last_capture_error = ("Recording could not start", result.error)
            if result.unresolved:
                self._state_card.set_state("failed", failure=(
                    f"Capture could not start cleanly: {result.error}",
                    "Retry stop to release the audio devices before you record again.",
                ))
            else:
                self._state_card.set_state("idle")
            self._status_bar.showMessage(f"Capture failed: {result.error}")
            self._lifecycle.notify("error", "Recording could not start", body=result.error)
            self._show_recording_error("Recording could not start", result.error)
            if not result.unresolved:
                self._recording_config.pop(result.recording, None)
            self._lifecycle.process()
            self._finish_pending_quit()
            return
        self._capture = result.backend
        system_name = self._capture.active_system_source
        self._recording_line = (
            "Recording microphone only" if system_name is None
            else "Recording system audio and microphone")
        self._state_card.set_state("recording", recording_line=self._recording_line)
        self._level_bars.set_device_names(
            system_name or "not recorded",
            self._capture.active_mic_source,
        )
        self._level_bars.start()

        self._is_recording = True
        self._recording_start_time = time.time()
        self._timer.start(200)

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
        self._update_cost_strip()
        self._update_live_view_visibility()
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
        self._state_card.set_state("recording", muted=muted)
        self._status_bar.showMessage("Microphone muted" if muted else "Recording...")

    def _stop_recording(self):
        self._lifecycle.controller.stop()
        self._lifecycle.process()

    def _retry_capture_stop(self):
        self._lifecycle.controller.retry_stop()
        self._lifecycle.process()

    def _begin_stop(self, recording):
        self._state_card.set_state("stopping")
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
            self._last_capture_error = (
                "Capture shutdown is unresolved. Use Retry stop before starting again.",
                result.error,
            )
            self._state_card.set_state("failed", failure=(
                f"Capture shutdown unresolved: {result.error}",
                "The audio recorded so far is kept on disk. Retry stop to finish saving, "
                "or open Details for the recovery options.",
            ))
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
        self._stop_speaker_recognition()

        self._is_recording = False
        self._timer.stop()
        self._level_bars.stop()
        self._state_card.set_state("idle")
        self._timer_label.setText("00:00:00")
        self._update_live_view_visibility()
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

            worker = self._enqueue_encoder(
                system_path, mic_path, output_path, capture_config,
                self._on_encode_finished,
                lambda error, path=output_path: self._on_encode_error(error, path),
            )
            self._encoding_ids[output_path] = (result.recording, worker)
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

    def _enqueue_encoder(self, system_path, mic_path, output_path, config, finished, error):
        thread = QThread()
        worker = EncoderWorker(
            system_path,
            mic_path,
            output_path,
            config.get("system_volume", 1.0),
            config.get("mic_volume", 1.0),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(finished)
        worker.error.connect(error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        entry = (thread, worker)
        self._encoder_queue.append(entry)
        thread.finished.connect(lambda e=entry: self._cleanup_encoder(e))
        return worker

    def _recover_orphaned_captures(self):
        """Encode capture files that a logged-off or crashed session left behind."""
        for system_path, mic_path, last_write in self._manager.orphaned_captures():
            output_path = os.path.join(
                self.config["output_dir"], self._manager.generate_filename(last_write))
            self._enqueue_encoder(
                system_path, mic_path, output_path, self.config,
                self._on_recovery_finished,
                lambda error, s=system_path, m=mic_path: self._on_recovery_error(error, s, m),
            )
        self._start_next_encoder()

    def _on_recovery_finished(self, path):
        self._status_bar.showMessage(
            f"Recovered an interrupted recording: {os.path.basename(path)}", 8000)
        self._refresh_list()

    def _on_recovery_error(self, error, system_path, mic_path):
        kept = self._manager.set_aside_capture(system_path, mic_path)
        self._status_bar.showMessage("An interrupted recording could not be recovered")
        self._show_recording_error(
            "An interrupted recording could not be recovered. Its audio was kept.",
            f"{error}\nRetained audio: {', '.join(kept)}",
        )

    def end_session(self):
        """The operating system session is ending: close the capture files while we can.

        Windows may kill the process before the encoder finishes. The finished WAV files
        stay in the output directory and the next start encodes them.
        """
        self._session_ending = True
        if self._is_recording:
            self._stop_recording()
        self._capture_session.wait_idle(SESSION_END_STOP_SECONDS)

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
        sidecars = self._write_pending_sidecars(path)
        suffix = f" with {len(sidecars)} live transcript files" if sidecars else ""
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

    def _show_last_capture_error(self):
        if self._last_capture_error is not None:
            self._show_recording_error(*self._last_capture_error)

    def _show_processing_failure(self, path):
        failure = self._recording_list.failure(path)
        if failure is None:
            return
        kind, error = failure
        title = "Transcription Error" if kind == "transcribe" else "Cleanup Error"
        dialog = QMessageBox(QMessageBox.Icon.Warning, title, str(error), parent=self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._error_dialogs.append(dialog)
        dialog.destroyed.connect(lambda: self._error_dialogs.remove(dialog))
        dialog.show()

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
        name = os.path.splitext(os.path.basename(path))[0]
        self._status_bar.showMessage(f"Playing: {name}")

    def _stop_recording_playback(self, path):
        self._player.stop_playback()
        self._recording_list.clear_playing()
        self._status_bar.clearMessage()

    def _on_playback_stopped(self):
        self._recording_list.clear_playing()
        self._status_bar.clearMessage()

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
            self._manager.move(path, dest_dir)

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
            try:
                self._manager.delete(path)
            except Exception as error:
                QMessageBox.warning(self, "Delete Error", f"Failed to delete recording:\n{error}")
                return
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

        worker = SonioxWorker(
            api_key=api_key, audio_path=path,
            options=TranscriptionOptions.from_config(self.config),
        )
        self._busy_paths.add(path)
        self._busy_kinds[path] = "transcribe"
        self._processing.enqueue("transcribe", path, worker)

    def _on_processing_progress(self, path, message):
        self._recording_list.set_processing(
            path, message, busy=True, kind=self._busy_kinds.get(path))
        self._status_bar.showMessage(f"{os.path.basename(path)}: {message}")

    def _on_processing_completed(self, kind, path, output):
        self._busy_paths.discard(path)
        self._busy_kinds.pop(path, None)
        self._recording_list.set_processing(path, "", busy=False)
        self._refresh_list()
        self._status_bar.showMessage(f"Saved: {os.path.basename(output)}", 8000)

    def _on_processing_failed(self, kind, path, error):
        self._busy_paths.discard(path)
        self._busy_kinds.pop(path, None)
        self._recording_list.set_failure(path, kind, error)
        self._refresh_list()
        # The row shows Failed with See details; the dialog opens once, non-modal, so one
        # failed queued job cannot stall other jobs or shutdown.
        self._status_bar.showMessage(f"Processing failed: {os.path.basename(path)}")
        self._show_processing_failure(path)

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
        self._busy_kinds[path] = "cleanup"
        self._processing.enqueue("cleanup", path, worker)

    def _toggle_subtitles(self):
        wanted = self._btn_rts.isChecked()
        self.config["rts_translate"] = wanted
        save_config(self.config)
        self._update_live_view_visibility()
        self._update_cost_strip()
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
        self._subtitles.set_mode(
            "Live subtitles",
            f"{self._source_name()} → {language_name(target)}" if target else "Not translated",
            "captions",
        )
        self._subtitle_engine.line_finalized.connect(self._subtitles.append_line)
        self._subtitle_engine.line_updated.connect(self._subtitles.set_pending)
        self._subtitle_engine.notice.connect(
            lambda message: self._status_bar.showMessage(message, 8000)
        )
        self._subtitle_engine.error_occurred.connect(self._on_subtitle_error)
        self._subtitle_engine.start()
        self._refresh_audio_taps()
        self._update_cost_strip()

    def _toggle_live_speakers(self):
        wanted = self._btn_live_speakers.isChecked()
        self.config["live_speakers"] = wanted
        save_config(self.config)
        self._update_live_view_visibility()
        self._update_cost_strip()
        self._subtitles.set_speaker_mapping_visible(wanted)
        if wanted:
            self._status_bar.showMessage("Speaker labels start with the next recording", 5000)
        elif self._mic_speaker_engine is not None or self._remote_speaker_engine is not None:
            self._stop_speaker_recognition()
            self._status_bar.showMessage("Live speaker recognition stopped", 5000)

    def _update_live_view_visibility(self):
        """The live panel shares the height with the library while a live function is on."""
        wanted = self._btn_rts.isChecked() or self._btn_live_speakers.isChecked()
        visible = wanted and (self._is_recording or self._subtitles.has_lines())
        if visible == self._live_shown:
            return
        self._live_shown = visible
        self._live_holder.setVisible(visible)
        self._library_holder.layout().setContentsMargins(0, 0 if visible else 8, 0, 0)
        if visible:
            # After the layout has run, so the splitter knows its height.
            QTimer.singleShot(0, self._size_live_panel)

    def _size_live_panel(self):
        library = 118 if self.height() < 700 else 196
        self._splitter.setSizes([max(1, self._splitter.height() - library), library])

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
            self._subtitles.set_mode("Live subtitles and speakers", None, "captions")
        elif system_source_name is None:
            self._subtitles.set_mode("Live speakers", "Microphone only", "users")
        else:
            self._subtitles.set_mode("Live speakers", "Me · Remote", "users")
        self._refresh_audio_taps()
        self._update_cost_strip()

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
        self._update_cost_strip()

    def _on_speaker_line(self, timestamp, speaker_id, text):
        if speaker_id != "Me":
            self._observed_remote_speakers.add(speaker_id)
            self._subtitles.set_speaker_mapping_available(len(self._observed_remote_speakers))
        self._subtitles.append_speaker_line(
            timestamp, resolve_speaker(speaker_id, self._speaker_name_map), text,
            self._speaker_colour(speaker_id),
        )
        self._update_speaker_detail()

    @staticmethod
    def _speaker_colour(speaker_id):
        if speaker_id == "Me":
            return "speaker.me"
        number = "".join(ch for ch in speaker_id if ch.isdigit())
        index = (int(number) - 1) if number else 0
        return REMOTE_COLOURS[index % len(REMOTE_COLOURS)]

    def _update_speaker_detail(self):
        if self._btn_rts.isChecked() or self._remote_speaker_engine is None:
            return
        remotes = sorted(self._observed_remote_speakers, key=lambda label: (len(label), label))
        names = ["Me"] + [resolve_speaker(label, self._speaker_name_map) for label in remotes]
        self._subtitles.set_mode("Live speakers", " · ".join(names), "users")

    def _name_speakers(self):
        if not self._observed_remote_speakers:
            return
        dialog = SpeakerMappingDialog(self._observed_remote_speakers, self._speaker_name_map, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        for speaker_id in self._observed_remote_speakers:
            self._speaker_name_map.pop(speaker_id, None)
        self._speaker_name_map.update(dialog.mappings())
        self._update_speaker_detail()
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
        self._update_cost_strip()
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
        self._state_card.set_state("dictation")
        self._level_bars.start()
        self._level_bars.set_device_names("", self.config.get("mic_source_name") or "Default")
        self._update_cost_strip()
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
        self._level_bars.stop()
        self._state_card.set_state("idle")
        self._update_cost_strip()
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
            self._state_card.set_preview("")
            if self._dictation_active:
                self._status_bar.showMessage("Dictation ON - Ctrl+Space to dictate")
        elif state.startswith("notice:"):
            self._overlay.set_state("idle")
            self._state_card.set_preview("")
            self._status_bar.showMessage(state[7:], 5000)
        elif state.startswith("preview:"):
            self._state_card.set_preview(state[8:])
            preview = state[8:]
            if len(preview) > 60:
                preview = "..." + preview[-57:]
            self._status_bar.showMessage(f"Live: {preview}")

    def _on_dictation_pasted(self, text):
        self._state_card.set_preview("")
        self._state_card.set_pasted(text)
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
            browser_agent=self._browser_agent,
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
        if (
            not self._quitting
            and not self._session_ending
            and self._is_recording
            and QMessageBox.question(
                self, "Recording in Progress",
                "A recording is in progress. Stop, save it and exit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes
        ):
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
        self._browser_agent.stop()
        self._calendar_meeting_detector.stop()
        self._lifecycle.close()
        self._capture_session.close()
        if self._tray_icon is not None:
            self._tray_icon.hide()
        QApplication.instance().removeEventFilter(self._hotkey_filter)
        self._exit_ready = True
        event.accept()
        QTimer.singleShot(0, QApplication.instance().quit)
