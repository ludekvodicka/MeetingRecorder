"""A tidy WebSocket close is not a failure.

websocket-client hands the server's closing frame to the error callback, so the end of a
finished dictation used to be reported as `Soniox error: fin=1 opcode=8 data=b'\\x03\\xe8'`.
That aborted finish() before the transcribed text was read, so the dictation was never
pasted even though the transcription had succeeded.
"""

import json

import pytest
import websocket

from audiorecorder.dictation.streaming import SonioxStreamingSession, is_normal_close


def close_frame(status=None):
    data = b"" if status is None else status.to_bytes(2, "big")
    return websocket.ABNF(opcode=websocket.ABNF.OPCODE_CLOSE, data=data)


class TestIsNormalClose:
    def test_status_1000_is_normal(self):
        assert is_normal_close(close_frame(1000))

    def test_a_close_without_a_status_is_treated_as_normal(self):
        assert is_normal_close(close_frame())

    def test_an_abnormal_status_is_not(self):
        assert not is_normal_close(close_frame(1006))
        assert not is_normal_close(close_frame(1011))

    def test_a_data_frame_is_not_a_close(self):
        frame = websocket.ABNF(opcode=websocket.ABNF.OPCODE_TEXT, data=b"hello")
        assert not is_normal_close(frame)

    def test_a_real_exception_is_not_a_close(self):
        assert not is_normal_close(ConnectionResetError("connection reset"))
        assert not is_normal_close(RuntimeError("boom"))


class TestSessionErrorHandling:
    def session(self):
        return SonioxStreamingSession(api_key="x")

    def test_a_normal_close_leaves_no_error_behind(self):
        s = self.session()
        s._on_error(None, close_frame(1000))
        assert s._error is None
        assert s._finished.is_set()

    def test_a_real_error_is_kept(self):
        s = self.session()
        s._on_error(None, ConnectionResetError("connection reset"))
        assert "connection reset" in s._error
        assert s._finished.is_set()

    def test_finish_after_a_normal_close_does_not_raise(self):
        """The whole point: the text has to survive the end of the stream."""
        s = self.session()
        s._final_tokens = [{"text": "hello"}]
        s._on_error(None, close_frame(1000))

        s.finish(timeout=0.1)

        assert s.get_final_text() == "hello"


def token(text, final=True, status=None):
    tok = {"text": text, "is_final": final}
    if status:
        tok["translation_status"] = status
    return tok


def deliver(session, tokens, finished=False):
    import json
    session._on_message(None, json.dumps({"tokens": tokens, "finished": finished}))


class TestBothStreams:
    """A translating session returns the source and the target language in one stream.

    Dictation only ever wanted the target, and used to throw the original away on arrival.
    Subtitles show both, so nothing is discarded any more and the accessors decide.
    """

    def translating(self, **kwargs):
        return SonioxStreamingSession(api_key="k", language_hints=["en"],
                                      translate_to="cs", **kwargs)

    def test_the_two_languages_come_out_separately(self):
        s = self.translating()
        deliver(s, [token("Hello. ", status="original"), token("Ahoj. ", status="translation")])

        assert s.get_original_text() == "Hello."
        assert s.get_translated_text() == "Ahoj."

    def test_dictation_still_gets_only_the_translation(self):
        s = self.translating()
        deliver(s, [token("Hello. ", status="original"), token("Ahoj. ", status="translation")])

        assert s.get_final_text() == "Ahoj."

    def test_without_translation_the_original_is_the_result(self):
        s = SonioxStreamingSession(api_key="k", language_hints=["cs"])
        deliver(s, [token("Dobrý den.")])

        assert s.get_final_text() == "Dobrý den."
        assert s.get_original_text() == "Dobrý den."
        assert s.get_translated_text() == ""

    def test_provisional_text_does_not_bleed_between_languages(self):
        s = self.translating()
        deliver(s, [token("Hel", final=False, status="original"),
                    token("Ah", final=False, status="translation")])

        assert s.get_original_text() == "Hel"
        assert s.get_translated_text() == "Ah"

    def test_provisional_is_replaced_not_appended(self):
        s = self.translating()
        deliver(s, [token("Ah", final=False, status="translation")])
        deliver(s, [token("Ahoj", final=False, status="translation")])

        assert s.get_translated_text() == "Ahoj"

    def test_finals_accumulate_across_messages(self):
        s = self.translating()
        deliver(s, [token("Ahoj. ", status="translation")])
        deliver(s, [token("Jak se máš?", status="translation")])

        assert s.get_translated_text() == "Ahoj. Jak se máš?"

    def test_the_pair_callback_reports_both(self):
        pairs = []
        s = self.translating(on_pair=lambda o, t: pairs.append((o, t)))
        deliver(s, [token("Hello", final=False, status="original"),
                    token("Ahoj", final=False, status="translation")])

        assert pairs[-1] == ("Hello", "Ahoj")

    def test_the_preview_callback_keeps_its_meaning(self):
        """Dictation reads this one, and must still see only the translation."""
        previews = []
        s = self.translating(on_preview=previews.append)
        deliver(s, [token("Hello", final=False, status="original"),
                    token("Ahoj", final=False, status="translation")])

        assert previews[-1] == "Ahoj"

    def test_an_unknown_status_is_refused_rather_than_guessed(self):
        s = self.translating()
        with pytest.raises(ValueError):
            s._assemble("something-else", "")


class TestFinalOnlyMessages:
    """The last sentence of a stream arrives already final, with no provisional left.

    Watching only the provisional text meant that sentence never reached the listener, so
    the closing line of every call lost its translation.
    """

    def test_a_final_only_message_notifies(self):
        pairs = []
        s = SonioxStreamingSession(api_key="k", translate_to="cs",
                                   on_pair=lambda o, t: pairs.append((o, t)))
        deliver(s, [token("Goodbye. ", status="original"),
                    token("Na shledanou. ", status="translation")])

        assert pairs, "settling text has to notify even with no provisional change"
        assert pairs[-1] == ("Goodbye.", "Na shledanou.")

    def test_a_message_that_changes_nothing_does_not_notify(self):
        pairs = []
        s = SonioxStreamingSession(api_key="k", translate_to="cs",
                                   on_pair=lambda o, t: pairs.append((o, t)))
        deliver(s, [])
        deliver(s, [])

        assert pairs == []

    def test_dictation_still_hears_the_last_sentence(self):
        previews = []
        s = SonioxStreamingSession(api_key="k", translate_to="cs", on_preview=previews.append)
        deliver(s, [token("Goodbye. ", status="original"),
                    token("Na shledanou. ", status="translation")])

        assert previews[-1] == "Na shledanou."


class TestSpeechAlreadyInTheTargetLanguage:
    """Soniox tags it `none`: neither a source awaiting a translation nor a translation.

    Filing it under "translation" is what paired a Czech sentence with the English one
    spoken after it, because the English original then found a partner already waiting.
    """

    def session(self):
        return SonioxStreamingSession(api_key="k", translate_to="cs")

    def final(self, text, status):
        return {"text": text, "is_final": True, "translation_status": status}

    def test_it_is_not_taken_for_a_translation(self):
        s = self.session()
        s._on_message(None, json.dumps({"tokens": [self.final("Čau.", "none")]}))
        assert s.finalized_translation() == ""
        assert s.finalized_untranslated() == "Čau."

    def test_it_is_not_taken_for_an_original_awaiting_one_either(self):
        s = self.session()
        s._on_message(None, json.dumps({"tokens": [self.final("Čau.", "none")]}))
        assert s.finalized_original() == ""

    def test_it_still_counts_as_the_text_the_caller_wanted(self):
        """Dictating into Czech with the Czech already spoken must paste it, not nothing."""
        s = self.session()
        s._on_message(None, json.dumps({"tokens": [self.final("Čau.", "none")]}))
        assert s.get_final_text() == "Čau."

    def test_a_real_translation_still_lands_in_its_own_bucket(self):
        s = self.session()
        s._on_message(None, json.dumps({"tokens": [
            self.final("Nice to meet you.", "original"),
            self.final("Rád vás poznávám.", "translation"),
        ]}))
        assert s.finalized_original() == "Nice to meet you."
        assert s.finalized_translation() == "Rád vás poznávám."
        assert s.finalized_untranslated() == ""

    def test_an_unknown_status_is_refused_rather_than_guessed(self):
        s = self.session()
        with pytest.raises(ValueError):
            s._on_message(None, json.dumps(
                {"tokens": [{"text": "?", "translation_status": "sideways"}]}))

    def test_an_untagged_session_puts_everything_in_the_spoken_text(self):
        s = SonioxStreamingSession(api_key="k")
        s._on_message(None, json.dumps({"tokens": [{"text": "hello", "is_final": True}]}))
        assert s.get_final_text() == "hello"
        assert s.finalized_untranslated() == ""
