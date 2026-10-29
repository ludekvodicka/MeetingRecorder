"""Routed-input capture through PortAudio.

The system track is an ordinary input device the user routes their output into:

* Linux - the ``.monitor`` source that PulseAudio or pipewire-pulse creates for every
  output. It appears in the input list like any microphone.
* macOS compatibility path - a virtual device such as BlackHole, with a multi-output
  device sending the sound to both the speakers and the virtual device.

When no system source is configured, only the microphone is recorded and ``stop`` returns
None for the system track.
"""

import logging
import os
import threading
import uuid

import numpy as np
import sounddevice as sd
import soundfile as sf

from audiorecorder.audio.backend import (
    SYSTEM_SOURCE_NONE,
    AudioDevice,
    CaptureBackend,
    CaptureError,
    CaptureUnresolved,
    rms_level,
)
from audiorecorder.audio.device_selection import (
    DeviceResolutionError,
    find_input_device,
    resolve_microphone,
)

BLOCK_SIZE = 1024
log = logging.getLogger(__name__)


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
            devices.append(
                AudioDevice(
                    name=info["name"],
                    sample_rate=int(info["default_samplerate"]),
                    channels=int(info["max_input_channels"]),
                )
            )
        return devices

    # Any input can serve as either track here, so both lists are the same. The monitor
    # sources the user wants for the system track are inputs like any other.
    def list_system_sources(self):
        return self._input_devices()

    def list_microphones(self):
        return self._input_devices()

    def _resolve_system_source(self, name):
        """A routed source is optional and must never fall back to a microphone."""
        return find_input_device(sd, name)

    def _resolve_microphone(self, name):
        try:
            return resolve_microphone(sd, name)
        except DeviceResolutionError as err:
            raise CaptureError(f"No microphone available: {err}") from err

    def _open_routed_system(self, tmp_dir, system_device, stamp):
        index, info = system_device
        channels = min(2, int(info["max_input_channels"]))
        rate = int(info["default_samplerate"])
        self._active_system_source = info["name"]
        self._system_path = os.path.join(tmp_dir, f"_rec_system_{stamp}.wav")
        self._system_writer = sf.SoundFile(
            self._system_path,
            mode="w",
            samplerate=rate,
            channels=channels,
            format="WAV",
            subtype="FLOAT",
        )
        self._system_stream = sd.InputStream(
            device=index,
            channels=channels,
            samplerate=rate,
            dtype="float32",
            blocksize=BLOCK_SIZE,
            callback=self._system_callback,
        )
        self._prepare_tap(rate)

    def _open_microphone(self, tmp_dir, mic_source, stamp):
        mic_device = self._resolve_microphone(mic_source)
        self._open_microphone_device(tmp_dir, mic_device, stamp)

    def _open_microphone_device(self, tmp_dir, mic_device, stamp):
        """Open one resolved microphone without leaving partial resources on failure."""
        mic_index, mic_info = mic_device
        mic_rate = int(mic_info["default_samplerate"])
        mic_path = os.path.join(tmp_dir, f"_rec_mic_{stamp}.wav")
        mic_stream = sd.InputStream(
            device=mic_index,
            channels=1,
            samplerate=mic_rate,
            dtype="float32",
            blocksize=BLOCK_SIZE,
            callback=self._mic_callback,
        )
        try:
            mic_writer = sf.SoundFile(
                mic_path,
                mode="w",
                samplerate=mic_rate,
                channels=1,
                format="WAV",
                subtype="FLOAT",
            )
        except Exception:
            try:
                mic_stream.close()
            except Exception:
                log.exception("could not close the microphone after its file failed to open")
            raise

        self._active_mic_source = mic_info["name"]
        self._mic_path = mic_path
        self._mic_writer = mic_writer
        self._mic_stream = mic_stream
        self._prepare_mic_tap(mic_rate)

    def _start_open_streams(self):
        if self._system_stream is not None:
            self._system_stream.start()
        self._mic_stream.start()

    def resolve_sources(self, system_source, mic_source):
        microphone = self._resolve_microphone(mic_source)[1]["name"]
        system = self._resolve_system_source(system_source)[1]["name"] if system_source else None
        return system, microphone

    def start(self, tmp_dir, system_source, mic_source):
        if self._is_recording:
            return

        # None means "the platform default", and this platform has no default system
        # source, so both None and the explicit choice mean microphone only.
        stamp = uuid.uuid4().hex

        try:
            system_device = None
            if system_source and system_source != SYSTEM_SOURCE_NONE:
                system_device = self._resolve_system_source(system_source)
            record_system = system_device is not None
            if record_system:
                self._open_routed_system(tmp_dir, system_device, stamp)
            else:
                self._active_system_source = None
                self._system_path = None

            self._open_microphone(tmp_dir, mic_source, stamp)
            self._start_open_streams()
        except CaptureUnresolved:
            self._is_recording = True
            raise
        except CaptureError as err:
            self._cleanup_failed_start(err)
            raise
        except Exception as err:
            self._cleanup_failed_start(err)
            raise CaptureError(f"Failed to start recording: {err}") from err

        self._is_recording = True

    def _cleanup_failed_start(self, original_error):
        try:
            self._discard_partial_start()
        except CaptureUnresolved as cleanup_error:
            raise CaptureUnresolved(
                f"Failed to start recording: {original_error}; {cleanup_error}"
            ) from cleanup_error

    def _discard_partial_start(self):
        for attr in ("_system_stream", "_mic_stream"):
            stream = getattr(self, attr)
            if stream is not None:
                try:
                    stream.close()
                except Exception as error:
                    self._is_recording = True
                    raise CaptureUnresolved(
                        "Could not release a partially opened audio stream"
                    ) from error
                setattr(self, attr, None)
        for attr in ("_system_writer", "_mic_writer"):
            writer = getattr(self, attr)
            try:
                if writer is not None and not writer.closed:
                    writer.close()
            except Exception as error:
                self._is_recording = True
                raise CaptureUnresolved("Could not close a partial audio file") from error
            setattr(self, attr, None)
        for attr in ("_system_path", "_mic_path"):
            path = getattr(self, attr)
            try:
                if path is not None:
                    os.unlink(path)
            except FileNotFoundError:
                pass
            except OSError:
                log.exception("could not remove a partial audio file")
            setattr(self, attr, None)

    def _system_callback(self, indata, frames, time_info, status):
        data = np.asarray(indata, dtype=np.float32)
        self._write_samples("system", data)
        if self._system_level_callback:
            self._system_level_callback(rms_level(data))
        self._feed_tap(data)

    def _mic_callback(self, indata, frames, time_info, status):
        data = np.asarray(indata, dtype=np.float32)
        if self._mic_muted:
            data = np.zeros_like(data)
        self._write_samples("mic", data)
        if self._mic_level_callback:
            self._mic_level_callback(rms_level(data))
        self._feed_mic_tap(data)

    def stop(self):
        if not self._is_recording:
            return None, None
        for attr in ("_system_stream", "_mic_stream"):
            stream = getattr(self, attr)
            if stream is not None:
                try:
                    stream.stop()
                except Exception:
                    raise CaptureUnresolved("Audio stream did not acknowledge stop") from None
                try:
                    stream.close()
                except Exception:
                    raise CaptureUnresolved("Audio stream did not close") from None
                setattr(self, attr, None)

        with self._lock:
            for attr in ("_system_writer", "_mic_writer"):
                writer = getattr(self, attr)
                try:
                    if writer is not None and not writer.closed:
                        writer.close()
                except Exception:
                    raise CaptureUnresolved("Audio writer could not be finalized") from None

        self._is_recording = False
        return self._system_path, self._mic_path

    def close(self):
        self.stop()
