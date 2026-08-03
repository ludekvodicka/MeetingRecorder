import itertools
import json
import logging
import threading
import time

import websocket

SONIOX_WS_URL = "wss://stt-rt.soniox.com/transcribe-websocket"

log = logging.getLogger(__name__)
_session_numbers = itertools.count(1)

NORMAL_CLOSURE = 1000


def _bucket_of(token):
    """Which of the three states a token is in.

    A session that is not translating tags nothing at all, and everything it says is
    simply what was spoken.
    """
    status = token.get("translation_status")
    if status == "original":
        return "original"
    if status == "translation":
        return "translation"
    if status in (None, "none"):
        return "untranslated"
    raise ValueError(f"Unknown translation status: {status}")


def is_normal_close(error):
    """True when what came through the error channel is the stream ending as it should.

    websocket-client hands the server's closing frame to the error callback, so the tidy
    end of a finished dictation arrives looking like a failure. Reporting it as one used to
    abort the session before the transcribed text was ever pasted.
    """
    if not isinstance(error, websocket.ABNF):
        return False
    if error.opcode != websocket.ABNF.OPCODE_CLOSE:
        return False
    if len(error.data) < 2:
        return True
    return int.from_bytes(error.data[:2], "big") == NORMAL_CLOSURE


class SonioxStreamingSession:
    """Manages a single WebSocket session for one dictation recording."""

    def __init__(self, api_key, language_hints=None, sample_rate=16000, translate_to=None,
                 on_preview=None, on_pair=None):
        self.api_key = api_key
        self.language_hints = language_hints or []
        self.sample_rate = sample_rate
        self.translate_to = translate_to
        self.on_preview = on_preview
        # Subtitles want both languages as they arrive; dictation only wants the result.
        self.on_pair = on_pair
        self.ws = None

        self._final_tokens = []
        self._provisional_original = ""
        self._provisional_translation = ""
        self._provisional_untranslated = ""
        self._finished = threading.Event()
        self._opened = threading.Event()
        self._error = None

        self.first_token_time = None
        self.start_time = None
        self.number = next(_session_numbers)
        self._audio_frames_sent = 0

    def _on_open(self, ws):
        log.debug("session %d: socket open, sending config", self.number)
        config = {
            "api_key": self.api_key,
            "model": "stt-rt-v4",
            # With no hint to go on, ask the server to work the language out for itself.
            "enable_language_identification": not self.language_hints,
            "enable_speaker_diarization": False,
            "enable_endpoint_detection": False,
            "audio_format": "pcm_s16le",
            "sample_rate": self.sample_rate,
            "num_channels": 1,
        }
        # Sent only when there is one. An empty list is not a valid hint and the server
        # rejects the whole request with "Invalid language hint" and closes the socket.
        if self.language_hints:
            config["language_hints"] = self.language_hints
        if self.translate_to:
            config["translation"] = {"type": "one_way", "target_language": self.translate_to}
        ws.send(json.dumps(config))
        log.debug("session %d: config sent %s", self.number,
                  {k: v for k, v in config.items() if k != "api_key"})
        self._opened.set()

    def _on_message(self, ws, message):
        log.debug("session %d: recv %s", self.number, message[:300])
        try:
            data = json.loads(message)
        except Exception:
            log.warning("session %d: unparseable message", self.number)
            return
        if data.get("error_code") or data.get("error_message"):
            self._error = f"{data.get('error_code')}: {data.get('error_message')}"
            log.error("session %d: server reported %s", self.number, self._error)
            self._finished.set()
            return

        tokens = data.get("tokens", [])
        # start_time is only set by connect(), and a message can arrive without one when
        # the session is driven directly. Losing a latency figure is not worth an exception
        # inside the socket thread.
        if tokens and self.first_token_time is None and self.start_time is not None:
            self.first_token_time = time.perf_counter() - self.start_time

        # Every token is kept, whichever language it is in. A translating session returns
        # both, and subtitles show both; the accessors below decide what each caller sees.
        # Three states, not two. Speech already in the target language comes back tagged
        # "none": it is neither a source waiting for a translation nor a translation of
        # anything. Treating it as a translation paired a Czech sentence with the English
        # one that followed it.
        buckets = {"original": [], "translation": [], "untranslated": []}
        settled_anything = False
        for tok in tokens:
            if tok.get("is_final", False):
                self._final_tokens.append(tok)
                settled_anything = True
            else:
                buckets[_bucket_of(tok)].append(tok.get("text", ""))

        new_original = "".join(buckets["original"])
        new_translation = "".join(buckets["translation"])
        new_untranslated = "".join(buckets["untranslated"])
        provisional_changed = (
            (new_original, new_translation, new_untranslated)
            != (self._provisional_original, self._provisional_translation,
                self._provisional_untranslated))
        self._provisional_original = new_original
        self._provisional_translation = new_translation
        self._provisional_untranslated = new_untranslated

        # Text settling counts as a change too. The last sentence of a stream arrives
        # already final, with no provisional left to differ, and watching only the
        # provisional meant that sentence never reached the listener.
        if settled_anything or provisional_changed:
            if self.on_preview:
                self.on_preview(self.get_final_text())
            if self.on_pair:
                self.on_pair(self.get_original_text(), self.get_translated_text())

        if data.get("finished"):
            self._finished.set()

    def _on_error(self, ws, error):
        log.debug("session %d: error callback %s: %s", self.number, type(error).__name__, error)
        if not is_normal_close(error):
            self._error = str(error)
        self._finished.set()

    def _on_close(self, ws, code, msg):
        log.debug("session %d: closed code=%s msg=%s after %d audio frames",
                  self.number, code, msg, self._audio_frames_sent)
        # Releases connect() when the socket dies before it was ever ready.
        self._opened.set()
        self._finished.set()

    def connect(self, timeout=5):
        """Open the socket and return once the server is ready for audio.

        Waiting for the socket rather than sleeping a fixed moment and hoping: audio sent
        before the connection is up is silently dropped, which ate the first syllable of
        every dictation whenever the network was a little slower than the guess.
        """
        self.start_time = time.perf_counter()
        self.ws = websocket.WebSocketApp(
            SONIOX_WS_URL,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._thread = threading.Thread(target=self.ws.run_forever, daemon=True)
        self._thread.start()

        if not self._opened.wait(timeout=timeout):
            raise TimeoutError(f"Soniox did not accept the connection within {timeout}s")
        log.debug("session %d: ready after %.0fms",
                  self.number, (time.perf_counter() - self.start_time) * 1000)

    def send_audio(self, pcm_bytes):
        if self.ws and self.ws.sock and self.ws.sock.connected:
            self.ws.send(pcm_bytes, opcode=websocket.ABNF.OPCODE_BINARY)
            self._audio_frames_sent += 1
        else:
            self._dropped = getattr(self, '_dropped', 0) + 1
            if self._dropped in (1, 10, 100):
                log.warning("session %d: dropped %d audio frame(s), socket not connected",
                            self.number, self._dropped)

    def finish(self, timeout=10):
        log.debug("session %d: finish() called, %d frames sent, %d dropped",
                  self.number, self._audio_frames_sent, getattr(self, '_dropped', 0))
        if self.ws and self.ws.sock and self.ws.sock.connected:
            self.ws.send("")
        self._finished.wait(timeout=timeout)
        if self.ws:
            self.ws.close()
        if self._error:
            raise RuntimeError(f"Soniox error: {self._error}")

    def get_final_text(self):
        """What the caller asked the session for: the target language when translating.

        Speech already in that language belongs here too. It arrives untagged and is not a
        translation of anything, but it is what the caller wanted to read.
        """
        if self.translate_to:
            return self._assemble(
                ("translation", "untranslated"),
                self._provisional_translation + self._provisional_untranslated)
        return self._assemble(None, self._provisional_original
                              + self._provisional_translation
                              + self._provisional_untranslated)

    def get_original_text(self):
        """The source language, whether or not a translation was asked for."""
        if not self.translate_to:
            return self.get_final_text()
        return self._assemble(("original",), self._provisional_original)

    def get_translated_text(self):
        """The target language. Empty when the session is not translating."""
        if not self.translate_to:
            return ""
        return self._assemble(("translation",), self._provisional_translation)

    def get_untranslated_text(self):
        """Speech that was already in the target language, so has no counterpart."""
        if not self.translate_to:
            return ""
        return self._assemble(("untranslated",), self._provisional_untranslated)

    def finalized_original(self):
        """Only the settled part, so a caller can tell new text from a revision."""
        return self._assemble(("original",) if self.translate_to else None, "")

    def finalized_translation(self):
        return self._assemble(("translation",), "") if self.translate_to else ""

    def finalized_untranslated(self):
        return self._assemble(("untranslated",), "") if self.translate_to else ""

    def is_finished(self):
        return self._finished.is_set()

    def _assemble(self, wanted, provisional):
        """Join the settled tokens of the named buckets, then the provisional tail."""
        if wanted is None:
            finals = self._final_tokens
        else:
            for name in wanted:
                if name not in ("original", "translation", "untranslated"):
                    raise ValueError(f"Unknown token bucket: {name}")
            finals = [t for t in self._final_tokens if _bucket_of(t) in wanted]
        return ("".join(t.get("text", "") for t in finals) + provisional).strip()
