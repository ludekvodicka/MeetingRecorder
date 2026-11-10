"""Windows capture through WASAPI loopback.

Both streams are opened at the sample rate the loopback device reports, because the
loopback rate is fixed by the audio engine and cannot be negotiated. Microphones that
refuse that rate are reopened at their own, and the encoder resamples afterwards.

A loopback delivers nothing while nothing plays on its device, so the system track would
fall behind the microphone at every pause. The microphone is the clock: when the loopback
has been quiet for PAD_AFTER_SECONDS, silence fills the system track up to the microphone's
position. The system source can switch to another device during the recording; its audio
is converted to the rate and channels the track was opened with.
"""

import os
import threading
import time
import uuid

import numpy as np
import pyaudiowpatch as pyaudio
import soundfile as sf

from audiorecorder.audio.backend import (
    SYSTEM_SOURCE_NONE,
    AudioDevice,
    CaptureBackend,
    CaptureError,
    rms_level,
)
from audiorecorder.audio.resample import Downsampler

CHUNK_SIZE = 1024
# How long the loopback may deliver nothing before silence fills the gap. Longer than a few
# callback periods, so a playing device is never padded between two of its blocks.
PAD_AFTER_SECONDS = 0.25


class WasapiBackend(CaptureBackend):
    def __init__(self):
        super().__init__()
        self._pa = pyaudio.PyAudio()
        self._system_stream = None
        self._mic_stream = None
        self._system_writer = None
        self._mic_writer = None
        self._system_path = None
        self._mic_path = None
        self._system_channels = None
        self._system_rate = None
        self._mic_rate = None
        # Callbacks of a replaced loopback stream may still run while it closes; only the
        # current generation writes.
        self._system_generation = 0
        self._last_system_block = 0.0
        self._lock = threading.Lock()

    def _wasapi_devices(self, loopback):
        devices = []
        try:
            wasapi = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        except OSError:
            return devices
        for i in range(self._pa.get_device_count()):
            info = self._pa.get_device_info_by_index(i)
            if info.get("hostApi") != wasapi["index"]:
                continue
            is_loopback = bool(info.get("isLoopbackDevice", False))
            if is_loopback != loopback:
                continue
            if not loopback and info["maxInputChannels"] <= 0:
                continue
            devices.append(
                AudioDevice(
                    name=info["name"],
                    sample_rate=int(info["defaultSampleRate"]),
                    channels=int(info["maxInputChannels"]),
                )
            )
        return devices

    def list_system_sources(self):
        return self._wasapi_devices(loopback=True)

    def list_microphones(self):
        return self._wasapi_devices(loopback=False)

    def _info_by_name(self, name):
        for i in range(self._pa.get_device_count()):
            info = self._pa.get_device_info_by_index(i)
            if info["name"] == name:
                return info
        return None

    def _resolve_loopback(self, system_source):
        if system_source:
            info = self._info_by_name(system_source)
            if info is not None:
                return info
            # The configured device is gone (unplugged, renamed by a driver update). Fall
            # back to the default rather than refusing to record.
        try:
            return self._pa.get_default_wasapi_loopback()
        except OSError as err:
            candidates = self.list_system_sources()
            if candidates:
                return self._info_by_name(candidates[0].name)
            raise CaptureError(
                "No WASAPI loopback device found, so the system audio cannot be recorded."
            ) from err

    def _resolve_mic(self, mic_source):
        if mic_source:
            info = self._info_by_name(mic_source)
            if info is not None:
                return info
        try:
            return self._pa.get_default_input_device_info()
        except OSError as err:
            raise CaptureError("No microphone available.") from err

    def resolve_sources(self, system_source, mic_source):
        microphone = self._resolve_mic(mic_source)["name"]
        system = (
            self._resolve_loopback(system_source)["name"].replace(" [Loopback]", "")
            if system_source != SYSTEM_SOURCE_NONE
            else None
        )
        return system, microphone

    def start(self, tmp_dir, system_source, mic_source):
        if self._is_recording:
            return

        record_system = system_source != SYSTEM_SOURCE_NONE
        stamp = uuid.uuid4().hex
        self._system_generation = 0
        self._last_system_block = time.monotonic()
        self.capture_stamp = stamp
        rate = None

        if record_system:
            loopback = self._resolve_loopback(system_source)
            rate = int(loopback["defaultSampleRate"])
            self._system_rate = rate
            self._system_channels = int(loopback["maxInputChannels"])
            self._active_system_source = loopback["name"].replace(" [Loopback]", "")
            self._system_path = os.path.join(tmp_dir, f"_rec_system_{stamp}.wav")
            self._system_writer = sf.SoundFile(
                self._system_path,
                mode="w",
                samplerate=rate,
                channels=self._system_channels,
                format="WAV",
                subtype="FLOAT",
            )
            self._prepare_tap(rate)
        else:
            self._active_system_source = None
            self._system_path = None

        mic_info = self._resolve_mic(mic_source)
        self._active_mic_source = mic_info["name"]
        mic_rate = rate or int(mic_info["defaultSampleRate"])
        self._mic_path = os.path.join(tmp_dir, f"_rec_mic_{stamp}.wav")
        self._mic_writer = sf.SoundFile(
            self._mic_path,
            mode="w",
            samplerate=mic_rate,
            channels=1,
            format="WAV",
            subtype="FLOAT",
        )
        self._mic_rate = mic_rate
        self._prepare_mic_tap(mic_rate)

        try:
            if record_system:
                self._system_stream = self._pa.open(
                    format=pyaudio.paFloat32,
                    channels=self._system_channels,
                    rate=rate,
                    input=True,
                    input_device_index=loopback["index"],
                    frames_per_buffer=CHUNK_SIZE,
                    stream_callback=self._system_callback(
                        self._system_generation, self._system_channels, None),
                )
            self._open_mic(mic_info, mic_rate)
        except CaptureError:
            self._discard_partial_start()
            raise
        except Exception as err:
            self._discard_partial_start()
            raise CaptureError(f"Failed to start recording: {err}") from err

        self._is_recording = True

    def _open_mic(self, mic_info, mic_rate):
        try:
            self._mic_stream = self._pa.open(
                format=pyaudio.paFloat32,
                channels=1,
                rate=mic_rate,
                input=True,
                input_device_index=mic_info["index"],
                frames_per_buffer=CHUNK_SIZE,
                stream_callback=self._mic_callback,
            )
            return
        except Exception:
            native_rate = int(mic_info["defaultSampleRate"])
            if native_rate == mic_rate:
                raise

        # The microphone rejected the loopback rate. Reopen the writer at its own rate and
        # let the encoder resample when the two tracks are mixed.
        self._mic_writer.close()
        self._mic_writer = sf.SoundFile(
            self._mic_path,
            mode="w",
            samplerate=native_rate,
            channels=1,
            format="WAV",
            subtype="FLOAT",
        )
        self._mic_rate = native_rate
        self._mic_stream = self._pa.open(
            format=pyaudio.paFloat32,
            channels=1,
            rate=native_rate,
            input=True,
            input_device_index=mic_info["index"],
            frames_per_buffer=CHUNK_SIZE,
            stream_callback=self._mic_callback,
        )
        self._prepare_mic_tap(native_rate)

    def _discard_partial_start(self):
        for stream in (self._system_stream, self._mic_stream):
            if stream is not None:
                stream.close()
        self._system_stream = self._mic_stream = None
        for writer in (self._system_writer, self._mic_writer):
            if writer is not None and not writer.closed:
                writer.close()
        self._system_writer = self._mic_writer = None

    def switch_system_source(self, name):
        if not self._is_recording or self._system_writer is None:
            raise CaptureError("The system track is not being recorded")
        info = self._info_by_name(f"{name} [Loopback]")
        if info is None:
            raise CaptureError(f"No loopback device named {name}")
        rate = int(info["defaultSampleRate"])
        channels = int(info["maxInputChannels"])
        same_format = rate == self._system_rate and channels == self._system_channels
        converter = None if same_format else Downsampler(rate, self._system_rate)
        generation = self._system_generation + 1
        try:
            stream = self._pa.open(
                format=pyaudio.paFloat32,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=info["index"],
                frames_per_buffer=CHUNK_SIZE,
                stream_callback=self._system_callback(generation, channels, converter),
            )
        except Exception as err:
            raise CaptureError(f"Could not open {name}: {err}") from err
        old, self._system_stream = self._system_stream, stream
        self._system_generation = generation
        self._active_system_source = name
        if old is not None:
            old.stop_stream()
            old.close()

    def _system_callback(self, generation, channels, converter):
        """A loopback callback writing in the track's format; a converter changes the rate."""

        def callback(in_data, frame_count, time_info, status):
            if generation != self._system_generation:
                return (None, pyaudio.paContinue)
            data = np.frombuffer(in_data, dtype=np.float32).reshape(-1, channels)
            if converter is not None:
                mono = converter.convert(data)
                data = np.repeat(mono[:, None], self._system_channels, axis=1)
            self._last_system_block = time.monotonic()
            self._write_samples("system", data)
            if self._system_level_callback:
                self._system_level_callback(rms_level(data))
            self._feed_tap(data)
            return (None, pyaudio.paContinue)

        return callback

    def _pad_system(self):
        """Fill the system track with silence up to the microphone while the loopback is quiet."""
        if time.monotonic() - self._last_system_block < PAD_AFTER_SECONDS:
            return
        with self._lock:
            writer = self._system_writer
            if writer is None or writer.closed:
                return
            expected = self.frame_counts["mic"] * self._system_rate // self._mic_rate
            behind = expected - self.frame_counts["system"]
            if behind > 0:
                writer.write(np.zeros((behind, self._system_channels), dtype=np.float32))
                self.frame_counts["system"] += behind

    def _mic_callback(self, in_data, frame_count, time_info, status):
        data = np.frombuffer(in_data, dtype=np.float32).reshape(-1, 1)
        if self._mic_muted:
            data = np.zeros_like(data)
        self._write_samples("mic", data)
        try:
            self._pad_system()
        except Exception:
            if not self._write_failure_reported:
                self._write_failure_reported = True
                self._report_error("Could not write system audio; source files retained")
        if self._mic_level_callback:
            self._mic_level_callback(rms_level(data))
        self._feed_mic_tap(data)
        return (None, pyaudio.paContinue)

    def stop(self):
        if not self._is_recording:
            return None, None
        for attr in ("_system_stream", "_mic_stream"):
            stream = getattr(self, attr)
            if stream is not None:
                stream.stop_stream()
                stream.close()
                setattr(self, attr, None)

        with self._lock:
            for attr in ("_system_writer", "_mic_writer"):
                writer = getattr(self, attr)
                if writer is not None and not writer.closed:
                    writer.close()

        self._is_recording = False
        return self._system_path, self._mic_path

    def close(self):
        self.stop()
        self._pa.terminate()
