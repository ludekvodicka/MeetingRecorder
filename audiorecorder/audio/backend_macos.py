"""macOS capture through ScreenCaptureKit with virtual-device compatibility."""

import os
import platform
import time

import soundfile as sf

from audiorecorder.audio.backend import CaptureError
from audiorecorder.audio.backend_portaudio import PortAudioBackend

NATIVE_SYSTEM_SOURCE = "System Audio (native)"


def native_system_audio_supported(version=None):
    """ScreenCaptureKit added system-audio output in macOS 13."""
    if version is None:
        version = platform.mac_ver()[0]
    try:
        return int(version.split(".", 1)[0]) >= 13
    except (AttributeError, ValueError):
        return False


class MacOSBackend(PortAudioBackend):
    """Use native system capture by default and explicit routed inputs on request."""

    def _native_system_callback(self, data):
        self._system_callback(data, len(data), None, None)

    def _open_native_system(self, tmp_dir, stamp):
        from audiorecorder.audio.macos_screen_capture import (
            CAPTURE_CHANNELS,
            CAPTURE_RATE,
            ScreenCaptureAudioStream,
        )

        self._active_system_source = NATIVE_SYSTEM_SOURCE
        self._system_path = os.path.join(tmp_dir, f"_rec_system_{stamp}.wav")
        self._system_writer = sf.SoundFile(
            self._system_path,
            mode="w",
            samplerate=CAPTURE_RATE,
            channels=CAPTURE_CHANNELS,
            format="WAV",
            subtype="FLOAT",
        )
        self._system_stream = ScreenCaptureAudioStream(
            self._native_system_callback,
            self._report_error,
        )
        self._prepare_tap(CAPTURE_RATE)

    def start(self, tmp_dir, system_source, mic_source):
        if system_source is not None or not native_system_audio_supported():
            return super().start(tmp_dir, system_source, mic_source)
        if self._is_recording:
            return

        stamp = int(time.time())
        try:
            self._open_native_system(tmp_dir, stamp)
            self._open_microphone(tmp_dir, mic_source, stamp)
            self._start_open_streams()
        except Exception as err:
            self._discard_partial_start()
            if isinstance(err, CaptureError):
                raise
            raise CaptureError(f"Failed to start recording: {err}") from err
        self._is_recording = True
