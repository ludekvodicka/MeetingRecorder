"""Which programs play on which output device right now.

Windows keeps an audio session for every program that opens a stream on an output device,
with its process and a peak meter. A browser plays through its own audio service process,
a child of the browser, so a session is matched by the exe names of its process and of the
process's parents. One process can hold sessions on several devices: each browser tab may
choose its own output.
"""

import logging
import os
import sys
from dataclasses import dataclass

log = logging.getLogger(__name__)

# AudioSessionStateActive: the program has an open, running stream on the device.
SESSION_ACTIVE = 1


@dataclass(frozen=True)
class PlayingSession:
    programs: frozenset[str]
    peak: float


@dataclass(frozen=True)
class OutputActivity:
    """An output device by the name its loopback carries, without " [Loopback]"."""

    name: str
    sessions: tuple[PlayingSession, ...] = ()

    def peak(self, programs=None):
        """Loudest session, of the given programs only when programs is a set."""
        return max(
            (s.peak for s in self.sessions if programs is None or s.programs & programs),
            default=0.0,
        )

    def plays(self, programs):
        return any(session.programs & programs for session in self.sessions)


class WindowsOutputMonitor:
    def __init__(self):
        # A device name costs a read of its whole property store, about 15 ms per device, so
        # it is read once per endpoint ID. A look then takes a few milliseconds.
        self._names = {}

    def outputs(self):
        """Active sessions on every active output; raises OSError when Windows refuses."""
        import comtypes
        from pycaw.constants import DEVICE_STATE, CLSID_MMDeviceEnumerator, EDataFlow
        from pycaw.pycaw import (
            IAudioMeterInformation,
            IAudioSessionControl2,
            IAudioSessionManager2,
            IMMDeviceEnumerator,
        )
        from pycaw.utils import AudioUtilities

        own = os.getpid()
        result = []
        try:
            enumerator = comtypes.CoCreateInstance(
                CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER)
            devices = enumerator.EnumAudioEndpoints(
                EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value)
            for device_index in range(devices.GetCount()):
                device = devices.Item(device_index)
                key = device.GetId()
                if key not in self._names:
                    self._names[key] = AudioUtilities.CreateDevice(device).FriendlyName
                manager = device.Activate(
                    IAudioSessionManager2._iid_, comtypes.CLSCTX_ALL, None,
                ).QueryInterface(IAudioSessionManager2)
                sessions = []
                listed = manager.GetSessionEnumerator()
                for index in range(listed.GetCount()):
                    control = listed.GetSession(index).QueryInterface(
                        IAudioSessionControl2)
                    pid = control.GetProcessId()
                    if pid in (0, own) or control.GetState() != SESSION_ACTIVE:
                        continue
                    peak = control.QueryInterface(IAudioMeterInformation).GetPeakValue()
                    sessions.append(PlayingSession(_programs(pid), float(peak)))
                result.append(OutputActivity(self._names[key], tuple(sessions)))
        except Exception as error:
            # comtypes raises COMError, which is no OSError; callers handle one kind.
            raise OSError(f"audio sessions could not be read: {error}") from error
        return tuple(result)


def _programs(pid):
    """Lowercase exe names of the process and its parents."""
    import psutil

    try:
        process = psutil.Process(pid)
        return frozenset(item.name().casefold() for item in [process, *process.parents()])
    except psutil.Error:
        return frozenset()


def create_output_monitor(platform_name=None):
    """A monitor on Windows, None where the recorder cannot switch its system source."""
    platform_name = sys.platform if platform_name is None else platform_name
    return WindowsOutputMonitor() if platform_name == "win32" else None
