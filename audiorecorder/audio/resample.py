"""Turning captured audio into what the realtime API wants.

The loopback runs at whatever rate the sound card chose, in however many channels the
device has. The subtitle stream wants mono 16 kHz signed 16-bit. The conversion happens in
the audio callback, once per block, so it has to be cheap and it has to join blocks
seamlessly: restarting the read position every block would put a step at every boundary,
64 milliseconds apart, and the transcript would hear it.
"""

import numpy as np


class Downsampler:
    """Mono, rate-converted, int16 bytes, continuous across calls.

    Linear interpolation, the same as the encoder uses when it mixes tracks of different
    rates. It is not a brickwall filter and does not pretend to be: speech at 16 kHz from a
    48 kHz source is what the API asks for, and the model is doing the listening.
    """

    def __init__(self, source_rate, target_rate):
        if source_rate <= 0 or target_rate <= 0:
            raise ValueError(f"rates must be positive, got {source_rate} and {target_rate}")
        # How far to step through the source for each output sample.
        self._step = source_rate / target_rate
        self._position = 0.0
        self._pending = np.zeros(0, dtype=np.float32)

    def reset(self):
        self._position = 0.0
        self._pending = np.zeros(0, dtype=np.float32)

    def feed(self, block):
        """A block of captured audio in, int16 bytes out. May legitimately return nothing."""
        mono = np.asarray(block, dtype=np.float32)
        if mono.ndim > 1:
            mono = mono.mean(axis=1)

        data = np.concatenate([self._pending, mono]) if self._pending.size else mono
        # Interpolation reads one sample past the index, so the last sample can never be a
        # starting point. It waits in the buffer for the next block to give it a partner.
        if data.size < 2:
            self._pending = data
            return b""

        last_usable = data.size - 2
        count = int(np.floor((last_usable - self._position) / self._step)) + 1
        if count <= 0:
            self._pending = data
            return b""

        positions = self._position + np.arange(count) * self._step
        left = positions.astype(np.int64)
        fraction = positions - left
        samples = data[left] * (1.0 - fraction) + data[left + 1] * fraction

        # Where the next block picks up, split into the whole samples we can now forget and
        # the fraction that has to survive until the next call.
        next_position = positions[-1] + self._step
        keep_from = min(int(next_position), data.size)
        self._position = next_position - keep_from
        self._pending = data[keep_from:].copy()

        return (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
