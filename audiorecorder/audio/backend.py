"""Audio capture interface.

Recording always has two tracks: the system output and the microphone. How the system
output is obtained differs per platform, and that is the only thing the two backends
disagree about:

* Windows records it directly through the WASAPI loopback of the default output device.
* macOS and Linux have no loopback of their own, so the system track is an ordinary input
  device that the user routes their output into: a PulseAudio or PipeWire ``.monitor``
  source on Linux, a virtual device such as BlackHole on macOS. Without one, recording is
  microphone only, and the application says so rather than pretending.

The user interface talks to this interface only and never branches on the platform.
"""

import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np

# An explicit "record the microphone only". Distinct from None, which means "whatever this
# platform offers by default": the default output loopback on Windows, nothing anywhere else.
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


class CaptureBackend(ABC):
    def __init__(self):
        self._system_level_callback = None
        self._mic_level_callback = None
        self._mic_muted = False
        self._is_recording = False
        self._active_system_source = None
        self._active_mic_source = None

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

    def set_level_callbacks(self, system_callback, mic_callback):
        self._system_level_callback = system_callback
        self._mic_level_callback = mic_callback

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
    rms = float(np.sqrt(np.mean(data ** 2)))
    return min(1.0, rms * 10)


def create_backend():
    if sys.platform == "win32":
        from audiorecorder.audio.backend_wasapi import WasapiBackend

        return WasapiBackend()
    elif sys.platform in ("darwin", "linux"):
        from audiorecorder.audio.backend_portaudio import PortAudioBackend

        return PortAudioBackend()
    else:
        raise CaptureError(f"Unsupported platform: {sys.platform}")
