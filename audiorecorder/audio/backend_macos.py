"""macOS capture through ScreenCaptureKit with virtual-device compatibility."""

import logging
import os
import platform
import time

import sounddevice as sd
import soundfile as sf

from audiorecorder.audio.backend import CaptureError
from audiorecorder.audio.backend_portaudio import PortAudioBackend
from audiorecorder.audio.device_selection import macos_builtin_microphones

NATIVE_SYSTEM_SOURCE = "System Audio (native)"
log = logging.getLogger(__name__)


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

    def _open_microphone(self, tmp_dir, mic_source, stamp):
        preferred = self._resolve_microphone(mic_source)
        try:
            self._open_microphone_device(tmp_dir, preferred, stamp)
            return
        except sd.PortAudioError as error:
            preferred_error = error
            log.warning(
                "microphone %r could not open; trying the built-in macOS microphone: %s",
                preferred[1]["name"],
                preferred_error,
            )

        try:
            fallbacks = macos_builtin_microphones(sd, excluded_indices={preferred[0]})
        except Exception:
            log.exception("could not refresh microphones after the preferred input failed")
            raise preferred_error from None

        for fallback in fallbacks:
            try:
                self._open_microphone_device(tmp_dir, fallback, stamp)
                log.warning(
                    "recording with fallback microphone %r because %r was unavailable",
                    fallback[1]["name"],
                    preferred[1]["name"],
                )
                return
            except sd.PortAudioError as fallback_error:
                log.warning(
                    "fallback microphone %r could not open: %s",
                    fallback[1]["name"],
                    fallback_error,
                )

        raise preferred_error

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
