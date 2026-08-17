import pytest

from audiorecorder.audio import backend_portaudio
from audiorecorder.audio.backend_portaudio import PortAudioBackend
from audiorecorder.audio.device_selection import (
    DeviceResolutionError,
    default_input_device,
    find_input_device,
    macos_builtin_microphones,
    resolve_microphone,
)


class FakePortAudioError(Exception):
    pass


class FakeDefault:
    def __init__(self, index):
        self.device = (index, 9)


class FakeAudioApi:
    PortAudioError = FakePortAudioError

    def __init__(self, devices, default=0):
        self.devices = devices
        self.default = FakeDefault(default)

    def query_devices(self, index=None):
        return self.devices if index is None else self.devices[index]


DEVICES = [
    {"name": "BlackHole 2ch", "max_input_channels": 2, "default_samplerate": 48000},
    {"name": "MacBook Microphone", "max_input_channels": 1, "default_samplerate": 48000},
    {"name": "Speakers", "max_input_channels": 0, "default_samplerate": 48000},
]


def test_named_input_is_resolved_without_using_the_default():
    api = FakeAudioApi(DEVICES, default=1)
    assert find_input_device(api, "BlackHole 2ch")[0] == 0


def test_missing_optional_input_is_not_replaced_by_the_default_microphone():
    api = FakeAudioApi(DEVICES, default=1)
    assert find_input_device(api, "Unplugged BlackHole") is None


def test_portaudio_system_adapter_preserves_missing_as_none(monkeypatch):
    api = FakeAudioApi(DEVICES, default=1)
    monkeypatch.setattr(backend_portaudio, "sd", api)

    assert PortAudioBackend()._resolve_system_source("Unplugged BlackHole") is None


def test_default_input_uses_the_os_default_not_the_first_input():
    api = FakeAudioApi(DEVICES, default=1)
    index, info = default_input_device(api)
    assert index == 1
    assert info["name"] == "MacBook Microphone"


def test_missing_saved_microphone_falls_back_to_the_os_default():
    api = FakeAudioApi(DEVICES, default=1)
    assert resolve_microphone(api, "Unplugged headset")[0] == 1


def test_portaudio_microphone_adapter_uses_the_os_default(monkeypatch):
    api = FakeAudioApi(DEVICES, default=1)
    monkeypatch.setattr(backend_portaudio, "sd", api)

    assert PortAudioBackend()._resolve_microphone(None)[0] == 1


def test_truncated_device_names_can_be_matched_when_requested():
    api = FakeAudioApi(DEVICES)
    found = find_input_device(api, "MacBook Micro", allow_prefix=True)
    assert found[1]["name"] == "MacBook Microphone"


def test_macos_fallbacks_include_only_builtin_hardware_microphones():
    devices = [
        {"name": "Luboš’s AirPods Pro", "max_input_channels": 1},
        {"name": "MacBook Air Microphone", "max_input_channels": 1},
        {"name": "Microsoft Teams Audio", "max_input_channels": 1},
        {"name": "BlackHole 2ch", "max_input_channels": 2},
        {"name": "Studio Display Microphone", "max_input_channels": 1},
    ]

    assert [info["name"] for _, info in macos_builtin_microphones(
        FakeAudioApi(devices), excluded_indices={4},
    )] == ["MacBook Air Microphone"]


@pytest.mark.parametrize("index", [None, -1])
def test_missing_default_is_an_explicit_error(index):
    with pytest.raises(DeviceResolutionError, match="No default input"):
        default_input_device(FakeAudioApi(DEVICES, default=index))
