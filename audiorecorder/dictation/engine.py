import logging
import queue
import threading

import numpy as np
import sounddevice as sd
from PyQt6.QtCore import QObject, pyqtSignal

from audiorecorder.dictation.hotkeys import (
    HotkeyListener,
    PushToTalkStateMachine,
    held_modifiers,
)
from audiorecorder.dictation.output import paste_text
from audiorecorder.dictation.streaming import SonioxStreamingSession

log = logging.getLogger(__name__)


def _same_device(candidate, configured):
    """Same physical device, allowing for a name one API truncated and the other did not."""
    return (candidate == configured
            or candidate.startswith(configured)
            or configured.startswith(candidate))


class DictationEngine(QObject):
    state_changed = pyqtSignal(str)
    level_changed = pyqtSignal(float)
    text_pasted = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, api_key, language="cs", translate_target="en",
                 mic_source_name=None, sample_rate=16000):
        super().__init__()
        self.api_key = api_key
        self.language = language
        self.translate_target = translate_target
        self.mic_source_name = mic_source_name
        self.sample_rate = sample_rate

        self._running = False
        self._stream = None
        self._audio_queue = queue.Queue()
        self._is_recording = False
        self._active_label = None
        self._recording_started = threading.Event()
        self._recording_done = threading.Event()
        self._machine = PushToTalkStateMachine(
            on_activate=self._begin_capture, on_release=self._end_capture,
            modifier_probe=held_modifiers,
        )
        self._listener = HotkeyListener(self._machine)

    def language_hints(self):
        """The configured language as a hint, or none at all.

        "auto" is a setting of ours, not a language. Sending it verbatim makes Soniox
        reject the whole request with "Invalid language hint" and close the socket, which
        looked like the dictation silently doing nothing.
        """
        if not self.language or self.language == "auto":
            return []
        return [self.language]

    def _find_input_device(self):
        """Index of the configured microphone, else the first available input.

        Settings store the device name, so it can be matched directly. WASAPI and
        PortAudio sometimes truncate the same device name differently, which is why a
        prefix on either side counts as a match.
        """
        if self.mic_source_name:
            for i, d in enumerate(sd.query_devices()):
                if d["max_input_channels"] > 0 and _same_device(d["name"], self.mic_source_name):
                    return i
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                return i
        raise RuntimeError("No input device found")

    def start(self):
        if self._running:
            return

        # Raises HotkeyUnsupportedError before anything is opened, so a platform without
        # global hotkeys fails on the button click and not silently later.
        self._listener.start()
        self._running = True

        sd_device = self._find_input_device()
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            callback=self._audio_callback,
            blocksize=1024,
            device=sd_device,
        )
        self._stream.start()

        self._worker = threading.Thread(target=self._dictation_loop, daemon=True)
        self._worker.start()

        self.state_changed.emit("idle")

    def stop(self):
        if not self._running:
            return

        self._running = False
        self._listener.stop()

        self._recording_started.set()
        self._recording_done.set()

        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        self.state_changed.emit("off")

    def _audio_callback(self, indata, frames, time_info, status):
        if self._is_recording:
            self._audio_queue.put(indata.copy())

    def _begin_capture(self, label):
        """The hotkey went down. Called from the listener thread."""
        log.debug("hotkey down, capture begins (%s)", label)
        self._active_label = label
        self._recording_done.clear()
        self._audio_queue = queue.Queue()
        self._is_recording = True
        self._recording_started.set()

    def _end_capture(self):
        """The hotkey came up. Called from the listener thread."""
        log.debug("hotkey up, capture ends")
        self._is_recording = False
        self._recording_done.set()

    def _dictation_loop(self):
        while self._running:
            self._recording_started.clear()
            self._recording_done.clear()
            self._is_recording = False

            self._recording_started.wait()
            if not self._running:
                break

            label = self._active_label
            translate_to = self.translate_target if label == "translate" else None

            session = SonioxStreamingSession(
                api_key=self.api_key,
                language_hints=self.language_hints(),
                sample_rate=self.sample_rate,
                translate_to=translate_to,
                on_preview=lambda text: self.state_changed.emit(f"preview:{text}"),
            )

            try:
                session.connect()
            except Exception as e:
                self.error_occurred.emit(f"WebSocket connect failed: {e}")
                continue

            self.state_changed.emit("recording")
            start_time = __import__("time").perf_counter()
            log.debug("streaming to Soniox, label=%s", label)

            while not self._recording_done.is_set() or not self._audio_queue.empty():
                try:
                    chunk = self._audio_queue.get(timeout=0.05)
                    int16 = (chunk.flatten() * 32767).astype(np.int16)
                    pcm_bytes = int16.tobytes()

                    arr = chunk.flatten()
                    rms = float(np.sqrt(np.mean(arr ** 2)))
                    level = min(1.0, (rms ** 0.5) * 2.2)
                    self.level_changed.emit(level)

                    session.send_audio(pcm_bytes)
                except queue.Empty:
                    continue

            duration = __import__("time").perf_counter() - start_time
            log.debug("recording finished after %.2fs, %d chunks still queued",
                      duration, self._audio_queue.qsize())
            self.level_changed.emit(0.0)
            self.state_changed.emit("finishing")

            def post_process(s, dur, lbl):
                try:
                    if dur < 0.3:
                        log.debug("too short (%.2fs), nothing will be pasted", dur)
                        s.finish(timeout=2)
                        # Saying so beats a hotkey that silently does nothing when the
                        # user taps it instead of holding it.
                        self.state_changed.emit("notice:Too short, hold Ctrl+Space while "
                                                "you speak")
                        return
                    s.finish(timeout=10)
                    text = s.get_final_text()
                    log.debug("soniox returned %d characters, %d final tokens",
                              len(text), len(s._final_tokens))
                    if not text:
                        log.warning("nothing to paste: the stream produced no text")
                        self.state_changed.emit("notice:Nothing was recognized")
                        return
                    log.debug("pasting %d characters", len(text))
                    paste_text(text)
                    log.debug("paste done")
                    tag = f"{lbl}→{self.translate_target}" if lbl == "translate" else lbl
                    self.text_pasted.emit(f"[{tag}] {text}")
                    self.state_changed.emit("idle")
                except Exception as ex:
                    log.exception("dictation post-processing failed")
                    self.error_occurred.emit(str(ex))
                    self.state_changed.emit("idle")

            threading.Thread(
                target=post_process, args=(session, duration, label), daemon=True,
            ).start()
