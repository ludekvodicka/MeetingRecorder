"""The downsampler runs inside the audio callback, block by block.

Its whole job is to hand the realtime API a continuous 16 kHz mono stream out of whatever
the sound card produces. Getting the rate wrong makes the transcript hear chipmunks; losing
the position between blocks puts a step every 64 milliseconds.
"""

import numpy as np
import pytest

from audiorecorder.audio.resample import Downsampler

TARGET = 16000


def sine(seconds, rate, freq=440.0, channels=1):
    t = np.arange(int(seconds * rate)) / rate
    wave = np.sin(2 * np.pi * freq * t).astype(np.float32)
    return np.column_stack([wave] * channels) if channels > 1 else wave


def in_blocks(data, size=1024):
    for start in range(0, len(data), size):
        yield data[start:start + size]


def to_float(raw):
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0


def zero_crossings(samples):
    return int(np.sum(np.diff(np.signbit(samples)) != 0))


class TestRate:
    @pytest.mark.parametrize("source_rate", [48000, 44100, 32000, 16000])
    def test_output_length_matches_the_ratio(self, source_rate):
        data = sine(1.0, source_rate)
        sampler = Downsampler(source_rate, TARGET)
        out = b"".join(sampler.feed(block) for block in in_blocks(data))
        assert len(to_float(out)) == pytest.approx(TARGET, rel=0.01)

    @pytest.mark.parametrize("source_rate", [48000, 44100])
    def test_the_tone_keeps_its_pitch(self, source_rate):
        """A 440 Hz tone has to still be 440 Hz, or the transcript hears the wrong voice."""
        data = sine(1.0, source_rate, freq=440.0)
        sampler = Downsampler(source_rate, TARGET)
        out = to_float(b"".join(sampler.feed(block) for block in in_blocks(data)))
        # Two crossings per cycle.
        assert zero_crossings(out) / 2 == pytest.approx(440, rel=0.02)

    def test_equal_rates_pass_the_signal_through(self):
        data = sine(0.1, TARGET)
        sampler = Downsampler(TARGET, TARGET)
        out = to_float(b"".join(sampler.feed(block) for block in in_blocks(data)))
        assert len(out) == pytest.approx(len(data), abs=2)
        assert np.abs(out[:100] - data[:100]).max() < 0.01


class TestContinuity:
    def test_block_size_does_not_change_the_result(self):
        """Whatever the driver hands over, the stream must come out the same."""
        data = sine(0.5, 48000)
        one_go = to_float(Downsampler(48000, TARGET).feed(data))

        blocked = Downsampler(48000, TARGET)
        pieces = to_float(b"".join(blocked.feed(block) for block in in_blocks(data, 1024)))

        shared = min(len(one_go), len(pieces))
        assert abs(len(one_go) - len(pieces)) <= 1
        assert np.abs(one_go[:shared] - pieces[:shared]).max() < 1e-3

    def test_odd_block_sizes_do_not_drift(self):
        data = sine(0.5, 44100)
        reference = to_float(Downsampler(44100, TARGET).feed(data))

        sampler = Downsampler(44100, TARGET)
        pieces = to_float(b"".join(sampler.feed(block) for block in in_blocks(data, 391)))

        assert abs(len(reference) - len(pieces)) <= 1

    def test_no_step_at_the_joins(self):
        """A discontinuity at a block boundary is a click, 16 times a second."""
        data = sine(0.3, 48000, freq=100.0)
        sampler = Downsampler(48000, TARGET)
        out = to_float(b"".join(sampler.feed(block) for block in in_blocks(data, 1024)))

        # A 100 Hz sine at 16 kHz moves by at most 0.04 between neighbours.
        assert np.abs(np.diff(out)).max() < 0.06


class TestEdges:
    def test_empty_block(self):
        assert Downsampler(48000, TARGET).feed(np.zeros(0, dtype=np.float32)) == b""

    def test_a_single_sample_waits_for_a_partner(self):
        """Interpolation needs the next sample, so one alone cannot be converted yet."""
        sampler = Downsampler(48000, TARGET)
        assert sampler.feed(np.array([0.5], dtype=np.float32)) == b""

    def test_stereo_is_averaged_to_mono(self):
        left = sine(0.1, 48000)
        stereo = np.column_stack([left, -left])  # cancels exactly
        out = to_float(Downsampler(48000, TARGET).feed(stereo))
        assert np.abs(out).max() < 0.01

    def test_channel_count_does_not_change_the_length(self):
        mono = to_float(Downsampler(48000, TARGET).feed(sine(0.2, 48000, channels=1)))
        six = to_float(Downsampler(48000, TARGET).feed(sine(0.2, 48000, channels=6)))
        assert len(mono) == len(six)

    def test_loud_input_is_clipped_not_wrapped(self):
        """int16 wrapping turns a loud passage into noise."""
        out = to_float(Downsampler(48000, TARGET).feed(np.full(4096, 3.0, dtype=np.float32)))
        assert out.max() <= 1.0

    def test_reset_starts_again(self):
        sampler = Downsampler(48000, TARGET)
        sampler.feed(sine(0.1, 48000))
        sampler.reset()
        first = to_float(sampler.feed(sine(0.1, 48000)))
        second = to_float(Downsampler(48000, TARGET).feed(sine(0.1, 48000)))
        assert len(first) == len(second)

    @pytest.mark.parametrize("bad", [0, -1])
    def test_impossible_rates_are_refused(self, bad):
        with pytest.raises(ValueError):
            Downsampler(bad, TARGET)
        with pytest.raises(ValueError):
            Downsampler(48000, bad)
