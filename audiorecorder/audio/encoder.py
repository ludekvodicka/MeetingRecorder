import os
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass

import av
import numpy as np
import soundfile as sf


class NoAudioError(RuntimeError):
    """No frames were captured; inputs are retained for inspection."""


@dataclass(frozen=True)
class EncodeResult:
    cleanup_errors: tuple[str, ...] = ()


def mix_and_encode(
    system_wav_path,
    mic_wav_path,
    output_m4a_path,
    system_volume=1.0,
    mic_volume=1.0,
    bitrate=128000,
):
    """Publish a validated recording without exposing a partially muxed final file."""
    directory = os.path.dirname(os.path.abspath(output_m4a_path))
    fd, staging = tempfile.mkstemp(prefix=".recording-", suffix=".m4a", dir=directory)
    os.close(fd)
    published = False
    errors = []
    try:
        _mix_to_file(system_wav_path, mic_wav_path, staging, system_volume, mic_volume, bitrate)
        with av.open(staging) as container:
            if not container.streams.audio or next(container.decode(audio=0), None) is None:
                raise NoAudioError("Encoded recording has no decodable audio")
        # Atomic publication refuses to overwrite an existing recording.
        os.link(staging, output_m4a_path)
        published = True
    finally:
        try:
            os.unlink(staging)
        except OSError as error:
            if published:
                errors.append(f"{staging}: {error}")
            # Before publication preserve the original encoding error and source WAVs.
    for path in (system_wav_path, mic_wav_path):
        if path is not None:
            try:
                os.remove(path)
            except OSError as error:
                errors.append(f"{path}: {error}")
    return EncodeResult(tuple(errors))


def _read_aligned(source, start, count, output_rate):
    """Read a bounded, time-aligned mono block; resampling phase spans block boundaries."""
    result = np.zeros(count, dtype=np.float32)
    if source is None or source.frames == 0:
        return result
    positions = (np.arange(count, dtype=np.float64) + start) * source.samplerate / output_rate
    valid = positions < source.frames
    if not valid.any():
        return result
    positions = positions[valid]
    first = int(positions[0])
    last = min(source.frames, int(positions[-1]) + 2)
    source.seek(first)
    data = source.read(last - first, dtype="float32", always_2d=True).mean(axis=1)
    if not len(data):
        return result
    result[valid] = np.interp(positions - first, np.arange(len(data)), data)
    return result


def _mix_blocks(system, microphone, output_rate, system_volume, mic_volume):
    sources = [source for source in (system, microphone) if source is not None]
    length = max(int(source.frames * output_rate / source.samplerate) for source in sources)
    # Bound input reads too, including unusual devices with very different sample rates.
    ratio = max(source.samplerate / output_rate for source in sources)
    block_size = min(4096, max(1, int(65536 / max(1, ratio))))
    for start in range(0, length, block_size):
        count = min(block_size, length - start)
        mixed = (
            _read_aligned(system, start, count, output_rate) * system_volume
            + _read_aligned(microphone, start, count, output_rate) * mic_volume
        )
        yield np.clip(mixed, -1.0, 1.0)


def _mix_to_file(
    system_wav_path,
    mic_wav_path,
    output_m4a_path,
    system_volume=1.0,
    mic_volume=1.0,
    bitrate=128000,
):
    """Stream both closed source WAVs without loading an entire call into memory."""
    with ExitStack() as stack:
        system = stack.enter_context(sf.SoundFile(system_wav_path)) if system_wav_path else None
        microphone = stack.enter_context(sf.SoundFile(mic_wav_path))
        if microphone.frames == 0 and (system is None or system.frames == 0):
            raise NoAudioError("No audio captured; source WAVs were retained")
        output_rate = (
            system.samplerate if system is not None and system.frames else microphone.samplerate
        )
        blocks = _mix_blocks(system, microphone, output_rate, system_volume, mic_volume)
        _encode_aac(blocks, int(output_rate), output_m4a_path, bitrate)


def _resample(data, src_rate, dst_rate):
    if src_rate == dst_rate:
        return data
    if len(data) < 2:
        return np.repeat(data, max(1, int(len(data) * dst_rate / src_rate)))
    ratio = dst_rate / src_rate
    new_len = int(len(data) * ratio)
    indices = np.arange(new_len) / ratio
    indices_floor = indices.astype(int)
    indices_floor = np.clip(indices_floor, 0, len(data) - 2)
    frac = indices - indices_floor
    return data[indices_floor] * (1 - frac) + data[indices_floor + 1] * frac


def _aac_blocks(blocks):
    pending = np.array([], dtype=np.float32)
    for block in blocks:
        pending = np.concatenate((pending, block))
        while len(pending) >= 1024:
            yield (pending[:1024] * 32767).astype(np.int16)
            pending = pending[1024:]
    if len(pending):
        yield (np.pad(pending, (0, 1024 - len(pending))) * 32767).astype(np.int16)


def _encode_aac(blocks, sample_rate, output_path, bitrate):
    """Encode a bounded stream of float32 mono blocks to M4A (AAC)."""
    with av.open(output_path, mode="w") as container:
        stream = container.add_stream("aac", rate=sample_rate)
        stream.bit_rate = bitrate
        stream.layout = "mono"
        for chunk in _aac_blocks(blocks):
            frame = av.AudioFrame.from_ndarray(chunk.reshape(1, -1), format="s16", layout="mono")
            frame.sample_rate = sample_rate
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
