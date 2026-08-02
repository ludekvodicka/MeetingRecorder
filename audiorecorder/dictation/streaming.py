import json
import threading
import time

import websocket

SONIOX_WS_URL = "wss://stt-rt.soniox.com/transcribe-websocket"


class SonioxStreamingSession:
    """Manages a single WebSocket session for one dictation recording."""

    def __init__(self, api_key, language_hints=None, sample_rate=16000, translate_to=None,
                 on_preview=None):
        self.api_key = api_key
        self.language_hints = language_hints or ["cs"]
        self.sample_rate = sample_rate
        self.translate_to = translate_to
        self.on_preview = on_preview
        self.ws = None

        self._final_tokens = []
        self._current_provisional = ""
        self._finished = threading.Event()
        self._error = None

        self.first_token_time = None
        self.start_time = None

    def _on_open(self, ws):
        config = {
            "api_key": self.api_key,
            "model": "stt-rt-v4",
            "language_hints": self.language_hints,
            "enable_language_identification": False,
            "enable_speaker_diarization": False,
            "enable_endpoint_detection": False,
            "audio_format": "pcm_s16le",
            "sample_rate": self.sample_rate,
            "num_channels": 1,
        }
        if self.translate_to:
            config["translation"] = {"type": "one_way", "target_language": self.translate_to}
        ws.send(json.dumps(config))

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
        except Exception:
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
        self._error = str(error)
        self._finished.set()

    def _on_close(self, ws, code, msg):
        self._finished.set()

    def connect(self):
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
        time.sleep(0.3)

    def send_audio(self, pcm_bytes):
        if self.ws and self.ws.sock and self.ws.sock.connected:
            self.ws.send(pcm_bytes, opcode=websocket.ABNF.OPCODE_BINARY)

    def finish(self, timeout=10):
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
