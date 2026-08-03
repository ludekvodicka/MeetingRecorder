import sys

import pytest

from audiorecorder.audio.backend import CaptureError, create_backend
from audiorecorder.audio.backend_macos import MacOSBackend
from audiorecorder.audio.backend_portaudio import PortAudioBackend


def test_macos_has_a_dedicated_backend_boundary():
    backend = create_backend("darwin")
    try:
        assert type(backend) is MacOSBackend
    finally:
        backend.close()


def test_linux_keeps_the_generic_portaudio_backend():
    backend = create_backend("linux")
    try:
        assert type(backend) is PortAudioBackend
    finally:
        backend.close()


def test_unknown_platform_is_rejected_without_importing_a_native_backend():
    with pytest.raises(CaptureError, match="Unsupported platform: plan9"):
        create_backend("plan9")


@pytest.mark.skipif(sys.platform != "win32", reason="imports the Windows-only WASAPI library")
def test_windows_keeps_the_wasapi_backend():
    from audiorecorder.audio.backend_wasapi import WasapiBackend

    backend = create_backend("win32")
    try:
        assert type(backend) is WasapiBackend
    finally:
        backend.close()
