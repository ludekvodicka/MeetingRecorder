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
                 on_preview=None):
        self.api_key = api_key
        self.language_hints = language_hints or []
        self.sample_rate = sample_rate
        self.translate_to = translate_to
        self.on_preview = on_preview
        self.ws = None

        self._final_tokens = []
        self._current_provisional = ""
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
        if tokens and self.first_token_time is None:
            self.first_token_time = time.perf_counter() - self.start_time

        provisional_parts = []
        for tok in tokens:
            text = tok.get("text", "")
            is_final = tok.get("is_final", False)
            if self.translate_to and tok.get("translation_status") == "original":
                continue
            if is_final:
                self._final_tokens.append(tok)
            else:
                provisional_parts.append(text)

        new_provisional = "".join(provisional_parts)
        if new_provisional != self._current_provisional:
            self._current_provisional = new_provisional
            if self.on_preview:
                final_text = "".join(t.get("text", "") for t in self._final_tokens)
                preview = (final_text + new_provisional).strip()
                self.on_preview(preview)

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
        if self.translate_to:
            text = "".join(
                t.get("text", "") for t in self._final_tokens
                if t.get("translation_status") != "original"
            )
        else:
            text = "".join(t.get("text", "") for t in self._final_tokens)
        text += self._current_provisional
        return text.strip()
