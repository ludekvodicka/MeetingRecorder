"""The subtitle tap runs inside the audio callback, next to the recording write.

That is the one place in this application where a mistake costs the user their call, so the
tap is tested for what it must never do: raise, block, or exist when nobody asked for it.
"""

import numpy as np
import pytest

from audiorecorder.audio.backend import SUBTITLE_RATE, CaptureBackend


class FakeBackend(CaptureBackend):
    """The base class without a sound card. Only the tap machinery is under test."""

    def list_system_sources(self):
        return []

    def list_microphones(self):
        return []

    def start(self, tmp_dir, system_source, mic_source):
        pass

    def stop(self):
        return None, None

    def close(self):
        pass


def block(frames=1024, channels=2, value=0.25):
    return np.full((frames, channels), value, dtype=np.float32)


@pytest.fixture
def backend():
    b = FakeBackend()
    b._prepare_tap(48000)
    return b


class TestNoTap:
    def test_nothing_happens_without_a_tap(self, backend):
        backend._feed_tap(block())  # must not raise

    def test_nothing_happens_without_a_prepared_downsampler(self):
        FakeBackend().set_system_tap(lambda chunk: pytest.fail("should not be called"))
        FakeBackend()._feed_tap(block())


class TestFeeding:
    def test_the_tap_receives_int16_bytes(self, backend):
        received = []
        backend.set_system_tap(received.append)

        backend._feed_tap(block())

        assert received, "a full block should produce output"
        assert isinstance(received[0], bytes)
        assert len(received[0]) % 2 == 0

    def test_the_rate_is_the_one_the_api_wants(self, backend):
        received = []
        backend.set_system_tap(received.append)

        for _ in range(47):  # roughly a second at 48 kHz in 1024-frame blocks
            backend._feed_tap(block())

        samples = sum(len(chunk) // 2 for chunk in received)
        assert samples == pytest.approx(SUBTITLE_RATE, rel=0.02)

    def test_stereo_arrives_as_mono(self, backend):
        received = []
        backend.set_system_tap(received.append)
        backend._feed_tap(block(frames=3072, channels=2))
        # 3072 frames at 48 kHz is 1024 at 16 kHz, whatever the channel count was.
        assert sum(len(chunk) // 2 for chunk in received) == pytest.approx(1024, abs=2)

    def test_clearing_the_tap_stops_the_flow(self, backend):
        received = []
        backend.set_system_tap(received.append)
        backend._feed_tap(block())
        backend.set_system_tap(None)
        backend._feed_tap(block())
        assert len(received) == 1


class TestFailureIsContained:
    def test_a_raising_tap_does_not_propagate(self, backend):
        """The recording is written before this point and must not be taken down with it."""
        def explode(chunk):
            raise RuntimeError("the subtitle session died")

        backend.set_system_tap(explode)
        backend._feed_tap(block())  # must not raise

    def test_a_raising_tap_is_dropped_rather_than_retried_every_block(self, backend):
        calls = []

        def explode(chunk):
            calls.append(1)
            raise RuntimeError("boom")

        backend.set_system_tap(explode)
        for _ in range(5):
            backend._feed_tap(block())

        assert calls == [1], "one failure is enough to disable the tap"
        assert backend._system_tap is None
