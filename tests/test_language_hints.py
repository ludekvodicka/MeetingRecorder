""""auto" is a setting of ours, not a language code.

Sending it to Soniox as a language hint makes the server answer
`400 invalid_request: Invalid language hint` and close the socket immediately, so the
dictation recorded happily for three seconds, transcribed nothing and pasted nothing,
without any visible failure.
"""

from audiorecorder.dictation.engine import DictationEngine
from audiorecorder.dictation.streaming import SonioxStreamingSession


def engine(language):
    return DictationEngine(api_key="x", language=language, translate_target="en",
                           mic_source_name=None, sample_rate=16000)


class TestEngineLanguageHints:
    def test_a_real_language_is_passed_as_a_hint(self):
        assert engine("cs").language_hints() == ["cs"]
        assert engine("en").language_hints() == ["en"]

    def test_auto_sends_no_hint_at_all(self):
        assert engine("auto").language_hints() == []

    def test_an_empty_setting_sends_no_hint(self):
        assert engine("").language_hints() == []
        assert engine(None).language_hints() == []


class TestSessionConfig:
    def config_of(self, hints):
        session = SonioxStreamingSession(api_key="key", language_hints=hints)
        sent = {}

        class FakeWs:
            def send(self, payload):
                import json
                sent.update(json.loads(payload))

        session._on_open(FakeWs())
        return sent

    def test_a_hint_is_sent_and_identification_stays_off(self):
        config = self.config_of(["cs"])
        assert config["language_hints"] == ["cs"]
        assert config["enable_language_identification"] is False

    def test_without_a_hint_the_key_is_absent_and_identification_is_on(self):
        config = self.config_of([])
        assert "language_hints" not in config
        assert config["enable_language_identification"] is True

    def test_the_default_is_no_hint_rather_than_czech(self):
        """The old default quietly transcribed every dictation as Czech."""
        session = SonioxStreamingSession(api_key="key")
        assert session.language_hints == []

    def test_the_translation_target_is_still_sent(self):
        session = SonioxStreamingSession(api_key="key", language_hints=["cs"],
                                         translate_to="en")
        sent = {}

        class FakeWs:
            def send(self, payload):
                import json
                sent.update(json.loads(payload))

        session._on_open(FakeWs())
        assert sent["translation"] == {"type": "one_way", "target_language": "en"}


class TestSilentFailuresNowSpeak:
    """A dictation that produces nothing must say so rather than appear to have worked.

    The handler is exercised against a stub rather than a real window: building one starts
    an update check and a compositor-backed overlay, neither of which belongs in a unit test.
    """

    def handle(self, state):
        from audiorecorder.ui.main_window import MainWindow

        class StubOverlay:
            state = None

            def set_state(self, value):
                self.state = value

        class StubStatusBar:
            def __init__(self):
                self.messages = []

            def showMessage(self, message, timeout=0):
                self.messages.append(message)

        class StubWindow:
            def __init__(self):
                self._overlay = StubOverlay()
                self._status_bar = StubStatusBar()
                self._dictation_active = True

        window = StubWindow()
        MainWindow._on_dictation_state(window, state)
        return window

    def test_a_notice_reaches_the_status_bar(self):
        window = self.handle("notice:Nothing was recognized")
        assert window._status_bar.messages == ["Nothing was recognized"]

    def test_a_notice_leaves_the_overlay_idle(self):
        window = self.handle("notice:Too short")
        assert window._overlay.state == "idle"

    def test_recording_still_drives_the_overlay(self):
        window = self.handle("recording")
        assert window._overlay.state == "recording"
