import os

import av
import numpy as np
import soundfile as sf


def mix_and_encode(system_wav_path, mic_wav_path, output_m4a_path,
                   system_volume=1.0, mic_volume=1.0, bitrate=128000):
    """Mix the system and microphone WAV files and encode to M4A (AAC).

    ``system_wav_path`` is None when the platform recorded the microphone only, which is
    the normal case on macOS and Linux without a routed system source.
    """

    if system_wav_path is None:
        system_data, system_rate = np.array([], dtype="float32"), 0
    else:
        system_data, system_rate = sf.read(system_wav_path, dtype="float32")
    mic_data, mic_rate = sf.read(mic_wav_path, dtype="float32")

    # Handle empty sources (loopback has no audio if nothing was playing)
    system_empty = system_data.size == 0
    mic_empty = mic_data.size == 0

    if system_empty and mic_empty:
        output_rate = mic_rate or system_rate or 48000
        _encode_aac(np.zeros(output_rate, dtype=np.float32), output_rate, output_m4a_path, bitrate)
        _remove_temps(system_wav_path, mic_wav_path)
        return

    # Convert system audio to mono
    if not system_empty and system_data.ndim > 1:
        system_mono = system_data.mean(axis=1)
    elif not system_empty:
        system_mono = system_data.flatten()
    else:
        system_mono = np.array([], dtype=np.float32)

    mic_flat = mic_data.flatten() if not mic_empty else np.array([], dtype=np.float32)

    # Determine output rate
    output_rate = system_rate if not system_empty else mic_rate

    # Resample mic if rates differ
    if not mic_empty and mic_rate != output_rate:
        mic_flat = _resample(mic_flat, mic_rate, output_rate)

    # Match lengths (pad shorter with silence)
    max_len = max(len(system_mono), len(mic_flat))
    if len(system_mono) < max_len:
        system_mono = np.pad(system_mono, (0, max_len - len(system_mono)))
    if len(mic_flat) < max_len:
        mic_flat = np.pad(mic_flat, (0, max_len - len(mic_flat)))

    mixed = system_mono * system_volume + mic_flat * mic_volume
    mixed = np.clip(mixed, -1.0, 1.0)

    _encode_aac(mixed, int(output_rate), output_m4a_path, bitrate)

    _remove_temps(system_wav_path, mic_wav_path)


def _remove_temps(*paths):
    for path in paths:
        if path is not None:
            os.remove(path)


def _resample(data, src_rate, dst_rate):
    if src_rate == dst_rate:
        return data
    ratio = dst_rate / src_rate
    new_len = int(len(data) * ratio)
    indices = np.arange(new_len) / ratio
    indices_floor = indices.astype(int)
    indices_floor = np.clip(indices_floor, 0, len(data) - 2)
    frac = indices - indices_floor
    return data[indices_floor] * (1 - frac) + data[indices_floor + 1] * frac


def _encode_aac(samples, sample_rate, output_path, bitrate):
    """Encode float32 mono samples to M4A (AAC) using PyAV."""

    int16_data = (samples * 32767).astype(np.int16)

    container = av.open(output_path, mode="w")
    stream = container.add_stream("aac", rate=sample_rate)
    stream.bit_rate = bitrate
    stream.layout = "mono"

    frame_size = 1024
    for i in range(0, len(int16_data), frame_size):
        chunk = int16_data[i:i + frame_size]
        if len(chunk) < frame_size:
            chunk = np.pad(chunk, (0, frame_size - len(chunk)))

        frame = av.AudioFrame.from_ndarray(
            chunk.reshape(1, -1), format="s16", layout="mono"
        )
        frame.sample_rate = sample_rate

        for packet in stream.encode(frame):
            container.mux(packet)

    for packet in stream.encode(None):
        container.mux(packet)

    container.close()
