"""Live subtitles for the far side of a call.

A copy of the system audio arrives from the capture tap and is streamed to the Soniox
realtime API with one-way translation. What comes back is shown as it is spoken and kept
for the sidecar written when the recording stops.

Two threads meet here. `feed` is called from the audio callback and only puts bytes on a
queue, because anything slower there is paid for in recorded audio. A worker owns the
session, does the sending, and reopens it if it dies before the recording does.
"""

import contextlib
import logging
import queue
import threading
import time

from PyQt6.QtCore import QObject, pyqtSignal

from audiorecorder.audio.backend import SUBTITLE_RATE
from audiorecorder.dictation.streaming import SonioxStreamingSession

log = logging.getLogger(__name__)


def _clean_languages(languages):
    """Real language codes only, in order, without repeats."""
    seen = []
    for language in languages or []:
        if language in (None, "", "auto") or language in seen:
            continue
        seen.append(language)
    return seen


def _now():
    """Wall clock, because that is what the user sees on the call."""
    return time.strftime("%H:%M:%S")

# A dropped session is reopened with a widening gap, then given up on. Speech during a gap
# is missing from the subtitles and never from the recording.
RECONNECT_DELAYS = (1, 2, 4)

# Enough for a few seconds of audio. If the network stalls for longer than that, dropping
# the oldest audio keeps the subtitles near the present rather than falling ever further
# behind the call.
QUEUE_LIMIT = 200


class SubtitleEngine(QObject):
    """Owns one realtime session for the length of a recording."""

    line_updated = pyqtSignal(str, str)         # the sentence being spoken, both languages
    # A settled line: when it was said, then both languages.
    line_finalized = pyqtSignal(str, str, str)
    notice = pyqtSignal(str)                 # reconnecting, or giving up, and why
    error_occurred = pyqtSignal(str)

    def __init__(self, api_key, translate_to, source_languages=None,
                 sample_rate=SUBTITLE_RATE):
        super().__init__()
        if not api_key:
            raise ValueError("Subtitles need a Soniox API key.")
        self._api_key = api_key
        # The language to translate INTO, which is the "Translate to" setting. Not the
        # primary language: that one is what the batch transcription aims at, and reading
        # it here made the subtitles translate an English call into English.
        self._translate_to = None if translate_to in (None, "", "auto") else translate_to
        # The languages the call can be in. Given the whole world to choose from, the
        # model guesses from the first seconds and gets it wrong on short utterances:
        # English came back as Dutch, and the translation was of the mistake. Naming the
        # two candidates narrows it to a choice it can actually make.
        self._source_languages = _clean_languages(source_languages)
        self._sample_rate = sample_rate

        self._audio = queue.Queue(maxsize=QUEUE_LIMIT)
        self._running = False
        self._worker = None
        self._session = None
        self._pairs = []
        self._lock = threading.Lock()
        self._sent_original = 0
        self._sent_translation = 0
        self._sent_plain = 0
        self._held_original = ""
        self._held_translation = ""

    @property
    def translating(self):
        return self._translate_to is not None

    @property
    def target_language(self):
        return self._translate_to

    @property
    def source_languages(self):
        return list(self._source_languages)

    def transcript(self):
        """The finalized pairs, for the sidecar. Safe to call after stop()."""
        with self._lock:
            return list(self._pairs)

    def start(self):
        if self._running:
            return
        self._running = True
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def feed(self, pcm_bytes):
        """From the audio thread. Never blocks and never raises."""
        if not self._running:
            return
        try:
            self._audio.put_nowait(pcm_bytes)
        except queue.Full:
            # Falling behind is better than growing without limit. Drop the oldest.
            try:
                self._audio.get_nowait()
                self._audio.put_nowait(pcm_bytes)
            except (queue.Empty, queue.Full):
                pass

    def stop(self, timeout=5):
        self._running = False
        # Never a blocking put. Once the engine has given up reconnecting, nothing drains
        # the queue, and waiting for room in it here would hang the interface at the end of
        # every recording. The sentinel only wakes the worker sooner; the get() timeout
        # ends the loop regardless.
        with contextlib.suppress(queue.Full):
            self._audio.put_nowait(None)
        if self._worker is not None:
            self._worker.join(timeout=timeout)
            self._worker = None

    def _run(self):
        delays = list(RECONNECT_DELAYS)
        while self._running:
            try:
                self._session = self._open_session()
            except Exception as err:
                log.warning("subtitle session could not be opened: %s", err)
                self.error_occurred.emit(str(err))
                self._session = None
            else:
                self._pump()

            self._close_session()
            if not self._running:
                return
            if not delays:
                self.notice.emit("Subtitles stopped. The recording is not affected.")
                return
            wait = delays.pop(0)
            self.notice.emit(f"Subtitles reconnecting in {wait}s")
            time.sleep(wait)

    def _open_session(self):
        session = SonioxStreamingSession(
            api_key=self._api_key,
            language_hints=list(self._source_languages),
            sample_rate=self._sample_rate,
            translate_to=self._translate_to,
            on_pair=self._on_pair,
        )
        session.connect()
        return session

    def _pump(self):
        """Send audio until the recording stops or the session dies under us."""
        while self._running and not self._session.is_finished():
            try:
                chunk = self._audio.get(timeout=0.2)
            except queue.Empty:
                continue
            if chunk is None:           # the stop() sentinel
                return
            try:
                self._session.send_audio(chunk)
            except Exception as err:
                log.warning("subtitle session dropped while sending: %s", err)
                return

    def _close_session(self):
        if self._session is None:
            self._flush_held()
            return
        try:
            self._session.finish(timeout=5)
        except Exception as err:
            # A session that ends badly has already given us whatever it recognised.
            log.debug("subtitle session closed with %s", err)
        self._session = None
        self._sent_original = 0
        self._sent_translation = 0
        self._sent_plain = 0
        self._flush_held()

    def _on_pair(self, original, translation):
        """From the socket thread, whenever the recognised text changes."""
        session = self._session
        if session is None:
            return
        settled_original = session.finalized_original()
        settled_translation = session.finalized_translation()
        settled_plain = session.finalized_untranslated()

        fresh_original = settled_original[self._sent_original:]
        fresh_translation = settled_translation[self._sent_translation:]
        fresh_plain = settled_plain[self._sent_plain:]
        self._sent_original = len(settled_original)
        self._sent_translation = len(settled_translation)
        self._sent_plain = len(settled_plain)

        complete = []
        with self._lock:
            # Speech already in the target language is a finished line on its own and must
            # not wait for a partner that is never coming: holding it paired a Czech
            # sentence with the English sentence spoken after it.
            if fresh_plain.strip():
                complete.append((_now(), "", fresh_plain.strip()))

            # A sentence and its translation are settled in separate messages, the original
            # first. Emitting each arrival on its own produced lines with one half filled
            # and the other blank, alternating languages instead of pairing them.
            self._held_original += fresh_original
            self._held_translation += fresh_translation
            has_original = bool(self._held_original.strip())
            has_translation = bool(self._held_translation.strip())
            if has_original and (has_translation or not self.translating):
                complete.append((_now(), self._held_original.strip(),
                                 self._held_translation.strip()))
                self._held_original = ""
                self._held_translation = ""
            self._pairs.extend(complete)

        for line in complete:
            self.line_finalized.emit(*line)

        pending_original = original[len(settled_original):].strip()
        pending_translation = translation[len(settled_translation):].strip()
        pending_plain = session.get_untranslated_text()[len(settled_plain):].strip()
        self.line_updated.emit(pending_original, pending_translation or pending_plain)

    def _flush_held(self):
        """The last sentence of a call may never get its partner. Show it anyway."""
        with self._lock:
            leftover = (_now(), self._held_original.strip(),
                        self._held_translation.strip())
            self._held_original = ""
            self._held_translation = ""
            if not any(leftover[1:]):
                return
            self._pairs.append(leftover)
        self.line_finalized.emit(*leftover)
