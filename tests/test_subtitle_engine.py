"""The subtitle engine runs for the length of a call and must not take the call with it.

Everything here uses a fake session: the realtime protocol is tested in test_streaming.py,
and what matters at this level is the lifecycle. A session that dies mid-call has to come
back, a queue that fills has to stay near the present, and stop() has to return.
"""

import threading
import time

import pytest
from PyQt6.QtCore import QCoreApplication

from audiorecorder.subtitles import engine as engine_module
from audiorecorder.subtitles.engine import QUEUE_LIMIT, SubtitleEngine


@pytest.fixture(autouse=True)
def app():
    """Signals are emitted from the worker; a receiver-less app is enough to construct."""
    return QCoreApplication.instance() or QCoreApplication([])


class FakeSession:
    """Stands in for the realtime session, with the same surface the engine touches."""

    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.on_pair = kwargs.get("on_pair")
        self.sent = []
        self.connected = False
        self.finished = threading.Event()
        self.finish_calls = 0
        self._original = ""
        self._translation = ""
        self._plain = ""
        self._plain_pending = ""
        FakeSession.instances.append(self)

    def connect(self):
        self.connected = True

    def send_audio(self, chunk):
        self.sent.append(chunk)

    def is_finished(self):
        return self.finished.is_set()

    def finish(self, timeout=10):
        self.finish_calls += 1
        self.finished.set()

    def finalized_original(self):
        return self._original

    def finalized_translation(self):
        return self._translation

    def finalized_untranslated(self):
        return self._plain

    def get_untranslated_text(self):
        return self._plain + self._plain_pending

    def say(self, original, translation, settled=True):
        """Pretend the server recognised something."""
        if settled:
            self._original += original
            self._translation += translation
            self.on_pair(self._original, self._translation)
        else:
            self.on_pair(self._original + original, self._translation + translation)

    def say_in_the_target_language(self, text):
        """Speech that needs no translation, which the server tags `none`."""
        self._plain += text
        self.on_pair(self._original, self._translation)


@pytest.fixture(autouse=True)
def fake_session(monkeypatch):
    FakeSession.instances = []
    monkeypatch.setattr(engine_module, "SonioxStreamingSession", FakeSession)
    monkeypatch.setattr(engine_module, "RECONNECT_DELAYS", (0.01, 0.01, 0.01))
    return FakeSession


def wait_for(condition, timeout=3.0):
    """Waits while pumping the event loop.

    Signals emitted from the worker thread are queued for the thread the engine lives in,
    and are only delivered when that thread processes events. The application has an event
    loop doing that; a test has to do it by hand or the signal never arrives.
    """
    instance = QCoreApplication.instance()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if instance is not None:
            instance.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def started():
    engines = []

    def make(language="cs"):
        e = SubtitleEngine(api_key="key", translate_to=language)
        e.start()
        assert wait_for(lambda: FakeSession.instances), "the session should open"
        engines.append(e)
        return e

    yield make
    for e in engines:
        e.stop(timeout=2)


class TestConstruction:
    def test_a_missing_key_is_refused_up_front(self):
        with pytest.raises(ValueError):
            SubtitleEngine(api_key="", translate_to="cs")

    def test_a_language_becomes_the_translation_target(self):
        assert SubtitleEngine(api_key="k", translate_to="cs").translating

    @pytest.mark.parametrize("language", ["auto", "", None])
    def test_without_a_target_language_nothing_is_translated(self, language):
        """Auto names no language to translate into, so the subtitles just show speech."""
        assert not SubtitleEngine(api_key="k", translate_to=language).translating

    def test_the_target_is_the_translate_to_setting_not_the_primary_language(self):
        """With Language=English and Translate to=Czech, an English call must come out
        in Czech. Reading the primary language here translated English into English."""
        engine = SubtitleEngine(api_key="k", translate_to="cs")
        assert engine.target_language == "cs"

    def test_the_target_reaches_the_session(self, started):
        started("cs")
        assert FakeSession.instances[0].kwargs["translate_to"] == "cs"

    def test_without_a_source_language_the_server_decides(self, started):
        started("cs")
        assert FakeSession.instances[0].kwargs["language_hints"] == []


class TestFeeding:
    def test_audio_reaches_the_session(self, started):
        e = started()
        e.feed(b"\x00\x01" * 512)
        assert wait_for(lambda: FakeSession.instances[0].sent)

    def test_feeding_before_start_is_ignored(self):
        e = SubtitleEngine(api_key="k", translate_to="cs")
        e.feed(b"\x00" * 10)  # must not raise
        assert e._audio.qsize() == 0

    def test_a_stalled_network_drops_old_audio_rather_than_growing(self, started):
        """Falling behind the call is worse than losing the oldest second of it."""
        e = started()
        e._running = True
        for _ in range(QUEUE_LIMIT * 2):
            e.feed(b"x")
        assert e._audio.qsize() <= QUEUE_LIMIT + 1


class TestRecognisedText:
    def test_a_settled_pair_is_emitted_once(self, started):
        e = started()
        finalized = []
        e.line_finalized.connect(lambda ts, o, t: finalized.append((o, t)))

        FakeSession.instances[0].say("Hello. ", "Ahoj. ")

        assert wait_for(lambda: finalized)
        assert finalized[0] == ("Hello.", "Ahoj.")

    def test_settled_pairs_are_not_repeated(self, started):
        e = started()
        finalized = []
        e.line_finalized.connect(lambda ts, o, t: finalized.append((o, t)))

        session = FakeSession.instances[0]
        session.say("One. ", "Jedna. ")
        session.say("Two. ", "Dva. ")

        assert wait_for(lambda: len(finalized) == 2)
        assert finalized == [("One.", "Jedna."), ("Two.", "Dva.")]

    def test_the_sentence_in_progress_is_reported_separately(self, started):
        e = started()
        pending = []
        e.line_updated.connect(lambda o, t: pending.append((o, t)))

        FakeSession.instances[0].say("Hel", "Ah", settled=False)

        assert wait_for(lambda: pending)
        assert pending[-1] == ("Hel", "Ah")

    def test_the_transcript_keeps_what_was_said(self, started):
        e = started()
        session = FakeSession.instances[0]
        session.say("One. ", "Jedna. ")
        session.say("Two. ", "Dva. ")

        assert wait_for(lambda: len(e.transcript()) == 2)
        assert [(o, tr) for _, o, tr in e.transcript()] == [("One.", "Jedna."), ("Two.", "Dva.")]

    def test_the_transcript_survives_stopping(self, started):
        e = started()
        FakeSession.instances[0].say("One. ", "Jedna. ")
        assert wait_for(lambda: e.transcript())
        e.stop(timeout=2)
        assert [(o, tr) for _, o, tr in e.transcript()] == [("One.", "Jedna.")]


class TestReconnect:
    def test_a_session_that_dies_mid_call_is_reopened(self, started):
        e = started()
        notices = []
        e.notice.connect(notices.append)

        FakeSession.instances[0].finished.set()

        assert wait_for(lambda: len(FakeSession.instances) >= 2), "it should try again"
        assert wait_for(lambda: any("reconnect" in n.lower() for n in notices))

    def kill_every_session_until(self, condition, timeout=8):
        """Keep the network broken until the engine reacts.

        The engine notices a dead session within its poll interval, so killing a fixed
        number of times and hoping is a race. This keeps killing whatever it opens.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if FakeSession.instances:
                FakeSession.instances[-1].finished.set()
            instance = QCoreApplication.instance()
            if instance is not None:
                instance.processEvents()
            if condition():
                return True
            time.sleep(0.02)
        return False

    def test_it_gives_up_loudly_rather_than_going_quiet(self, started):
        e = started()
        notices = []
        e.notice.connect(notices.append)

        assert self.kill_every_session_until(
            lambda: any("stopped" in n.lower() for n in notices))
        assert len(FakeSession.instances) <= 1 + len(engine_module.RECONNECT_DELAYS)

    def test_giving_up_says_the_recording_is_unaffected(self, started):
        e = started()
        notices = []
        e.notice.connect(notices.append)

        assert self.kill_every_session_until(
            lambda: any("recording" in n.lower() for n in notices))

    def test_a_session_that_will_not_open_is_reported(self, monkeypatch):
        def refuse(**kwargs):
            raise ConnectionError("no route to host")

        monkeypatch.setattr(engine_module, "SonioxStreamingSession", refuse)
        e = SubtitleEngine(api_key="k", translate_to="cs")
        errors = []
        e.error_occurred.connect(errors.append)
        e.start()

        assert wait_for(lambda: errors)
        assert "no route to host" in errors[0]
        e.stop(timeout=2)


class TestStopping:
    def test_stop_closes_the_session(self, started):
        e = started()
        session = FakeSession.instances[0]
        e.stop(timeout=2)
        assert session.finish_calls == 1

    def test_stop_returns_even_with_a_full_queue(self, started):
        e = started()
        for _ in range(QUEUE_LIMIT):
            e.feed(b"x")
        e.stop(timeout=3)
        assert e._worker is None

    def test_stopping_twice_is_harmless(self, started):
        e = started()
        e.stop(timeout=2)
        e.stop(timeout=2)

    def test_starting_twice_opens_one_session(self, started):
        e = started()
        e.start()
        time.sleep(0.1)
        assert len(FakeSession.instances) == 1


class TestSourceLanguages:
    """Given the whole world to choose from, the model guessed wrong.

    English test speech came back tagged as Dutch and the translation was of the mistake.
    Naming the languages a call can actually be in narrows it to a choice it can make.
    """

    def hints_for(self, **kwargs):
        e = SubtitleEngine(api_key="k", **kwargs)
        e.start()
        assert wait_for(lambda: FakeSession.instances)
        try:
            return FakeSession.instances[-1].kwargs["language_hints"]
        finally:
            e.stop(timeout=2)

    def test_both_configured_languages_are_offered(self):
        assert self.hints_for(translate_to="cs", source_languages=["en", "cs"]) == ["en", "cs"]

    def test_auto_and_blanks_are_dropped(self):
        engine = SubtitleEngine(api_key="k", translate_to="cs",
                                source_languages=["auto", "", None, "en"])
        assert engine.source_languages == ["en"]

    def test_the_same_language_twice_is_named_once(self):
        """Language and Translate to can be set to the same thing."""
        engine = SubtitleEngine(api_key="k", translate_to="cs",
                                source_languages=["cs", "cs"])
        assert engine.source_languages == ["cs"]

    def test_no_candidates_lets_the_server_decide(self):
        engine = SubtitleEngine(api_key="k", translate_to="cs", source_languages=["auto"])
        assert engine.source_languages == []

    def test_the_target_is_independent_of_the_candidates(self):
        engine = SubtitleEngine(api_key="k", translate_to="cs", source_languages=["en", "cs"])
        assert engine.target_language == "cs"


class TestSpeechAlreadyInTheTargetLanguage:
    """A Czech sentence on a call being translated into Czech has no counterpart.

    It used to be filed as a translation and held, so it sat waiting for an original. The
    next English sentence supplied one, and the two were shown as a pair: "Nice to meet
    you." above "Ahoj. Cau, jak se mate?".
    """

    def test_it_is_a_finished_line_on_its_own(self, started):
        e = started()
        finalized = []
        e.line_finalized.connect(lambda ts, o, t: finalized.append((o, t)))

        FakeSession.instances[0].say_in_the_target_language("Cau, jak se mate? ")

        assert wait_for(lambda: finalized)
        assert finalized[0] == ("", "Cau, jak se mate?")

    def test_it_does_not_become_the_partner_of_the_next_sentence(self, started):
        e = started()
        finalized = []
        e.line_finalized.connect(lambda ts, o, t: finalized.append((o, t)))
        session = FakeSession.instances[0]

        session.say_in_the_target_language("Cau, jak se mate? ")
        session.say("Nice to meet you. ", "Rad vas poznavam. ")

        assert wait_for(lambda: len(finalized) == 2)
        assert finalized == [("", "Cau, jak se mate?"),
                             ("Nice to meet you.", "Rad vas poznavam.")]

    def test_it_is_shown_while_still_being_spoken(self, started):
        """Otherwise a whole Czech sentence appears out of nowhere when it settles."""
        e = started()
        pending = []
        e.line_updated.connect(lambda o, t: pending.append((o, t)))

        session = FakeSession.instances[0]
        session._plain_pending = "Cau"
        session.on_pair("", "")

        assert wait_for(lambda: ("", "Cau") in pending)

    def test_it_reaches_the_transcript(self, started):
        e = started()
        FakeSession.instances[0].say_in_the_target_language("Cau. ")
        assert wait_for(lambda: e.transcript())
        assert [(o, t) for _, o, t in e.transcript()] == [("", "Cau.")]
