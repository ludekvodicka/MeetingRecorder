"""Turning captured audio into what the realtime API wants.

The loopback runs at whatever rate the sound card chose, in however many channels the
device has. The subtitle stream wants mono 16 kHz signed 16-bit. The conversion happens in
the audio callback, once per block, so it has to be cheap and it has to join blocks
seamlessly: restarting the read position every block would put a step at every boundary,
64 milliseconds apart, and the transcript would hear it.
"""

import numpy as np

# Taps of the anti-aliasing filter. 63 Hamming-windowed taps attenuate the stopband by
# about 50 dB and cost one short convolution per block.
FILTER_TAPS = 63
# The passband edge as a share of the target rate: just below its Nyquist frequency.
CUTOFF_RATIO = 0.45


def _lowpass(source_rate, target_rate):
    """Unity-gain windowed-sinc low-pass that keeps only what the target rate can carry."""
    cutoff = CUTOFF_RATIO * target_rate / source_rate
    n = np.arange(FILTER_TAPS) - (FILTER_TAPS - 1) / 2
    taps = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(FILTER_TAPS)
    return (taps / taps.sum()).astype(np.float32)


class Downsampler:
    """Mono, rate-converted, int16 bytes, continuous across calls.

    When the rate goes down, a low-pass filter first removes everything above the target's
    Nyquist frequency; without it, sibilants and room noise from a 48 kHz microphone would
    fold back into the speech band. Linear interpolation then picks the output samples.
    """

    def __init__(self, source_rate, target_rate):
        if source_rate <= 0 or target_rate <= 0:
            raise ValueError(f"rates must be positive, got {source_rate} and {target_rate}")
        # How far to step through the source for each output sample.
        self._step = source_rate / target_rate
        self._taps = _lowpass(source_rate, target_rate) if source_rate > target_rate else None
        self.reset()

    def reset(self):
        self._position = 0.0
        self._pending = np.zeros(0, dtype=np.float32)
        # The last input samples, so the filter runs across block boundaries without a seam.
        self._history = np.zeros(FILTER_TAPS - 1, dtype=np.float32)

    def feed(self, block):
        """A block of captured audio in, int16 bytes out. May legitimately return nothing."""
        mono = np.asarray(block, dtype=np.float32)
        if mono.ndim > 1:
            mono = mono.mean(axis=1)
        if self._taps is not None and mono.size:
            extended = np.concatenate([self._history, mono])
            self._history = extended[-(FILTER_TAPS - 1):]
            mono = np.convolve(extended, self._taps, mode="valid").astype(np.float32)

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
