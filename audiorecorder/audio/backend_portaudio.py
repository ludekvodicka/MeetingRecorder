"""macOS and Linux capture through PortAudio.

Neither platform exposes a loopback of its own, so the system track is an ordinary input
device the user routes their output into:

* Linux - the ``.monitor`` source that PulseAudio or pipewire-pulse creates for every
  output. It appears in the input list like any microphone.
* macOS - a virtual device such as BlackHole, with a multi-output device sending the sound
  to both the speakers and the virtual device.

When no system source is configured, only the microphone is recorded and ``stop`` returns
None for the system track.
"""

import os
import threading
import time

import numpy as np
import sounddevice as sd
import soundfile as sf

from audiorecorder.audio.backend import (
    SYSTEM_SOURCE_NONE,
    AudioDevice,
    CaptureBackend,
    CaptureError,
    rms_level,
)

BLOCK_SIZE = 1024


class PortAudioBackend(CaptureBackend):
    def __init__(self):
        super().__init__()
        self._system_stream = None
        self._mic_stream = None
        self._system_writer = None
        self._mic_writer = None
        self._system_path = None
        self._mic_path = None
        self._lock = threading.Lock()

    def _input_devices(self):
        devices = []
        for info in sd.query_devices():
            if info["max_input_channels"] <= 0:
                continue
            devices.append(AudioDevice(
                name=info["name"],
                sample_rate=int(info["default_samplerate"]),
                channels=int(info["max_input_channels"]),
            ))
        return devices

    # Any input can serve as either track here, so both lists are the same. The monitor
    # sources the user wants for the system track are inputs like any other.
    def list_system_sources(self):
        return self._input_devices()

    def list_microphones(self):
        return self._input_devices()

    def _index_by_name(self, name):
        for index, info in enumerate(sd.query_devices()):
            if info["max_input_channels"] > 0 and info["name"] == name:
                return index
        return None

    def _resolve(self, name, what):
        if name:
            index = self._index_by_name(name)
            if index is not None:
                return index, sd.query_devices(index)
        try:
            index = sd.default.device[0]
            if index is None or index < 0:
                raise CaptureError(f"No default {what} available.")
            return index, sd.query_devices(index)
        except (sd.PortAudioError, ValueError, TypeError) as err:
            raise CaptureError(f"No {what} available: {err}") from err

    def start(self, tmp_dir, system_source, mic_source):
        if self._is_recording:
            return

        # None means "the platform default", and this platform has no default system
        # source, so both None and the explicit choice mean microphone only.
        record_system = bool(system_source) and system_source != SYSTEM_SOURCE_NONE
        stamp = int(time.time())

        try:
            if record_system:
                index, info = self._resolve(system_source, "system audio source")
                channels = min(2, int(info["max_input_channels"]))
                rate = int(info["default_samplerate"])
                self._active_system_source = info["name"]
                self._system_path = os.path.join(tmp_dir, f"_rec_system_{stamp}.wav")
                self._system_writer = sf.SoundFile(
                    self._system_path, mode="w", samplerate=rate,
                    channels=channels, format="WAV", subtype="FLOAT",
                )
                self._system_stream = sd.InputStream(
                    device=index, channels=channels, samplerate=rate, dtype="float32",
                    blocksize=BLOCK_SIZE, callback=self._system_callback,
                )
                self._prepare_tap(rate)
            else:
                self._active_system_source = None
                self._system_path = None

            mic_index, mic_info = self._resolve(mic_source, "microphone")
            mic_rate = int(mic_info["default_samplerate"])
            self._active_mic_source = mic_info["name"]
            self._mic_path = os.path.join(tmp_dir, f"_rec_mic_{stamp}.wav")
            self._mic_writer = sf.SoundFile(
                self._mic_path, mode="w", samplerate=mic_rate,
                channels=1, format="WAV", subtype="FLOAT",
            )
            self._mic_stream = sd.InputStream(
                device=mic_index, channels=1, samplerate=mic_rate, dtype="float32",
                blocksize=BLOCK_SIZE, callback=self._mic_callback,
            )

            if self._system_stream is not None:
                self._system_stream.start()
            self._mic_stream.start()
        except CaptureError:
            self._discard_partial_start()
            raise
        except Exception as err:
            self._discard_partial_start()
            raise CaptureError(f"Failed to start recording: {err}") from err

        self._is_recording = True

    def _discard_partial_start(self):
        for attr in ("_system_stream", "_mic_stream"):
            stream = getattr(self, attr)
            if stream is not None:
                stream.close()
                setattr(self, attr, None)
        for attr in ("_system_writer", "_mic_writer"):
            writer = getattr(self, attr)
            if writer is not None and not writer.closed:
                writer.close()
            setattr(self, attr, None)

    def _system_callback(self, indata, frames, time_info, status):
        data = np.asarray(indata, dtype=np.float32)
        with self._lock:
            if self._system_writer and not self._system_writer.closed:
                self._system_writer.write(data)
        if self._system_level_callback:
            self._system_level_callback(rms_level(data))
        self._feed_tap(data)

    def _mic_callback(self, indata, frames, time_info, status):
        data = np.asarray(indata, dtype=np.float32)
        if self._mic_muted:
            data = np.zeros_like(data)
        with self._lock:
            if self._mic_writer and not self._mic_writer.closed:
                self._mic_writer.write(data)
        if self._mic_level_callback:
            self._mic_level_callback(rms_level(data))

    def stop(self):
        if not self._is_recording:
            return None, None
        self._is_recording = False

        for attr in ("_system_stream", "_mic_stream"):
            stream = getattr(self, attr)
            if stream is not None:
                stream.stop()
                stream.close()
                setattr(self, attr, None)

        with self._lock:
            for attr in ("_system_writer", "_mic_writer"):
                writer = getattr(self, attr)
                if writer is not None and not writer.closed:
                    writer.close()

        return self._system_path, self._mic_path

    def close(self):
        self.stop()
