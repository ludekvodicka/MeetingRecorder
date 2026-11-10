import logging
import os
import shutil
import sys
import time
from collections import deque
from datetime import datetime

from PyQt6.QtCore import QEvent, QObject, QRectF, QSize, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QBrush, QDesktopServices, QGuiApplication, QPainter
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
from audiorecorder.api.commands import RemoteControl
from audiorecorder.api.server import ApiServer
from audiorecorder.audio.encoder import mix_and_encode
from audiorecorder.audio.output_activity import create_output_monitor
from audiorecorder.audio.output_follow import OutputFollower
from audiorecorder.audio.runtime import capture_runtime
from audiorecorder.audio.session import CaptureSession
from audiorecorder.config import CONFIG_DIR, DEFAULT_SUMMARY_PROMPT, save_config
from audiorecorder.dictation.engine import DictationEngine
from audiorecorder.dictation.hotkeys import HotkeyListener, HotkeyUnsupportedError
from audiorecorder.meetings.browser_agent import BrowserAgentDetector
from audiorecorder.meetings.calendar_detection import create_calendar_detector
from audiorecorder.meetings.call_end import CallEndWatch
from audiorecorder.meetings.call_log import CallLog
from audiorecorder.meetings.call_watch import CallWatch
from audiorecorder.meetings.detection import (
    CombinedMeetingDetector,
    MeetingPromptPolicy,
    create_meeting_detector,
)
from audiorecorder.meetings.microphone_use import create_microphone_monitor
from audiorecorder.readiness import capture_readiness
from audiorecorder.recordings import journal
from audiorecorder.recordings.manager import (
    RecordingManager,
    new_recording_path,
    remove_empty_folder,
)
from audiorecorder.recordings.naming import meeting_title
from audiorecorder.slides.capture import SlideCapture
from audiorecorder.slides.selector import SlideTuning
from audiorecorder.slides.store import SlideFolder, finalize_folder
from audiorecorder.slides.windows_screen import create_slide_source
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
from audiorecorder.ui.call_end_prompt import CallEndPrompt
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
# What the call watch saw change, so a call that was not recorded can be explained later.
CALL_LOG_PATH = CONFIG_DIR / "call-detection.log"

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
    _slide_status = pyqtSignal(str, int, str)

    def __init__(self, config, notifications=None):
        super().__init__()
        self.config = config
        self._capture = None
        self._recording_start_time = None
        self._is_recording = False
        # The subject of the meeting being recorded, which names its folder.
        self._meeting_title = ""
        # Notices the end of the meeting of a running recording; None while not recording.
        self._call_end = None
        self._call_end_prompt = None
        # Follows calls also between recordings, to start and stop recordings by itself.
        self._call_watch = CallWatch()
        self._microphone = create_microphone_monitor()
        self._call_log = CallLog(CALL_LOG_PATH)
        # Moves the system track to the device the call plays on; None while not recording
        # system audio or where the system source cannot switch.
        self._outputs = create_output_monitor()
        self._output_follower = None
        self._output_choice = None
        self._output_failed = set()
        # The live journal of the running recording and the text last written to it.
        self._journal_path = None
        self._journal_text = ""

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
        self._slide_source, self._slide_unavailable = create_slide_source()
        self._slide_folder = None
        self._slides = None
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
        self._slide_status.connect(self._on_slide_status, Qt.ConnectionType.QueuedConnection)
        self._lifecycle = LifecycleAdapter(self, notifications)
        self._capture_session = CaptureSession()
        self._capture_session.started.connect(self._capture_started)
        self._capture_session.stopped.connect(self._capture_stopped)
        self._capture_session.failed.connect(self._on_capture_error)
        self._capture_session.system_level.connect(self._level_bars.set_system_level)
        self._capture_session.mic_level.connect(self._level_bars.set_mic_level)
        self._capture_session.system_level.connect(self._output_system_level)
        self._capture_session.mic_level.connect(self._output_mic_level)
        self._capture_session.switched.connect(self._output_switched)
        self._create_tray_icon()
        self._migrate_recordings()
        self._refresh_list()
        self._recover_orphaned_captures()

        self._system_level_signal.connect(self._level_bars.set_system_level)
        self._mic_level_signal.connect(self._level_bars.set_mic_level)
        self._capture_error_signal.connect(self._on_capture_error)
        self._meeting_timer = QTimer(self)
        self._meeting_timer.setInterval(5000)
        self._meeting_timer.timeout.connect(self._poll_for_meeting)
        self._call_timer = QTimer(self)
        self._call_timer.setInterval(5000)
        self._call_timer.timeout.connect(self._watch_call)
        self._sync_meeting_detection()
        self._api = ApiServer(RemoteControl(self).handle)
        self._api.configure(self.config)

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
        self._sync_call_timer()

    def _automatic_call_recording(self):
        """Calls start recordings by themselves; needs to know who holds the microphone."""
        return self._microphone is not None and self.config.get("automatic_recording", False)

    def _sync_call_timer(self):
        if self._is_recording or self._automatic_call_recording():
            if not self._call_timer.isActive():
                self._call_timer.start()
        else:
            self._call_timer.stop()

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
        self._app_title = QLabel("Meeting Recorder")
        self._app_title.setObjectName("appTitle")
        brand.addWidget(self._app_title)
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
        self._btn_slides = self._live_toggle(
            "Slides", "presentation",
            "Every 5 seconds of a recording, look at the Google Meet or Teams window in "
            "Chrome, Brave or Edge and keep each new slide or shared screen, plus one view "
            "of the people when they change. Snapshots stay on this computer.")
        self._btn_slides.setProperty("toggle", "local")
        if self._slide_source is None:
            self._btn_slides.setEnabled(False)
            self._btn_slides.setToolTip(self._slide_unavailable)
        else:
            self._btn_slides.setChecked(bool(self.config.get("slide_snapshots")))
        self._btn_slides.clicked.connect(self._toggle_slides)
        for button in (self._btn_dictation, self._btn_rts, self._btn_live_speakers,
                       self._btn_slides):
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
        self._header = header

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
        self._recording_list.open_folder_requested.connect(self._open_folder)
        self._recording_list.copy_folder_name_requested.connect(self._copy_folder_name)
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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_header()

    def showEvent(self, event):
        super().showEvent(event)
        # A resize before the first show measures the header before it is styled.
        self._fit_header()

    def _fit_header(self):
        # Four toggles with their labels do not fit beside the title in a narrow side
        # window; the mark alone still names the app there.
        title = self._app_title
        needed = self._header.sizeHint().width()
        if not title.isVisible():
            needed += title.sizeHint().width() + 8
        title.setVisible(self.width() >= needed)

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
        for button in (self._btn_dictation, self._btn_rts, self._btn_live_speakers,
                       self._btn_slides):
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
        if self.start_recording() == "dictation":
            self._status_bar.showMessage("Stop dictation before recording", 5000)

    def start_recording(self):
        """Start a manual recording; None when it starts, else why not: dictation or busy."""
        if self._dictation_active:
            return "dictation"
        controller = self._lifecycle.controller
        controller.blocked = self._recording_start_blocked()
        started = controller.start(manual=True)
        self._lifecycle.process()
        return None if started else "busy"

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
        self._meeting_title = ""
        self._journal_text = ""
        stamp = self._capture.capture_stamp
        self._journal_path = None if stamp is None else journal.journal_path(
            self._recording_config[result.recording]["output_dir"], stamp)
        self._call_end = CallEndWatch()
        self._output_follower = (
            OutputFollower(time.monotonic())
            if system_name is not None and self._outputs is not None else None)
        self._output_choice = None
        self._output_failed = set()
        self._watch_call()
        self._call_timer.start()

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
        if self._slide_source is not None and self._capture.capture_stamp is not None:
            self._slide_folder = SlideFolder(
                self._recording_config[result.recording]["output_dir"],
                self._capture.capture_stamp)
            if self._btn_slides.isChecked():
                self._start_slides()
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
        self._call_end = None
        self._close_call_end_prompt()
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
        self._output_follower = None
        controller.stopped(result.recording, has_audio=bool(mic_path))
        self._watch_call()
        self._stop_speaker_recognition()
        slides = self._finish_slides()

        self._is_recording = False
        self._sync_call_timer()
        live_journal, self._journal_path = self._journal_path, None
        self._timer.stop()
        self._level_bars.stop()
        self._state_card.set_state("idle")
        self._timer_label.setText("00:00:00")
        self._update_live_view_visibility()
        # system_path is None on a microphone-only recording, which the encoder handles.
        if mic_path:
            capture_config = self._recording_config.pop(result.recording)
            output_path = new_recording_path(
                capture_config["output_dir"],
                datetime.fromtimestamp(self._recording_start_time), self._meeting_title)
            pending = {
                "subtitles": self._finish_subtitles(),
                "speakers": self._speaker_sidecar_payload(),
                "slides": slides.path if slides is not None else None,
                "journal": journal.journal_for_capture(mic_path),
            }
            # The final state, for a crash before the encoder publishes the M4A.
            self._save_journal(
                pending["journal"], self._meeting_title, pending["subtitles"],
                pending["speakers"])
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
            if slides is not None:
                slides.discard()
            if live_journal is not None:
                journal.remove(live_journal)
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
        for system_path, mic_path, slides_path, started in self._manager.orphaned_captures():
            live_journal = journal.journal_for_capture(mic_path)
            title, subtitles, speakers = journal.load(live_journal)
            output_path = new_recording_path(self.config["output_dir"], started, title)
            self._pending_sidecars[output_path] = {
                "subtitles": subtitles,
                "speakers": speakers,
                "slides": slides_path,
                "journal": live_journal,
            }
            self._enqueue_encoder(
                system_path, mic_path, output_path, self.config,
                self._on_recovery_finished,
                lambda error, s=system_path, m=mic_path, o=output_path:
                    self._on_recovery_error(error, s, m, o),
            )
        self._start_next_encoder()

    def _on_recovery_finished(self, path):
        self._write_pending_sidecars(path)
        self._status_bar.showMessage(
            f"Recovered an interrupted recording: {os.path.basename(path)}", 8000)
        self._refresh_list()
        self._transcribe_when_saved(path)

    def _transcribe_when_saved(self, path):
        if self.config.get("auto_transcribe", False):
            self._transcribe_recording(path, automatic=True)

    def _on_recovery_error(self, error, system_path, mic_path, output_path):
        # Slide snapshots and the live journal stay under their temporary names beside the
        # retained audio.
        self._pending_sidecars.pop(output_path, None)
        remove_empty_folder(output_path)
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
        self._transcribe_when_saved(path)
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
        remove_empty_folder(path)
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

    def _migrate_recordings(self):
        """Give each recording saved before recordings had folders its own folder."""
        failed = self._manager.migrate()
        if failed:
            self._status_bar.showMessage(
                f"{len(failed)} older recording(s) could not move into a folder; "
                "they stay where they are", 10000)

    def _watch_call(self):
        """The call: its subject for the folder name, its start and its end.

        A calendar event is no evidence that the call still runs, so it names the folder but
        does not keep the call alive. Only exact evidence, who holds the microphone, starts
        or stops a recording by itself; a meeting window alone only asks.
        """
        candidates = list(self._lifecycle.candidates.values())
        window_title = ""
        if self._slide_source is not None:
            try:
                window_title = self._slide_source.meeting_title()
            except OSError:
                log.exception("the meeting window title could not be read")
        if not self._meeting_title:
            for raw in [candidate.title for candidate in candidates] + [window_title]:
                title = meeting_title(raw)
                if title:
                    self._meeting_title = title
                    break
        live = [candidate for candidate in candidates if candidate.source != "calendar"]
        meeting_shown = bool(window_title) or (
            bool(live) and self.config.get("meeting_detection", False))
        microphone = self._microphone_use()
        exact = microphone is not None
        now = time.monotonic()
        present = self._call_watch.observe(microphone, meeting_shown, self._is_recording, now)
        call_audio = self._follow_output(microphone, now)
        if self._call_end is not None:
            if self._journal_path is not None:
                self._save_journal(
                    self._journal_path, self._meeting_title, self._live_subtitle_payload(),
                    self._live_speaker_payload())
            event = self._call_end.observe(present, now)
            if event == "ask" and exact and self.config.get("auto_stop_recording", False):
                action = "stop"
                self._status_bar.showMessage("The call ended; saving the recording", 10000)
                self._stop_recording()
            elif event == "ask":
                action = "ask"
                self._ask_call_end()
            elif event == "back":
                action = ""
                self._close_call_end_prompt()
            elif event == "":
                action = ""
            else:
                raise ValueError(f"unknown call end event: {event}")
        elif self._automatic_call_recording() and self._call_watch.should_start(present, exact):
            action = "start" if self._start_call_recording() else "start refused"
        else:
            action = ""
        self._call_log.record(present, microphone, meeting_shown, action, call_audio)

    def _follow_output(self, microphone, now):
        """Move the system track to the device the call plays on; the devices it plays on."""
        follower = self._output_follower
        if follower is None or self._capture is None:
            return ""
        try:
            outputs = self._outputs.outputs()
        except OSError:
            log.exception("the output devices could not be read")
            return ""
        programs = frozenset() if microphone is None else microphone.call_programs
        if self._output_choice is None:
            choice = follower.choose(self._capture.active_system_source, outputs, programs, now)
            if choice is not None and choice.name not in self._output_failed:
                self._output_choice = choice
                self._capture_session.switch_system(
                    self._lifecycle.controller.recording, choice.name)
        return ",".join(sorted({o.name for o in outputs if programs and o.plays(programs)}))

    def _output_system_level(self, level):
        if self._output_follower is not None:
            self._output_follower.system_level(level, time.monotonic())

    def _output_mic_level(self, level):
        if self._output_follower is not None:
            self._output_follower.mic_level(level, time.monotonic())

    def _output_switched(self, recording, name, error):
        choice, self._output_choice = self._output_choice, None
        if self._capture is None or recording != self._lifecycle.controller.recording:
            return
        why = "the call plays there" if choice and choice.reason == "call" else (
            "the recorded device was silent while you spoke")
        if error:
            self._output_failed.add(name)
            self._call_log.note(f"system audio stays, {name} could not open: {error}")
            self._status_bar.showMessage(
                f"System audio could not switch to {name}: {error}", 15000)
            return
        self._call_log.note(f"system audio now from {name}, because {why}")
        self._level_bars.set_device_names(name, self._capture.active_mic_source)
        self._status_bar.showMessage(f"System audio now from {name}: {why}", 15000)

    def _microphone_use(self):
        """Who holds the microphone, or None when this system cannot tell."""
        if self._microphone is None:
            return None
        try:
            return self._microphone.use()
        except OSError:
            log.exception("the microphone records could not be read")
            return None

    def _start_call_recording(self):
        """Start for a call; False when dictation or another operation holds the recorder."""
        if self.start_recording() is not None:
            return False
        self._call_watch.started()
        if self._meeting_prompt is not None:
            self._meeting_prompt.close()
        self._status_bar.showMessage("A call started; recording it", 10000)
        return True

    def _ask_call_end(self):
        if self._call_end_prompt is not None:
            return
        prompt = CallEndPrompt(None if sys.platform == "darwin" else self)
        self._call_end_prompt = prompt
        prompt.accepted.connect(self._stop_recording)
        prompt.finished.connect(lambda _result: self._forget_call_end_prompt(prompt))
        prompt.show()
        prompt.raise_()
        self._status_bar.showMessage("The call seems to have ended", 10000)

    def _forget_call_end_prompt(self, prompt):
        if self._call_end_prompt is prompt:
            self._call_end_prompt = None
        prompt.deleteLater()

    def _close_call_end_prompt(self):
        prompt, self._call_end_prompt = self._call_end_prompt, None
        if prompt is not None:
            prompt.close()

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

    def _open_folder(self, folder):
        if not os.path.isdir(folder):
            self._status_bar.showMessage("Recording was moved or deleted", 5000)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _copy_folder_name(self, folder):
        name = os.path.basename(folder)
        QGuiApplication.clipboard().setText(name)
        self._status_bar.showMessage(f"Copied: {name}", 3000)

    def recording_busy(self, path):
        """The kind of job queued or running for path ("transcribe", "cleanup"), else None."""
        return self._busy_kinds.get(path) if path in self._busy_paths else None

    def recording_failure(self, path):
        """(kind, error) of the last failed job of path, until another job starts."""
        return self._recording_list.failure(path)

    def _release_playback(self, path):
        if self._player.current_path == path:
            self._player.stop_playback()
            self._recording_list.clear_playing()

    def move_recording(self, path, destination):
        """Move the recording with its files into destination; the new audio path."""
        self._release_playback(path)
        moved = self._manager.move(path, destination)
        self._refresh_list()
        return moved

    def rename_recording(self, path, name):
        """Rename the recording, its files and its folder; the new audio path."""
        self._release_playback(path)
        renamed = self._manager.rename(path, name)
        self._refresh_list()
        return renamed

    def delete_recording(self, path):
        self._release_playback(path)
        self._manager.delete(path)
        self._refresh_list()

    def _move_recording(self, path):
        if path in self._busy_paths:
            QMessageBox.warning(
                self, "Busy", "This recording is being processed. Wait until it finishes."
            )
            return

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
            self.move_recording(path, dest_dir)
            self._status_bar.showMessage(f"Moved to {dest_dir}", 5000)
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
            f"Delete '{name}' with its transcript, other side files and slides? "
            "Its folder is removed too when nothing else is in it.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.delete_recording(path)
            except Exception as error:
                QMessageBox.warning(self, "Delete Error", f"Failed to delete recording:\n{error}")

    def _transcribe_recording(self, path, automatic=False):
        """Queue a transcription; an automatic one reports problems in the status bar only."""
        problem = self.queue_transcription(path)
        if problem in (None, "closing") or (problem == "busy" and automatic):
            return
        if problem == "busy":
            QMessageBox.warning(self, "Busy", "This recording is already being processed.")
        elif problem == "no-api-key" and automatic:
            self._status_bar.showMessage(
                "Not transcribed automatically: no Soniox API key is configured", 10000)
        elif problem == "no-api-key":
            QMessageBox.warning(
                self,
                "API Key Missing",
                "Soniox API key not configured.\nEnter it in Settings, or set the "
                "SONIOX_API_KEY environment variable.",
            )
        else:
            raise ValueError(f"unknown transcription problem: {problem}")

    def queue_transcription(self, path):
        """Queue a transcription; None when queued, else why not: closing, busy, no-api-key."""
        if self._quitting:
            return "closing"
        if path in self._busy_paths:
            return "busy"
        api_key = secrets.get_api_key()
        if not api_key:
            return "no-api-key"
        worker = SonioxWorker(
            api_key=api_key, audio_path=path,
            options=TranscriptionOptions.from_config(self.config),
        )
        self._busy_paths.add(path)
        self._busy_kinds[path] = "transcribe"
        self._processing.enqueue("transcribe", path, worker)
        return None

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
        problem = self.queue_summary(path)
        if problem == "summary-exists" and QMessageBox.question(
            self,
            "Already Processed",
            "A summary file already exists. Overwrite?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes:
            problem = self.queue_summary(path, overwrite=True)
        if problem in (None, "closing", "summary-exists"):
            return
        if problem == "busy":
            QMessageBox.warning(self, "Busy", "This recording is already being processed.")
        elif problem == "no-transcript":
            QMessageBox.warning(self, "No Transcript", "Transcribe the recording first.")
        else:
            raise ValueError(f"unknown summary problem: {problem}")

    def queue_summary(self, path, overwrite=False):
        """Queue the cleanup and summary of the transcript; None when queued, else why not:
        closing, busy, no-transcript or summary-exists (only without overwrite)."""
        if self._quitting:
            return "closing"
        if path in self._busy_paths:
            return "busy"
        transcript_path = os.path.splitext(path)[0] + ".md"
        if not os.path.exists(transcript_path):
            return "no-transcript"
        if not overwrite and os.path.exists(os.path.splitext(path)[0] + ".summary.md"):
            return "summary-exists"
        worker = CleanupWorker(
            transcript_path, self.config.get("summary_prompt", DEFAULT_SUMMARY_PROMPT)
        )
        self._busy_paths.add(path)
        self._busy_kinds[path] = "cleanup"
        self._processing.enqueue("cleanup", path, worker)
        return None

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

    def _toggle_slides(self):
        wanted = self._btn_slides.isChecked()
        self.config["slide_snapshots"] = wanted
        save_config(self.config)
        if wanted and self._is_recording:
            self._start_slides()
        elif wanted:
            self._status_bar.showMessage("Slide snapshots start with the next recording", 5000)
        elif self._slides is not None:
            self._stop_slides()
            self._state_card.set_slides("Slides: off, saved snapshots are kept")

    def _start_slides(self):
        if self._slides is not None or self._slide_folder is None:
            return
        self._slides = SlideCapture(
            self._slide_source, self._slide_folder, self._recording_start_time,
            self._slide_status.emit, SlideTuning.from_config(self.config.get("slide_tuning")))
        self._slides.start()

    def _stop_slides(self):
        if self._slides is not None:
            self._slides.stop()
            self._slides = None

    def _finish_slides(self):
        """Stop slide capture of the ended recording; its folder, or None when it never ran."""
        self._stop_slides()
        folder, self._slide_folder = self._slide_folder, None
        self._state_card.set_slides("")
        return folder

    def _on_slide_status(self, state, count, reason):
        if self._slides is None:
            return
        saved = f", {count} saved" if count else ""
        if state == "waiting":
            self._state_card.set_slides(f"Slides: waiting for a Meet or Teams window{saved}")
        elif state == "capturing":
            self._state_card.set_slides(f"Slides: watching the meeting{saved}")
        elif state == "stopped":
            self._state_card.set_slides(f"Slides stopped: {reason}")
            self._status_bar.showMessage(f"Slide capture stopped: {reason}", 8000)
        else:
            raise ValueError(f"unknown slide capture state: {state}")

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

    def _live_speaker_payload(self):
        """The speaker sidecar payload so far, including the engines still running."""
        timed = list(self._speaker_segments)
        for engine in (self._mic_speaker_engine, self._remote_speaker_engine):
            if engine is not None:
                timed.extend(engine.timed_speaker_transcript())
        if not timed:
            return None
        timed.sort(key=lambda line: line[0])
        return [line[1:] for line in timed], dict(self._speaker_name_map)

    def _live_subtitle_payload(self):
        if self._subtitle_engine is None:
            return None
        lines = self._subtitle_engine.transcript()
        return (lines, self._subtitle_engine.target_language) if lines else None

    def _save_journal(self, path, title, subtitles, speakers):
        """Write the live journal when it changed; a failure costs only the crash copy."""
        text = journal.encode(title, subtitles, speakers)
        if text == self._journal_text:
            return
        try:
            journal.save(path, text)
        except OSError:
            log.exception("the live journal could not be written")
            return
        self._journal_text = text

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
        slides = pending.get("slides")
        if slides is not None:
            try:
                path = finalize_folder(slides, recording_path)
                if path is not None:
                    written.append(path)
            except OSError:
                log.exception("the slide snapshots could not be placed beside the recording")
        live_journal = pending.get("journal")
        if live_journal is not None:
            journal.remove(live_journal)
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
        problem = self.start_dictation()
        if problem is not None:
            QMessageBox.warning(self, *problem)

    def start_dictation(self):
        """Turn dictation on; None when it is on, else (title, message) of the reason."""
        if self._dictation_active:
            return None
        if self._is_recording:
            self._btn_dictation.setChecked(False)
            return "Recording Active", "Stop recording before enabling dictation."

        api_key = secrets.get_api_key()
        if not api_key:
            self._btn_dictation.setChecked(False)
            return "API Key Missing", "Soniox API key not configured."

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
            self._btn_dictation.setChecked(False)
            self._btn_dictation.setEnabled(False)
            self._btn_dictation.setToolTip(str(e))
            return "Dictation Unavailable", str(e)
        except Exception as e:
            self._dictation = None
            self._btn_dictation.setChecked(False)
            return "Dictation Unavailable", str(e)

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
        return None

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
            try:
                self.import_audio(src)
            except FileExistsError:
                reply = QMessageBox.question(
                    self,
                    "File Exists",
                    f"'{os.path.basename(src)}' already exists. Overwrite?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    continue
                self.import_audio(src, overwrite=True)
            added += 1

        if added:
            self._status_bar.showMessage(f"Added {added} file(s)", 5000)

    def import_audio(self, source, overwrite=False):
        """Copy an audio file into its own folder in the output folder; the copy's path.

        An existing copy raises FileExistsError unless overwrite is set.
        """
        name = os.path.basename(source)
        folder = os.path.join(self.config["output_dir"], os.path.splitext(name)[0])
        dest = os.path.join(folder, name)
        if os.path.exists(dest) and not overwrite:
            raise FileExistsError(dest)
        os.makedirs(folder, exist_ok=True)
        shutil.copy2(source, dest)
        self._refresh_list()
        return dest

    def _open_settings(self):
        dialog = SettingsDialog(
            self.config,
            self,
            browser_agent=self._browser_agent,
            meeting_detector=self._native_meeting_detector,
            lifecycle=self._lifecycle,
            api=self._api,
        )
        if dialog.exec():
            self.config = dialog.get_config()
            save_config(self.config)
            if self._slides is not None:
                self._slides.set_tuning(SlideTuning.from_config(self.config.get("slide_tuning")))
            os.makedirs(self.config["output_dir"], exist_ok=True)
            self._refresh_list()
            self._sync_meeting_detection()
            self._api.configure(self.config)
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
        self._api.close()
        if self._tray_icon is not None:
            self._tray_icon.hide()
        QApplication.instance().removeEventFilter(self._hotkey_filter)
        self._exit_ready = True
        event.accept()
        QTimer.singleShot(0, QApplication.instance().quit)
