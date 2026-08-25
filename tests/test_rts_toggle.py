"""The RTS Translate toggle decides whether a paid realtime session exists at all.

Soniox bills the realtime API for the audio streamed to it, so "off" has to mean no
session and no audio leaving the capture tap, not merely a hidden subtitle area. These
tests drive the real window against a fake capture backend and a fake engine, and assert
on the two things that cost money: whether a session was opened, and whether the tap was
handed anything to send.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from audiorecorder.meetings.detection import (  # noqa: E402
    MeetingCandidate,
    MeetingPromptPolicy,
)
from audiorecorder.ui import main_window as mw  # noqa: E402
from audiorecorder.ui.main_window import ACTIVE_TOGGLE_STYLE, MainWindow  # noqa: E402


class FakeCapture:
    def __init__(self):
        self.tap = None
        self.mic_tap = None
        self.started = False
        self.stopped = False
        self.error_callback = None

    def set_level_callbacks(self, system, mic):
        pass

    def set_error_callback(self, callback):
        self.error_callback = callback

    def set_system_tap(self, callback):
        self.tap = callback

    def set_mic_tap(self, callback):
        self.mic_tap = callback

    def start(self, tmp_dir, system_source, mic_source):
        self.started = True

    def stop(self):
        self.stopped = True
        return None, None

    def close(self):
        pass

    active_system_source = "Speakers"
    active_mic_source = "Microphone"


class FakeDictation:
    """The real one installs global keyboard hooks, which a test must not leave behind."""

    def __init__(self, **kwargs):
        pass

    class _Signal:
        def connect(self, slot):
            pass

    state_changed = _Signal()
    level_changed = _Signal()
    text_pasted = _Signal()
    error_occurred = _Signal()

    def start(self):
        pass

    def stop(self):
        pass


class FakeEngine:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.stopped = 0
        self.received = []
        FakeEngine.instances.append(self)

    target_language = "cs"

    def start(self):
        self.started = True

    def feed(self, pcm_bytes):
        self.received.append(pcm_bytes)

    def stop(self, timeout=5):
        self.stopped += 1

    def request_stop(self):
        self.stopped += 1

    def wait_stopped(self, timeout=5):
        return True

    def transcript(self):
        return []

    def timed_speaker_transcript(self):
        return []

    # The window connects to these; a plain object with connect() is enough.
    class _Signal:
        def connect(self, slot):
            pass

    line_finalized = _Signal()
    speaker_line_finalized = _Signal()
    line_updated = _Signal()
    notice = _Signal()
    error_occurred = _Signal()


@pytest.fixture(scope="module")
def app():
    """Held for the whole module: an unreferenced QApplication is collected and Qt dies."""
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    FakeEngine.instances = []

    captures = []

    def make_capture():
        captures.append(FakeCapture())
        return captures[-1]

    monkeypatch.setattr(mw, "create_backend", make_capture)
    monkeypatch.setattr(mw, "SubtitleEngine", FakeEngine)
    # Never the real one: it writes the user's live configuration file.
    monkeypatch.setattr(mw, "save_config", lambda config: None)
    monkeypatch.setattr(MainWindow, "_start_update_check", lambda self: None)
    monkeypatch.setattr(mw.secrets, "get_api_key", lambda: "key")
    monkeypatch.setattr(mw, "DictationEngine", FakeDictation)

    built = []

    def build(rts_translate, live_speakers=False, automatic_recording=False):
        w = MainWindow({
            "output_dir": str(tmp_path),
            "rts_translate": rts_translate,
            "live_speakers": live_speakers,
            "meeting_detection": False,
            "automatic_recording": automatic_recording,
            "language": "en",
            "translation_target": "cs",
        })
        w.captures = captures
        built.append(w)
        return w

    yield build

    # Every window is closed, because closing is what takes its application-wide event
    # filter back out. An abandoned window is collected while the filter is still
    # installed, and Qt then calls the filter on a half-destroyed object and aborts the
    # whole process. That is what broke the Linux and macOS release builds.
    for w in built:
        # Cleared first: closing while it is set asks the user whether to stop the
        # recording, and nobody is here to answer.
        w._is_recording = False
        w._overlay.close()
        w.close()
        w.deleteLater()
    app.processEvents()


class TestNothingIsPaidForWhileOff:
    def test_recording_opens_no_session(self, window):
        w = window(rts_translate=False)

        w._start_recording()

        assert FakeEngine.instances == [], "a session is billed the moment it opens"

    def test_no_audio_is_handed_to_the_tap(self, window):
        """The tap is what turns recorded audio into streamed, billable audio."""
        w = window(rts_translate=False)

        w._start_recording()

        assert w.captures[0].tap is None

    def test_the_recording_itself_still_runs(self, window):
        """Off must cost nothing and change nothing else."""
        w = window(rts_translate=False)

        w._start_recording()

        assert w.captures[0].started
        assert w._is_recording

    def test_runtime_capture_failure_stops_the_recording(self, window, monkeypatch):
        warnings = []
        monkeypatch.setattr(
            mw.QMessageBox,
            "warning",
            lambda parent, title, message: warnings.append((title, message)),
        )
        w = window(rts_translate=False)
        w._start_recording()

        w.captures[0].error_callback("System audio capture stopped unexpectedly: test")

        assert w.captures[0].stopped
        assert not w._is_recording
        assert warnings[0][0] == "Recording Stopped"
        assert "partial recording is being saved" in warnings[0][1]


class TestSwitchingOffMidRecording:
    def recording_with_subtitles(self, window):
        w = window(rts_translate=True)
        w._start_recording()
        assert FakeEngine.instances, "the toggle was on, so a session should exist"
        return w

    def test_the_session_is_closed_rather_than_hidden(self, window):
        """Hiding the area while still streaming would go on being billed unseen."""
        w = self.recording_with_subtitles(window)

        w._btn_rts.setChecked(False)
        w._toggle_subtitles()

        assert FakeEngine.instances[0].stopped == 1

    def test_the_tap_stops_feeding_it(self, window):
        w = self.recording_with_subtitles(window)

        w._btn_rts.setChecked(False)
        w._toggle_subtitles()

        assert w.captures[0].tap is None

    def test_what_was_heard_is_kept_for_the_sidecar(self, window):
        """Switching off must not throw away the subtitles of the call so far."""
        w = self.recording_with_subtitles(window)

        w._btn_rts.setChecked(False)
        w._toggle_subtitles()

        assert w._subtitle_engine is not None


class TestWhileOn:
    def test_a_session_is_opened_and_fed(self, window):
        w = window(rts_translate=True)

        w._start_recording()

        assert len(FakeEngine.instances) == 1
        assert w.captures[0].tap is not None

    def test_the_translation_target_comes_from_the_settings(self, window):
        w = window(rts_translate=True)

        w._start_recording()

        assert FakeEngine.instances[0].kwargs["translate_to"] == "cs"


class TestLiveSpeakerToggle:
    def test_off_opens_no_extra_session(self, window):
        w = window(rts_translate=False, live_speakers=False)
        w._start_recording()
        assert FakeEngine.instances == []
        assert w.captures[0].mic_tap is None

    def test_on_opens_local_and_remote_sessions(self, window):
        w = window(rts_translate=False, live_speakers=True)
        w._start_recording()
        assert len(FakeEngine.instances) == 2
        assert {engine.kwargs.get("speaker_label") for engine in FakeEngine.instances} == {
            None, "Me"}
        assert w.captures[0].tap is not None
        assert w.captures[0].mic_tap is not None

    def test_remote_session_uses_diarization(self, window):
        w = window(rts_translate=False, live_speakers=True)
        w._start_recording()
        remote = next(engine for engine in FakeEngine.instances
                      if engine.kwargs.get("speaker_prefix") == "Remote")
        assert remote.kwargs["enable_speaker_diarization"] is True

    def test_microphone_only_recording_opens_only_the_me_session(
            self, window, monkeypatch):
        monkeypatch.setattr(FakeCapture, "active_system_source", None)
        w = window(rts_translate=False, live_speakers=True)
        w._start_recording()
        assert len(FakeEngine.instances) == 1
        assert FakeEngine.instances[0].kwargs["speaker_label"] == "Me"
        assert w.captures[0].tap is None
        assert w.captures[0].mic_tap is not None

    def test_switching_off_closes_both_sessions_and_taps(self, window):
        w = window(rts_translate=False, live_speakers=True)
        w._start_recording()
        w._btn_live_speakers.setChecked(False)
        w._toggle_live_speakers()
        assert all(engine.stopped == 1 for engine in FakeEngine.instances)
        assert w.captures[0].tap is None
        assert w.captures[0].mic_tap is None

    def test_translation_and_speakers_share_the_system_tap(self, window):
        w = window(rts_translate=True, live_speakers=True)
        w._start_recording()
        w.captures[0].tap(b"audio")
        assert len(FakeEngine.instances) == 3
        translation = next(engine for engine in FakeEngine.instances
                           if engine.kwargs.get("translate_to") == "cs")
        remote = next(engine for engine in FakeEngine.instances
                      if engine.kwargs.get("speaker_prefix") == "Remote")
        assert translation.received == [b"audio"]
        assert remote.received == [b"audio"]

    def test_stopping_the_recording_closes_both_speaker_sessions(self, window):
        w = window(rts_translate=False, live_speakers=True)
        w._start_recording()
        w._stop_recording()
        assert all(engine.stopped == 1 for engine in FakeEngine.instances)

    def test_mapped_name_is_used_for_new_live_lines(self, window):
        w = window(rts_translate=False, live_speakers=True)
        w._speaker_name_map = {"Remote 1": "Radek Švarz"}
        w._on_speaker_line("10:00:00", "Remote 1", "Agreed.")
        text = w._subtitles._settled.toPlainText()
        assert "Radek Švarz" in text
        assert w._subtitles._btn_speaker_names.isEnabled()

    def test_complete_sidecar_uses_names_assigned_after_the_line(self, window, tmp_path):
        w = window(rts_translate=False, live_speakers=True)
        w._speaker_segments = [(1, "10:00:00", "Remote 1", "Agreed.")]
        w._speaker_name_map = {"Remote 1": "Radek Švarz"}
        recording = str(tmp_path / "call.m4a")
        w._pending_sidecars[recording] = {
            "subtitles": None,
            "speakers": w._speaker_sidecar_payload(),
        }
        w._write_pending_sidecars(recording)
        text = (tmp_path / "call.speakers.md").read_text(encoding="utf-8")
        assert "Radek Švarz" in text

    def test_sidecars_wait_for_successful_encoding(self, window, tmp_path):
        w = window(rts_translate=False, live_speakers=True)
        w._speaker_segments = [(1, "10:00:00", "Remote 1", "Agreed.")]
        recording = str(tmp_path / "call.m4a")
        w._pending_sidecars[recording] = {
            "subtitles": None,
            "speakers": w._speaker_sidecar_payload(),
        }
        assert not (tmp_path / "call.speakers.md").exists()
        w._on_encode_finished(recording)
        assert (tmp_path / "call.speakers.md").exists()


class TestMeetingPopupIntegration:
    candidate = MeetingCandidate("1:2", "Zoom", "Project sync - Zoom Meeting")

    class Detector:
        def __init__(self, candidate):
            self.candidate = candidate

        def scan(self):
            return [self.candidate]

    def test_a_stable_signal_shows_the_recording_prompt(self, window):
        w = window(rts_translate=False)
        w._meeting_detector = self.Detector(self.candidate)
        w._meeting_prompt_policy = MeetingPromptPolicy(observations_required=2)
        w._poll_for_meeting()
        assert w._meeting_prompt is None
        w._poll_for_meeting()
        assert w._meeting_prompt is not None
        w._meeting_prompt.reject()

    def test_macos_prompt_is_not_tied_to_the_main_window_space(
            self, window, monkeypatch):
        w = window(rts_translate=False)
        w._meeting_detector = self.Detector(self.candidate)
        w._meeting_prompt_policy = MeetingPromptPolicy(observations_required=1)
        monkeypatch.setattr(mw.sys, "platform", "darwin")

        w._poll_for_meeting()

        assert w._meeting_prompt.parent() is None
        w._meeting_prompt.reject()

    def test_other_platforms_keep_the_existing_prompt_ownership(
            self, window, monkeypatch):
        w = window(rts_translate=False)
        w._meeting_detector = self.Detector(self.candidate)
        w._meeting_prompt_policy = MeetingPromptPolicy(observations_required=1)
        monkeypatch.setattr(mw.sys, "platform", "linux")

        w._poll_for_meeting()

        assert w._meeting_prompt.parent() is w
        w._meeting_prompt.reject()

    def test_accepting_the_prompt_starts_the_normal_recorder(self, window):
        w = window(rts_translate=False)
        w._meeting_detector = self.Detector(self.candidate)
        w._meeting_prompt_policy = MeetingPromptPolicy(observations_required=1)
        w._poll_for_meeting()
        w._meeting_prompt.accept()
        assert w._is_recording
        assert w.captures[-1].started

    def test_an_active_recording_suppresses_the_popup(self, window):
        w = window(rts_translate=False)
        w._start_recording()
        w._meeting_detector = self.Detector(self.candidate)
        w._meeting_prompt_policy = MeetingPromptPolicy(observations_required=1)
        w._poll_for_meeting()
        assert w._meeting_prompt is None

    def test_opt_in_uses_a_cancelable_countdown_for_native_calls(self, window):
        w = window(rts_translate=False, automatic_recording=True)
        w._meeting_detector = self.Detector(self.candidate)
        w._meeting_prompt_policy = MeetingPromptPolicy(observations_required=1)
        w._poll_for_meeting()
        prompt = w._meeting_prompt
        assert prompt.is_automatic
        for _ in range(mw.AUTO_START_SECONDS):
            prompt._tick()
        assert w._is_recording

    def test_calendar_signal_never_automatically_starts_recording(self, window):
        calendar = MeetingCandidate(
            "calendar:1", "Calendar", "Project sync", source="calendar")
        w = window(rts_translate=False, automatic_recording=True)
        w._meeting_detector = self.Detector(calendar)
        w._meeting_prompt_policy = MeetingPromptPolicy(observations_required=1)
        w._poll_for_meeting()
        assert not w._meeting_prompt.is_automatic
        w._meeting_prompt.reject()

    def test_disappearing_call_cancels_the_countdown(self, window):
        detector = self.Detector(self.candidate)
        w = window(rts_translate=False, automatic_recording=True)
        w._meeting_detector = detector
        w._meeting_prompt_policy = MeetingPromptPolicy(observations_required=1)
        w._poll_for_meeting()
        assert w._meeting_prompt is not None
        detector.candidate = None
        detector.scan = lambda: []
        w._poll_for_meeting()
        assert w._meeting_prompt is None
        assert not w._is_recording

    def test_last_second_stale_signal_cannot_start_recording(self, window):
        detector = self.Detector(self.candidate)
        w = window(rts_translate=False, automatic_recording=True)
        w._meeting_detector = detector
        w._meeting_prompt_policy = MeetingPromptPolicy(observations_required=1)
        w._poll_for_meeting()
        prompt = w._meeting_prompt
        for _ in range(mw.AUTO_START_SECONDS - 1):
            prompt._tick()
        detector.scan = lambda: []
        prompt._tick()
        assert not w._is_recording

    def test_start_now_is_an_explicit_override_of_the_detector(self, window):
        detector = self.Detector(self.candidate)
        w = window(rts_translate=False, automatic_recording=True)
        w._meeting_detector = detector
        w._meeting_prompt_policy = MeetingPromptPolicy(observations_required=1)
        w._poll_for_meeting()
        detector.scan = lambda: []
        w._meeting_prompt.accept()
        assert w._is_recording


class TestTheButtonShowsItIsOn:
    """Green is the signal that a paid session is live, as on the Dictation button."""

    def test_it_is_green_when_switched_on(self, window):
        w = window(rts_translate=False)

        w._btn_rts.setChecked(True)
        w._toggle_subtitles()

        assert w._btn_rts.styleSheet() == ACTIVE_TOGGLE_STYLE

    def test_it_is_plain_again_when_switched_off(self, window):
        w = window(rts_translate=True)

        w._btn_rts.setChecked(False)
        w._toggle_subtitles()

        assert w._btn_rts.styleSheet() == ""

    def test_it_starts_green_when_the_setting_was_left_on(self, window):
        """The setting is remembered, so the colour has to be right before any click."""
        assert window(rts_translate=True)._btn_rts.styleSheet() == ACTIVE_TOGGLE_STYLE

    def test_it_starts_plain_when_the_setting_was_left_off(self, window):
        assert window(rts_translate=False)._btn_rts.styleSheet() == ""

    def test_it_matches_the_dictation_button(self, window):
        w = window(rts_translate=False)

        w._start_dictation()

        assert w._btn_dictation.styleSheet() == ACTIVE_TOGGLE_STYLE
