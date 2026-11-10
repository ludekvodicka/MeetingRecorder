"""The WASAPI system track: silence while the loopback is quiet, and a switch of device.

A loopback delivers no data while nothing plays on its device. Without padding the system
track would lose that time and drift ahead of the microphone. A switch writes the new
device into the same track, in the format the track was opened with.
"""

import sys

import numpy as np
import pytest
import soundfile as sf

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="WASAPI exists on Windows only")

DEVICES = [
    {"name": "Speakers [Loopback]", "isLoopbackDevice": True,
     "defaultSampleRate": 48000.0, "maxInputChannels": 2},
    {"name": "Headphones [Loopback]", "isLoopbackDevice": True,
     "defaultSampleRate": 44100.0, "maxInputChannels": 1},
    {"name": "Microphone", "isLoopbackDevice": False,
     "defaultSampleRate": 48000.0, "maxInputChannels": 1},
]


class FakeStream:
    def __init__(self, device, callback):
        self.device = device
        self.callback = callback
        self.closed = False

    def deliver(self, block):
        self.callback(np.ascontiguousarray(block, dtype=np.float32).tobytes(), len(block), {}, 0)

    def stop_stream(self):
        pass

    def close(self):
        self.closed = True


class FakePyAudio:
    def __init__(self):
        self.streams = []

    def get_device_count(self):
        return len(DEVICES)

    def get_device_info_by_index(self, index):
        return {**DEVICES[index], "index": index}

    def open(self, input_device_index, stream_callback, **kwargs):
        stream = FakeStream(DEVICES[input_device_index]["name"], stream_callback)
        self.streams.append(stream)
        return stream

    def terminate(self):
        pass


@pytest.fixture
def backend(monkeypatch, tmp_path):
    from audiorecorder.audio import backend_wasapi

    monkeypatch.setattr(backend_wasapi.pyaudio, "PyAudio", FakePyAudio)
    capture = backend_wasapi.WasapiBackend()
    capture.start(str(tmp_path), "Speakers [Loopback]", "Microphone")
    yield capture
    capture.close()


def streams(backend):
    system, mic = backend._pa.streams[:2]
    assert (system.device, mic.device) == ("Speakers [Loopback]", "Microphone")
    return system, mic


def system_track(backend):
    path, _mic = backend.stop()
    with sf.SoundFile(path) as track:
        return track.samplerate, track.channels, track.read(dtype="float32")


def test_a_quiet_loopback_is_filled_with_silence_up_to_the_microphone(backend):
    system, mic = streams(backend)
    backend._last_system_block = 0.0  # the loopback delivered nothing for a long time
    for _ in range(10):
        mic.deliver(np.full((1024, 1), 0.1))

    assert backend.frame_counts["system"] == backend.frame_counts["mic"] == 10240
    rate, channels, data = system_track(backend)
    assert (rate, channels) == (48000, 2)
    assert not data.any()


def test_a_playing_loopback_is_not_padded(backend):
    system, mic = streams(backend)
    system.deliver(np.full((1024, 2), 0.2))
    mic.deliver(np.full((2048, 1), 0.1))
    assert backend.frame_counts["system"] == 1024


def test_a_switch_writes_the_new_device_into_the_same_track(backend):
    system, mic = streams(backend)
    system.deliver(np.full((1024, 2), 0.2))

    backend.switch_system_source("Headphones")
    headphones = backend._pa.streams[-1]
    assert headphones.device == "Headphones [Loopback]"
    assert system.closed
    assert backend.active_system_source == "Headphones"

    system.deliver(np.full((1024, 2), 0.9))  # a late block of the closed stream is dropped
    for _ in range(10):
        headphones.deliver(np.full((441, 1), 0.5))

    rate, channels, data = system_track(backend)
    assert (rate, channels) == (48000, 2)
    # 4410 frames at 44.1 kHz are about 4800 at 48 kHz, the closed stream's block is absent.
    assert len(data) - 1024 == pytest.approx(4800, abs=2)
    assert np.allclose(data[:1024], 0.2)
    assert np.allclose(data[1100:], 0.5, atol=1e-4)
    assert not np.isclose(data, 0.9).any()


def test_an_unknown_device_keeps_the_current_one(backend):
    from audiorecorder.audio.backend import CaptureError

    system, _mic = streams(backend)
    with pytest.raises(CaptureError):
        backend.switch_system_source("Nowhere")
    assert not system.closed
    assert backend.active_system_source == "Speakers"
