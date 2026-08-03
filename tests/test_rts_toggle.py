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

from audiorecorder.ui import main_window as mw  # noqa: E402
from audiorecorder.ui.main_window import ACTIVE_TOGGLE_STYLE, MainWindow  # noqa: E402


class FakeCapture:
    def __init__(self):
        self.tap = None
        self.started = False

    def set_level_callbacks(self, system, mic):
        pass

    def set_system_tap(self, callback):
        self.tap = callback

    def start(self, tmp_dir, system_source, mic_source):
        self.started = True

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
        FakeEngine.instances.append(self)

    target_language = "cs"

    def start(self):
        self.started = True

    def feed(self, pcm_bytes):
        pass

    def stop(self, timeout=5):
        self.stopped += 1

    def transcript(self):
        return []

    # The window connects to these; a plain object with connect() is enough.
    class _Signal:
        def connect(self, slot):
            pass

    line_finalized = _Signal()
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

    def build(rts_translate):
        w = MainWindow({
            "output_dir": str(tmp_path),
            "rts_translate": rts_translate,
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
