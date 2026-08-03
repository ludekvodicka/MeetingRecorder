"""macOS capture boundary.

The first implementation intentionally preserves the routed-input behavior that existing
users rely on: a virtual device such as BlackHole is selected as the system source and the
microphone remains a separate PortAudio input. Native system capture can now be added behind
this class without changing the generic PortAudio backend used by Linux.
"""

from audiorecorder.audio.backend_portaudio import PortAudioBackend


class MacOSBackend(PortAudioBackend):
    """macOS backend with the legacy virtual-device path as its compatibility base."""
