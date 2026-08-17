"""Shared input-device resolution for recording and dictation.

PortAudio device indexes are ephemeral, so configuration stores names. Resolution happens
at the moment a stream opens and deliberately distinguishes two policies:

* microphones may fall back to the operating-system default when a saved device vanished;
* optional routed system sources must not fall back to a microphone and pretend that it is
  system audio.
"""


class DeviceResolutionError(RuntimeError):
    """An input device could not be resolved from the audio API."""


def same_device_name(candidate, configured, allow_prefix=False):
    if candidate == configured:
        return True
    return bool(
        allow_prefix
        and configured
        and (candidate.startswith(configured) or configured.startswith(candidate))
    )


def find_input_device(audio_api, configured_name, allow_prefix=False):
    """Return ``(index, info)`` for a named input, or ``None`` when it is absent."""
    if not configured_name:
        return None
    for index, info in enumerate(audio_api.query_devices()):
        if info["max_input_channels"] <= 0:
            continue
        if same_device_name(info["name"], configured_name, allow_prefix=allow_prefix):
            return index, info
    return None


def default_input_device(audio_api):
    """Return the operating-system default input and validate that it is usable."""
    try:
        index = audio_api.default.device[0]
        if index is None or index < 0:
            raise DeviceResolutionError("No default input device is configured.")
        info = audio_api.query_devices(index)
    except DeviceResolutionError:
        raise
    except (audio_api.PortAudioError, ValueError, TypeError, IndexError) as err:
        raise DeviceResolutionError(f"The default input device is unavailable: {err}") from err

    if info["max_input_channels"] <= 0:
        raise DeviceResolutionError("The default audio device has no input channels.")
    return index, info


def resolve_microphone(audio_api, configured_name, allow_prefix=False):
    """Resolve a saved microphone, falling back to the real OS default when necessary."""
    configured = find_input_device(
        audio_api, configured_name, allow_prefix=allow_prefix,
    )
    return configured or default_input_device(audio_api)


def macos_builtin_microphones(audio_api, excluded_indices=()):
    """Return safe built-in macOS microphone fallbacks in device-list order.

    Bluetooth microphones can temporarily reject another client while a meeting app is
    switching profiles. Do not fall back to arbitrary inputs such as BlackHole or Teams
    Audio: only names CoreAudio gives to Apple hardware microphones are eligible.
    """
    excluded = set(excluded_indices)
    markers = ("macbook", "imac", "built-in", "studio display")
    candidates = []
    for index, info in enumerate(audio_api.query_devices()):
        name = str(info.get("name", ""))
        normalized = name.casefold()
        if (
            index not in excluded
            and info.get("max_input_channels", 0) > 0
            and "microphone" in normalized
            and any(marker in normalized for marker in markers)
        ):
            candidates.append((index, info))
    return candidates
