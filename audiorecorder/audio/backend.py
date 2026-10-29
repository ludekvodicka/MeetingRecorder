"""Audio capture interface.

Recording always has two tracks: the system output and the microphone. How the system
output is obtained differs per platform, and that is the only thing the two backends
disagree about:

* Windows records it directly through the WASAPI loopback of the default output device.
* macOS records it directly through ScreenCaptureKit. A virtual input such as BlackHole
  remains available as an explicitly selected compatibility path.
* Linux records an ordinary PulseAudio or PipeWire ``.monitor`` input. Without one,
  recording is microphone only, and the application says so rather than pretending.

The user interface talks to this interface only and never branches on the platform.
"""

import logging
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np

from audiorecorder.audio.resample import Downsampler

log = logging.getLogger(__name__)

# What the Soniox realtime API wants, and therefore what the tap delivers.
SUBTITLE_RATE = 16000

# An explicit "record the microphone only". Distinct from None, which means "whatever this
# platform offers by default": the default output loopback on Windows, native capture on
# macOS, and nothing on Linux.
SYSTEM_SOURCE_NONE = ""


@dataclass(frozen=True)
class AudioDevice:
    """A selectable input.

    Identified by name, never by index: PortAudio and WASAPI indices are renumbered when
    devices come and go, so an index stored in the settings points at a different device
    after the next reboot.
    """

    name: str
    sample_rate: int
    channels: int


class CaptureError(RuntimeError):
    """Recording could not start. The message is shown to the user as it is."""


class CaptureUnresolved(CaptureError):
    """Native resource ownership is unresolved; do not start another capture."""


class CaptureBackend(ABC):
    def __init__(self):
        self._system_level_callback = None
        self._mic_level_callback = None
        self._error_callback = None
        self.frame_counts = {"system": 0, "mic": 0}
        self._write_failure_reported = False
        self._mic_muted = False
        self._is_recording = False
        self._active_system_source = None
        self._active_mic_source = None
        self._system_tap = None
        self._mic_tap = None
        self._downsampler = None
        self._mic_downsampler = None

    @abstractmethod
    def list_system_sources(self):
        """Devices offerable as the system track. May be empty."""

    @abstractmethod
    def list_microphones(self):
        """Devices offerable as the microphone."""

    @abstractmethod
    def start(self, tmp_dir, system_source, mic_source):
        """Open both streams and write to temporary WAV files in tmp_dir.

        ``system_source`` and ``mic_source`` are device names from the settings,
        ``SYSTEM_SOURCE_NONE`` for microphone only, or None for the platform default.
        Raises CaptureError when a stream cannot be opened.
        """

    @abstractmethod
    def stop(self):
        """Close the streams and return ``(system_wav_path_or_None, mic_wav_path)``."""

    @abstractmethod
    def close(self):
        """Release the audio library. The instance is unusable afterwards."""

    def resolve_sources(self, system_source, mic_source):
        """Resolve names without opening audio streams for unattended-start verification."""
        raise CaptureError("This backend does not support unattended source verification")

    def _write_samples(self, source, data):
        """Shared writer mechanics; callbacks never wait on UI or network work."""
        try:
            with self._lock:
                writer = getattr(self, f"_{source}_writer")
                if writer is not None and not writer.closed:
                    writer.write(data)
                    self.frame_counts[source] += len(data)
        except Exception:
            if not self._write_failure_reported:
                self._write_failure_reported = True
                self._report_error(f"Could not write {source} audio; source files retained")

    def set_level_callbacks(self, system_callback, mic_callback):
        self._system_level_callback = system_callback
        self._mic_level_callback = mic_callback

    def set_error_callback(self, callback):
        """Receive fatal capture errors that happen after a stream has started."""
        self._error_callback = callback

    def _report_error(self, message):
        """Notify the UI without allowing its callback to break an audio thread."""
        if self._error_callback is None:
            return
        try:
            self._error_callback(message)
        except Exception:
            log.exception("the capture error callback failed")

    def set_system_tap(self, callback):
        """Receive a copy of the system track as mono 16 kHz int16 bytes, None to stop.

        Called from the audio thread, so the callback must return immediately. Anything that
        waits there, a socket above all, is paid for in recorded audio.
        """
        self._system_tap = callback

    def set_mic_tap(self, callback):
        """Receive a copy of the microphone as mono 16 kHz int16 bytes, None to stop.

        The same audio-thread constraints as :meth:`set_system_tap` apply. The microphone
        remains an independent tap so a live transcript can label it as the local user
        without asking diarization to rediscover information the recorder already knows.
        """
        self._mic_tap = callback

    def _prepare_tap(self, source_rate):
        """Backends call this when the system stream opens, with its real sample rate."""
        self._downsampler = Downsampler(source_rate, SUBTITLE_RATE)

    def _prepare_mic_tap(self, source_rate):
        """Backends call this when the microphone opens, with its real sample rate."""
        self._mic_downsampler = Downsampler(source_rate, SUBTITLE_RATE)

    def _feed_tap(self, block):
        """Backends call this from the audio callback, after the recording is written.

        Never raises: a subtitle fault is not worth a lost call, so the tap switches itself
        off and the recording carries on without it.
        """
        self._feed_audio_tap(block, "_system_tap", "_downsampler", "system")

    def _feed_mic_tap(self, block):
        """Feed the local-speaker tap without risking the recording callback."""
        self._feed_audio_tap(block, "_mic_tap", "_mic_downsampler", "microphone")

    def _feed_audio_tap(self, block, callback_attr, downsampler_attr, label):
        callback = getattr(self, callback_attr)
        downsampler = getattr(self, downsampler_attr)
        if callback is None or downsampler is None:
            return
        try:
            chunk = downsampler.feed(block)
            if chunk:
                callback(chunk)
        except Exception:
            log.exception("the %s transcript tap failed, dropping it for this recording", label)
            setattr(self, callback_attr, None)

    @property
    def is_recording(self):
        return self._is_recording

    @property
    def active_system_source(self):
        """Name of the device actually recording the system track, None when there is none."""
        return self._active_system_source

    @property
    def active_mic_source(self):
        return self._active_mic_source

    @property
    def mic_muted(self):
        return self._mic_muted

    @mic_muted.setter
    def mic_muted(self, value):
        self._mic_muted = value


def rms_level(data):
    """Level for the meters, 0 to 1. Shared so both backends drive the bars identically."""
    rms = float(np.sqrt(np.mean(data**2)))
    return min(1.0, rms * 10)


def create_backend(platform_name=None):
    """Create the capture backend for ``platform_name`` or the running platform.

    The optional name keeps platform routing testable without importing a foreign native
    audio library into the current process.
    """
    if platform_name is None:
        platform_name = sys.platform
    if platform_name == "win32":
        from audiorecorder.audio.backend_wasapi import WasapiBackend

        return WasapiBackend()
    elif platform_name == "darwin":
        from audiorecorder.audio.backend_macos import MacOSBackend

        return MacOSBackend()
    elif platform_name == "linux":
        from audiorecorder.audio.backend_portaudio import PortAudioBackend

        return PortAudioBackend()
    else:
        raise CaptureError(f"Unsupported platform: {platform_name}")
