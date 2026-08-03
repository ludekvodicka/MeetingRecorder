import numpy as np
import pytest

from audiorecorder.dictation import engine as engine_module
from audiorecorder.dictation.engine import DictationEngine


class FakeSignalThread:
    def __init__(self, target=None, daemon=None, args=()):
        self.target = target

    def start(self):
        pass


class FakeListener:
    def __init__(self):
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


class FakeStream:
    def __init__(self, fail_start=False, **kwargs):
        self.kwargs = kwargs
        self.fail_start = fail_start
        self.closed = False

    def start(self):
        if self.fail_start:
            raise RuntimeError("microphone denied")

    def stop(self):
        pass

    def close(self):
        self.closed = True


class FakeDefault:
    device = (1, 2)


class FakeAudioApi:
    class PortAudioError(Exception):
        pass

    default = FakeDefault()
    devices = [
        {"name": "BlackHole", "max_input_channels": 2, "default_samplerate": 48000},
        {"name": "MacBook Mic", "max_input_channels": 1, "default_samplerate": 44100},
    ]

    def __init__(self, fail_start=False):
        self.fail_start = fail_start
        self.streams = []

    def query_devices(self, index=None):
        return self.devices if index is None else self.devices[index]

    def InputStream(self, **kwargs):
        stream = FakeStream(fail_start=self.fail_start, **kwargs)
        self.streams.append(stream)
        return stream


@pytest.fixture
def engine(monkeypatch):
    audio = FakeAudioApi()
    monkeypatch.setattr(engine_module, "sd", audio)
    monkeypatch.setattr(engine_module.threading, "Thread", FakeSignalThread)
    built = DictationEngine(api_key="x", mic_source_name=None, sample_rate=16000)
    built._listener = FakeListener()
    yield built, audio
    built.stop()


def test_dictation_opens_the_os_default_microphone_at_its_native_rate(engine):
    built, audio = engine
    built.start()

    assert audio.streams[0].kwargs["device"] == 1
    assert audio.streams[0].kwargs["samplerate"] == 44100


def test_native_microphone_audio_is_resampled_for_the_api(engine):
    built, _ = engine
    built.start()
    source = np.full((4410, 1), 0.25, dtype=np.float32)

    pcm = built._downsampler.feed(source)

    assert len(pcm) // 2 == pytest.approx(1600, abs=2)


def test_failed_stream_start_rolls_back_the_global_listener(monkeypatch):
    audio = FakeAudioApi(fail_start=True)
    monkeypatch.setattr(engine_module, "sd", audio)
    built = DictationEngine(api_key="x", mic_source_name=None)
    listener = FakeListener()
    built._listener = listener

    with pytest.raises(RuntimeError, match="microphone denied"):
        built.start()

    assert listener.started == 1
    assert listener.stopped == 1
    assert audio.streams[0].closed
    assert built._stream is None
    assert not built._running
