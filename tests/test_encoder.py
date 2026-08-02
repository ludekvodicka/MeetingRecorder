import os

import numpy as np
import pytest
import soundfile as sf

from audiorecorder.audio.encoder import _resample, mix_and_encode


def write_wav(path, samples, rate=48000, channels=1):
    data = samples if channels == 1 else np.column_stack([samples] * channels)
    sf.write(str(path), data.astype("float32"), rate)
    return str(path)


def tone(seconds=1.0, rate=48000, freq=440, amplitude=0.5):
    t = np.arange(int(seconds * rate)) / rate
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype("float32")


class TestResample:
    def test_same_rate_is_unchanged(self):
        data = tone(0.1)
        assert _resample(data, 48000, 48000) is data

    def test_upsampling_lengthens_proportionally(self):
        data = tone(0.1, rate=16000)
        out = _resample(data, 16000, 48000)
        assert len(out) == pytest.approx(len(data) * 3, rel=0.01)

    def test_downsampling_shortens_proportionally(self):
        data = tone(0.1, rate=48000)
        out = _resample(data, 48000, 16000)
        assert len(out) == pytest.approx(len(data) / 3, rel=0.01)

    def test_amplitude_survives_resampling(self):
        data = tone(0.2, rate=16000, freq=100, amplitude=0.5)
        out = _resample(data, 16000, 48000)
        assert float(np.abs(out).max()) == pytest.approx(0.5, abs=0.05)


class TestMixAndEncode:
    def test_two_tracks_produce_a_playable_file(self, tmp_path):
        system = write_wav(tmp_path / "s.wav", tone(1.0), channels=2)
        mic = write_wav(tmp_path / "m.wav", tone(1.0, freq=880))
        out = str(tmp_path / "out.m4a")

        mix_and_encode(system, mic, out)

        assert os.path.getsize(out) > 0

    def test_temporary_wavs_are_removed(self, tmp_path):
        system = write_wav(tmp_path / "s.wav", tone(0.5), channels=2)
        mic = write_wav(tmp_path / "m.wav", tone(0.5))

        mix_and_encode(system, mic, str(tmp_path / "out.m4a"))

        assert not os.path.exists(system)
        assert not os.path.exists(mic)

    def test_microphone_only_recording(self, tmp_path):
        """system_wav_path is None on macOS and Linux without a routed system source."""
        mic = write_wav(tmp_path / "m.wav", tone(1.0))
        out = str(tmp_path / "out.m4a")

        mix_and_encode(None, mic, out)

        assert os.path.getsize(out) > 0
        assert not os.path.exists(mic)

    def test_silent_loopback_still_produces_a_file(self, tmp_path):
        """Nothing was playing, so the loopback track is empty but the microphone is not."""
        system = write_wav(tmp_path / "s.wav", np.zeros(0, dtype="float32"), channels=2)
        mic = write_wav(tmp_path / "m.wav", tone(1.0))

        mix_and_encode(system, mic, str(tmp_path / "out.m4a"))

        assert os.path.getsize(str(tmp_path / "out.m4a")) > 0

    def test_both_tracks_empty(self, tmp_path):
        system = write_wav(tmp_path / "s.wav", np.zeros(0, dtype="float32"), channels=2)
        mic = write_wav(tmp_path / "m.wav", np.zeros(0, dtype="float32"))
        out = str(tmp_path / "out.m4a")

        mix_and_encode(system, mic, out)

        assert os.path.getsize(out) > 0

    def test_different_sample_rates_are_reconciled(self, tmp_path):
        system = write_wav(tmp_path / "s.wav", tone(1.0, rate=48000), rate=48000, channels=2)
        mic = write_wav(tmp_path / "m.wav", tone(1.0, rate=16000), rate=16000)

        mix_and_encode(system, mic, str(tmp_path / "out.m4a"))

        assert os.path.getsize(str(tmp_path / "out.m4a")) > 0

    def test_tracks_of_different_lengths_are_padded(self, tmp_path):
        system = write_wav(tmp_path / "s.wav", tone(2.0), channels=2)
        mic = write_wav(tmp_path / "m.wav", tone(0.5))

        mix_and_encode(system, mic, str(tmp_path / "out.m4a"))

        assert os.path.getsize(str(tmp_path / "out.m4a")) > 0

    def test_volumes_scale_the_mix(self, tmp_path):
        loud = write_wav(tmp_path / "s1.wav", tone(0.5, amplitude=0.5), channels=2)
        mic1 = write_wav(tmp_path / "m1.wav", np.zeros(24000, dtype="float32"))
        mix_and_encode(loud, mic1, str(tmp_path / "loud.m4a"), system_volume=1.0)

        quiet = write_wav(tmp_path / "s2.wav", tone(0.5, amplitude=0.5), channels=2)
        mic2 = write_wav(tmp_path / "m2.wav", np.zeros(24000, dtype="float32"))
        mix_and_encode(quiet, mic2, str(tmp_path / "quiet.m4a"), system_volume=0.0)

        assert os.path.getsize(str(tmp_path / "loud.m4a")) > os.path.getsize(
            str(tmp_path / "quiet.m4a"))
