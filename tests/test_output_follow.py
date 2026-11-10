"""The system track follows the output device the call plays on.

On 2026-11-10 a Google Meet call in Brave played into Bluetooth headphones chosen in Meet,
while the recorder recorded the speakers, the Windows default. The other side was missing
from the recording. The recorder now reads which program plays on which device.
"""

# The window fixtures come from test_rts_toggle and test_call_watch.
# ruff: noqa: F401, F811
from test_call_watch import FakeMicrophone, automatic
from test_rts_toggle import app, window

from audiorecorder.audio.output_activity import (
    OutputActivity,
    PlayingSession,
    create_output_monitor,
)
from audiorecorder.audio.output_follow import SILENT_SECONDS, OutputChoice, OutputFollower
from audiorecorder.meetings.microphone_use import classify

BRAVE = frozenset({"brave.exe"})


def output(name, *sessions):
    return OutputActivity(name, tuple(PlayingSession(frozenset(p), peak) for p, peak in sessions))


SPEAKERS = output("Speakers")
HEADPHONES_CALL = output("Headphones", (("brave.exe",), 0.2))


def test_the_call_program_is_followed_to_the_device_it_plays_on():
    follower = OutputFollower(0.0)
    choice = follower.choose("Speakers", (SPEAKERS, HEADPHONES_CALL), BRAVE, 1.0)
    assert choice == OutputChoice("Headphones", "call")


def test_no_switch_while_the_call_also_plays_on_the_recorded_device():
    follower = OutputFollower(0.0)
    both = output("Speakers", (("brave.exe",), 0.0))
    assert follower.choose("Speakers", (both, HEADPHONES_CALL), BRAVE, 1.0) is None


def test_the_loudest_device_wins_when_the_call_plays_on_several():
    follower = OutputFollower(0.0)
    quiet = output("Monitor", (("brave.exe",), 0.01))
    choice = follower.choose("Speakers", (SPEAKERS, quiet, HEADPHONES_CALL), BRAVE, 1.0)
    assert choice.name == "Headphones"


def test_a_parent_process_counts_as_the_program():
    # A browser plays through its audio service, a child process of the same exe; Teams may
    # play through a WebView2 child.
    follower = OutputFollower(0.0)
    child = output("Headphones", (("msedgewebview2.exe", "ms-teams.exe"), 0.1))
    choice = follower.choose("Speakers", (SPEAKERS, child), frozenset({"ms-teams.exe"}), 1.0)
    assert choice.name == "Headphones"


def test_silence_while_you_speak_moves_to_the_device_that_plays():
    follower = OutputFollower(0.0)
    music = output("Headphones", (("unknown.exe",), 0.3))
    follower.mic_level(0.5, SILENT_SECONDS - 1)
    assert follower.choose("Speakers", (SPEAKERS, music), frozenset(), SILENT_SECONDS - 1) is None

    choice = follower.choose("Speakers", (SPEAKERS, music), frozenset(), SILENT_SECONDS)
    assert choice == OutputChoice("Headphones", "silence")
    # The new device gets its own silence period before the next silence switch.
    assert follower.choose("Headphones", (SPEAKERS, music), frozenset(), SILENT_SECONDS + 1) is None


def test_silence_without_your_speech_or_without_sound_elsewhere_stays():
    music = output("Headphones", (("unknown.exe",), 0.3))
    quiet = output("Headphones", (("unknown.exe",), 0.0))
    late = SILENT_SECONDS * 3

    nobody_spoke = OutputFollower(0.0)
    assert nobody_spoke.choose("Speakers", (SPEAKERS, music), frozenset(), late) is None

    long_ago = OutputFollower(0.0)
    long_ago.mic_level(0.5, 1.0)
    assert long_ago.choose("Speakers", (SPEAKERS, music), frozenset(), late) is None

    no_sound = OutputFollower(0.0)
    no_sound.mic_level(0.5, late - 1)
    assert no_sound.choose("Speakers", (SPEAKERS, quiet), frozenset(), late) is None


def test_sound_on_the_recorded_device_resets_the_silence():
    follower = OutputFollower(0.0)
    music = output("Headphones", (("unknown.exe",), 0.3))
    follower.mic_level(0.5, SILENT_SECONDS)
    follower.system_level(0.5, SILENT_SECONDS - 5)
    assert follower.choose("Speakers", (SPEAKERS, music), frozenset(), SILENT_SECONDS) is None


def test_the_call_programs_are_meeting_apps_and_browsers_with_packages_mapped():
    use = classify({"brave.exe", "python.exe", "msteams_8wekyb3d8bbwe", "zoom.exe"})
    assert use.call_programs == frozenset({"brave.exe", "ms-teams.exe", "zoom.exe"})


def test_only_windows_has_an_output_monitor():
    assert create_output_monitor("win32") is not None
    assert create_output_monitor("darwin") is None
    assert create_output_monitor("linux") is None


class FakeOutputs:
    def __init__(self, *outputs):
        self.current = outputs

    def outputs(self):
        return self.current


TEAMS_ON_HEADPHONES = output("Headphones", (("ms-teams.exe",), 0.2))


def recording_window(automatic, outputs, switch):
    """A recording started by a Teams call, with the Teams call's capture switchable."""
    w = automatic()
    w._outputs = outputs
    w._microphone.programs = {"ms-teams.exe"}
    w._watch_call()
    assert w._is_recording
    capture = w.captures[-1]
    capture.switch_system_source = lambda name: switch(capture, name)
    return w, capture


def test_a_recording_switches_to_the_device_the_call_plays_on(automatic):
    outputs = FakeOutputs(SPEAKERS)
    switched = []

    def switch(capture, name):
        switched.append(name)
        capture.active_system_source = name

    w, capture = recording_window(automatic, outputs, switch)
    assert switched == []

    outputs.current = (SPEAKERS, TEAMS_ON_HEADPHONES)
    w._watch_call()
    assert switched == ["Headphones"]
    assert w._level_bars._system.device.text() == "Headphones"
    log = w._call_log.path.read_text(encoding="utf-8")
    assert "system audio now from Headphones, because the call plays there" in log
    assert "call-audio=Headphones" in log

    w._watch_call()
    assert switched == ["Headphones"]


def test_a_device_that_cannot_open_is_not_tried_again(automatic):
    from audiorecorder.audio.backend import CaptureError

    outputs = FakeOutputs(SPEAKERS, TEAMS_ON_HEADPHONES)
    tried = []

    def switch(capture, name):
        tried.append(name)
        raise CaptureError("device busy")

    w, capture = recording_window(automatic, FakeOutputs(SPEAKERS), switch)
    w._outputs = outputs
    w._watch_call()
    w._watch_call()
    assert tried == ["Headphones"]
    assert capture.active_system_source == "Speakers"
    assert "Headphones could not open: device busy" in w._call_log.path.read_text(
        encoding="utf-8")
