"""Which programs hold the microphone right now, read from the Windows privacy records.

Windows records every program that opens the microphone under the CapabilityAccessManager
consent store, the data behind the microphone icon in the taskbar. While a program holds
the microphone its LastUsedTimeStop is 0. Only program names leave this module.
"""

import sys
from dataclasses import dataclass

CONSENT_STORE = (
    r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
    r"\ConsentStore\microphone"
)
NON_PACKAGED = "NonPackaged"

BROWSERS = frozenset({
    "brave.exe", "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "vivaldi.exe",
    "arc.exe",
})
# Desktop programs whose only use of the microphone is a call.
MEETING_APPS = frozenset({
    "ms-teams.exe", "teams.exe", "zoom.exe", "webex.exe", "ciscocollabhost.exe",
    "slack.exe",
})
# Store packages are recorded by package family name; new Teams is MSTeams_8wekyb3d8bbwe.
MEETING_PACKAGES = ("msteams_",)
# The program a meeting package plays the call through, to find it among audio sessions.
PACKAGE_PROGRAMS = {"msteams_": "ms-teams.exe"}


@dataclass(frozen=True)
class MicrophoneUse:
    meeting_app: bool
    browser: bool
    programs: tuple[str, ...] = ()

    @property
    def call_programs(self):
        """Exe names of the meeting apps and browsers on the microphone, packages mapped."""
        found = set()
        for name in self.programs:
            if name in MEETING_APPS or name in BROWSERS:
                found.add(name)
            for prefix, program in PACKAGE_PROGRAMS.items():
                if name.startswith(prefix):
                    found.add(program)
        return frozenset(found)


def classify(programs):
    """Programs holding the microphone, lowercase exe or package names, as call evidence."""
    programs = tuple(sorted(programs))
    return MicrophoneUse(
        meeting_app=any(
            name in MEETING_APPS or name.startswith(MEETING_PACKAGES) for name in programs),
        browser=any(name in BROWSERS for name in programs),
        programs=programs,
    )


def _in_use(winreg, key):
    try:
        stop, _kind = winreg.QueryValueEx(key, "LastUsedTimeStop")
        start, _kind = winreg.QueryValueEx(key, "LastUsedTimeStart")
    except OSError:
        return False
    return stop == 0 and start != 0


def _subkeys(winreg, key):
    index = 0
    while True:
        try:
            yield winreg.EnumKey(key, index)
        except OSError:
            return
        index += 1


def programs_using_microphone():
    """Lowercase exe names (desktop programs) and package names (Store apps) in use now."""
    import winreg

    found = set()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CONSENT_STORE) as store:
        for name in _subkeys(winreg, store):
            with winreg.OpenKey(store, name) as key:
                if name == NON_PACKAGED:
                    # Desktop programs are keyed by their full path with '#' for '\'.
                    for path in _subkeys(winreg, key):
                        with winreg.OpenKey(key, path) as program:
                            if _in_use(winreg, program):
                                found.add(path.rsplit("#", 1)[-1].casefold())
                elif _in_use(winreg, key):
                    found.add(name.casefold())
    return found


class WindowsMicrophoneMonitor:
    def use(self):
        """The current MicrophoneUse; raises OSError when the records cannot be read."""
        return classify(programs_using_microphone())


def create_microphone_monitor(platform_name=None):
    """A monitor on Windows, None where the system does not say who holds the microphone."""
    platform_name = sys.platform if platform_name is None else platform_name
    return WindowsMicrophoneMonitor() if platform_name == "win32" else None
