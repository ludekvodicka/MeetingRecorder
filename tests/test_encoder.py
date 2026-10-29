import os

import numpy as np
import pytest
import soundfile as sf

from audiorecorder.audio.encoder import NoAudioError, _resample, mix_and_encode


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

        with pytest.raises(NoAudioError):
            mix_and_encode(system, mic, out)
        assert not os.path.exists(out)
        assert os.path.exists(mic) and os.path.exists(system)

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
            str(tmp_path / "quiet.m4a")
        )


def test_publish_collision_preserves_existing_output_and_sources(tmp_path):
    mic = write_wav(tmp_path / "mic.wav", tone(0.1))
    out = tmp_path / "out.m4a"
    out.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        mix_and_encode(None, mic, str(out))
    assert out.read_bytes() == b"existing"
    assert os.path.exists(mic)


def test_encode_failure_never_exposes_final_name(tmp_path, monkeypatch):
    from audiorecorder.audio import encoder

    mic = write_wav(tmp_path / "mic.wav", tone(0.1))
    out = tmp_path / "out.m4a"
    monkeypatch.setattr(
        encoder, "_encode_aac", lambda *args: (_ for _ in ()).throw(OSError("disk full"))
    )
    with pytest.raises(OSError, match="disk full"):
        mix_and_encode(None, mic, str(out))
    assert not out.exists()
    assert os.path.exists(mic)


def test_cleanup_failure_is_saved_with_warning(tmp_path, monkeypatch):
    from audiorecorder.audio import encoder

    mic = write_wav(tmp_path / "mic.wav", tone(0.1))
    out = tmp_path / "out.m4a"
    monkeypatch.setattr(
        encoder.os, "remove", lambda *args: (_ for _ in ()).throw(OSError("cleanup failed"))
    )
    result = mix_and_encode(None, mic, str(out))
    assert result.cleanup_errors
    with encoder.av.open(str(out)) as container:
        assert next(container.decode(audio=0)).samples > 0
    assert os.path.exists(mic)


def test_streamed_resampling_preserves_phase_across_blocks(tmp_path):
    from audiorecorder.audio.encoder import _read_aligned

    path = write_wav(tmp_path / "mic.wav", tone(1.3, rate=44100, freq=300), rate=44100)
    with sf.SoundFile(path) as source:
        whole = _read_aligned(source, 0, 60000, 48000)
        pieces = np.concatenate(
            [
                _read_aligned(source, start, min(4096, 60000 - start), 48000)
                for start in range(0, 60000, 4096)
            ]
        )
    assert np.allclose(whole, pieces, atol=1e-6)


def test_streamed_encoder_reads_bounded_blocks_and_decodes_both_tracks(tmp_path, monkeypatch):
    from audiorecorder.audio import encoder

    system = write_wav(tmp_path / "system.wav", tone(2.1, freq=440), channels=2)
    mic = write_wav(tmp_path / "mic.wav", tone(1.7, rate=44100, freq=880), rate=44100)
    reads = []
    original = sf.SoundFile.read

    def bounded(self, frames=-1, *args, **kwargs):
        reads.append(frames)
        assert 0 <= frames <= 65538
        return original(self, frames, *args, **kwargs)

    monkeypatch.setattr(sf.SoundFile, "read", bounded)
    out = tmp_path / "mix.m4a"
    encoder.mix_and_encode(system, mic, str(out))
    with encoder.av.open(str(out)) as container:
        decoded = np.concatenate(
            [frame.to_ndarray().flatten() for frame in container.decode(audio=0)]
        )
    assert len(decoded) / 48000 == pytest.approx(2.1, abs=0.05)
    # Both tones survive downmix/resampling; the shorter source is padded with silence.
    fft = np.abs(np.fft.rfft(decoded[:48000]))
    assert fft[440] > 100 and fft[880] > 100
    assert len(reads) > 10


def test_nonmultiple_mix_blocks_do_not_insert_silence_at_each_boundary():
    from audiorecorder.audio.encoder import _aac_blocks

    blocks = [np.ones(777, dtype="float32") * 0.25 for _ in range(5)]
    output = np.concatenate(list(_aac_blocks(blocks)))
    assert np.all(output[:3885] > 0)
    assert np.all(output[3885:] == 0)


def test_multi_hour_sources_allocate_only_bounded_mix_blocks():
    import itertools
    import tracemalloc

    from audiorecorder.audio.encoder import _mix_blocks

    class VirtualSource:
        frames = 8 * 3600 * 48000
        samplerate = 48000

        def seek(self, offset):
            self.offset = offset

        def read(self, count, **kwargs):
            assert count <= 65538
            return np.zeros((count, 2), dtype="float32")

    tracemalloc.start()
    try:
        blocks = _mix_blocks(VirtualSource(), VirtualSource(), 48000, 1, 1)
        for block in itertools.islice(blocks, 256):
            assert len(block) <= 4096
        _, peak = tracemalloc.get_traced_memory()
        assert peak < 4_000_000
    finally:
        tracemalloc.stop()


def test_publication_without_hard_links_produces_the_recording(tmp_path, monkeypatch):
    """FAT32, exFAT and some network shares refuse os.link."""
    from audiorecorder import atomic_files
    from audiorecorder.audio import encoder

    monkeypatch.setattr(
        atomic_files.os, "link", lambda *args: (_ for _ in ()).throw(OSError("no links")))
    mic = write_wav(tmp_path / "mic.wav", tone(0.1))
    out = tmp_path / "out.m4a"
    result = mix_and_encode(None, mic, str(out))
    assert result.cleanup_errors == ()
    with encoder.av.open(str(out)) as container:
        assert next(container.decode(audio=0)).samples > 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.m4a"]
