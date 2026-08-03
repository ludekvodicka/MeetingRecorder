import sys
from types import SimpleNamespace

import numpy as np
import pytest

from audiorecorder.audio import macos_screen_capture as native
from audiorecorder.audio.backend import CaptureError
from audiorecorder.audio.backend_macos import MacOSBackend, native_system_audio_supported
from audiorecorder.audio.backend_portaudio import PortAudioBackend


@pytest.fixture
def core_audio_flags(monkeypatch):
    constants = SimpleNamespace(
        kAudioFormatFlagIsFloat=1,
        kAudioFormatFlagIsBigEndian=2,
        kAudioFormatFlagIsSignedInteger=4,
        kAudioFormatFlagIsNonInterleaved=32,
    )
    monkeypatch.setitem(sys.modules, "CoreAudio", constants)
    return constants


def test_decode_interleaved_float_pcm(core_audio_flags):
    expected = np.array([[0.1, 0.2], [0.3, 0.4]], dtype=np.float32)

    actual = native._decode_pcm(
        expected.tobytes(), frames=2, channels=2, bits=32,
        flags=core_audio_flags.kAudioFormatFlagIsFloat,
    )

    np.testing.assert_array_equal(actual, expected)


def test_decode_non_interleaved_float_pcm(core_audio_flags):
    channel_planes = np.array([[0.1, 0.3], [0.2, 0.4]], dtype=np.float32)

    actual = native._decode_pcm(
        channel_planes.tobytes(), frames=2, channels=2, bits=32,
        flags=(
            core_audio_flags.kAudioFormatFlagIsFloat
            | core_audio_flags.kAudioFormatFlagIsNonInterleaved
        ),
    )

    np.testing.assert_array_equal(
        actual,
        np.array([[0.1, 0.2], [0.3, 0.4]], dtype=np.float32),
    )


def test_decode_signed_integer_pcm_as_normalized_float(core_audio_flags):
    encoded = np.array([-32768, 0, 16384, 32767], dtype=np.int16)

    actual = native._decode_pcm(
        encoded.tobytes(), frames=2, channels=2, bits=16,
        flags=core_audio_flags.kAudioFormatFlagIsSignedInteger,
    )

    np.testing.assert_allclose(actual, [[-1.0, 0.0], [0.5, 32767 / 32768]])


def test_decode_rejects_short_audio_blocks(core_audio_flags):
    with pytest.raises(ValueError, match="short ScreenCaptureKit audio block"):
        native._decode_pcm(
            np.zeros(3, dtype=np.float32).tobytes(),
            frames=2,
            channels=2,
            bits=32,
            flags=core_audio_flags.kAudioFormatFlagIsFloat,
        )


def test_default_macos_source_opens_native_system_and_microphone(monkeypatch, tmp_path):
    backend = MacOSBackend()
    opened = []
    monkeypatch.setattr(
        "audiorecorder.audio.backend_macos.native_system_audio_supported", lambda: True
    )
    monkeypatch.setattr(
        backend, "_open_native_system",
        lambda tmp_dir, stamp: opened.append(("system", tmp_dir, stamp)),
    )
    monkeypatch.setattr(
        backend, "_open_microphone",
        lambda tmp_dir, source, stamp: opened.append(("mic", tmp_dir, source, stamp)),
    )
    monkeypatch.setattr(backend, "_start_open_streams", lambda: opened.append(("start",)))

    backend.start(str(tmp_path), None, "Studio Mic")

    assert opened[0][:2] == ("system", str(tmp_path))
    assert opened[1][:3] == ("mic", str(tmp_path), "Studio Mic")
    assert opened[0][2] == opened[1][3]
    assert opened[2] == ("start",)
    assert backend.is_recording


def test_explicit_macos_source_uses_legacy_routed_capture(monkeypatch, tmp_path):
    called = {}

    def fake_start(self, tmp_dir, system_source, mic_source):
        called.update(
            tmp_dir=tmp_dir,
            system_source=system_source,
            mic_source=mic_source,
        )

    monkeypatch.setattr(PortAudioBackend, "start", fake_start)

    MacOSBackend().start(str(tmp_path), "BlackHole 2ch", "Studio Mic")

    assert called == {
        "tmp_dir": str(tmp_path),
        "system_source": "BlackHole 2ch",
        "mic_source": "Studio Mic",
    }


def test_older_macos_keeps_the_microphone_only_default(monkeypatch, tmp_path):
    called = {}
    monkeypatch.setattr(
        "audiorecorder.audio.backend_macos.native_system_audio_supported", lambda: False
    )
    monkeypatch.setattr(
        PortAudioBackend,
        "start",
        lambda self, tmp_dir, system_source, mic_source: called.update(
            system_source=system_source,
            mic_source=mic_source,
        ),
    )

    MacOSBackend().start(str(tmp_path), None, "Studio Mic")

    assert called == {"system_source": None, "mic_source": "Studio Mic"}


@pytest.mark.parametrize(
    ("version", "supported"),
    [("12.7.6", False), ("13.0", True), ("15.6.1", True), ("", False)],
)
def test_native_system_audio_os_support(version, supported):
    assert native_system_audio_supported(version) is supported


def test_native_start_failure_discards_partial_resources(monkeypatch, tmp_path):
    backend = MacOSBackend()
    discarded = []
    monkeypatch.setattr(
        "audiorecorder.audio.backend_macos.native_system_audio_supported", lambda: True
    )
    monkeypatch.setattr(backend, "_open_native_system", lambda *args: None)
    monkeypatch.setattr(
        backend,
        "_open_microphone",
        lambda *args: (_ for _ in ()).throw(RuntimeError("microphone disappeared")),
    )
    monkeypatch.setattr(backend, "_discard_partial_start", lambda: discarded.append(True))

    with pytest.raises(CaptureError, match="microphone disappeared"):
        backend.start(str(tmp_path), None, None)

    assert discarded == [True]
    assert not backend.is_recording


def test_partial_start_cleanup_does_not_mask_original_error(monkeypatch, tmp_path):
    class BrokenCleanup:
        def close(self):
            raise RuntimeError("cleanup failed")

    backend = MacOSBackend()
    monkeypatch.setattr(
        "audiorecorder.audio.backend_macos.native_system_audio_supported", lambda: True
    )

    def open_system(*args):
        backend._system_stream = BrokenCleanup()

    monkeypatch.setattr(backend, "_open_native_system", open_system)
    monkeypatch.setattr(
        backend,
        "_open_microphone",
        lambda *args: (_ for _ in ()).throw(RuntimeError("microphone disappeared")),
    )

    with pytest.raises(CaptureError, match="microphone disappeared"):
        backend.start(str(tmp_path), None, None)


def test_stop_finalizes_paths_even_when_native_stream_cleanup_fails():
    class BrokenStream:
        def stop(self):
            raise RuntimeError("stop failed")

        def close(self):
            raise RuntimeError("close failed")

    backend = MacOSBackend()
    backend._is_recording = True
    backend._system_stream = BrokenStream()
    backend._system_path = "system.wav"
    backend._mic_path = "mic.wav"

    assert backend.stop() == ("system.wav", "mic.wav")
    assert backend._system_stream is None


def test_missing_screen_capture_permission_is_requested(monkeypatch):
    requested = []
    stream = native.ScreenCaptureAudioStream(lambda data: None)
    monkeypatch.setattr(native, "screen_capture_permission_granted", lambda: False)
    monkeypatch.setattr(
        native, "request_screen_capture_permission", lambda: requested.append(True) or False
    )

    with pytest.raises(RuntimeError, match="Privacy & Security"):
        stream.start()

    assert requested == [True]


@pytest.mark.skipif(sys.platform != "darwin", reason="ScreenCaptureKit exists only on macOS")
def test_stream_output_delegate_class_is_reused():
    assert native._stream_output_class() is native._stream_output_class()
