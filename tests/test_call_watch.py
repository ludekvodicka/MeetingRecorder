"""Calls start and stop recordings by themselves, from who holds the microphone.

The microphone records of Windows are exact: a call app or a meeting browser holds the
microphone for as long as the call runs. A meeting window alone is not exact and only asks.
"""

# The window fixtures come from test_rts_toggle; pytest passes them in by parameter name.
# ruff: noqa: F401, F811
import sys
from types import SimpleNamespace

import pytest
from test_rts_toggle import app, window

from audiorecorder.meetings import microphone_use
from audiorecorder.meetings.call_end import CallEndWatch
from audiorecorder.meetings.call_log import CallLog
from audiorecorder.meetings.call_watch import CallWatch
from audiorecorder.meetings.microphone_use import MicrophoneUse, classify

TEAMS = classify({"ms-teams.exe"})
BROWSER = classify({"brave.exe"})
NOBODY = classify(set())


def test_programs_are_sorted_into_meeting_apps_and_browsers():
    assert classify({"ms-teams.exe"}) == MicrophoneUse(True, False, ("ms-teams.exe",))
    assert classify({"msteams_8wekyb3d8bbwe"}).meeting_app
    assert classify({"brave.exe", "python.exe"}) == MicrophoneUse(
        False, True, ("brave.exe", "python.exe"))
    assert classify({"meetingrecorder.exe", "telegram.exe"}) == MicrophoneUse(
        False, False, ("meetingrecorder.exe", "telegram.exe"))


class FakeKey:
    def __init__(self, values=None, children=None):
        self.values = values or {}
        self.children = children or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def fake_winreg(store):
    def open_key(parent, name):
        return store if parent == "HKCU" else parent.children[name]

    def enum_key(key, index):
        names = list(key.children)
        if index >= len(names):
            raise OSError("no more keys")
        return names[index]

    def query(key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        return key.values[name], 11

    return SimpleNamespace(
        HKEY_CURRENT_USER="HKCU", OpenKey=open_key, EnumKey=enum_key, QueryValueEx=query)


def test_the_consent_store_names_the_programs_that_hold_the_microphone(monkeypatch):
    holding = {"LastUsedTimeStart": 134384253204175744, "LastUsedTimeStop": 0}
    released = {"LastUsedTimeStart": 134384253204175744, "LastUsedTimeStop": 134384289950000000}
    store = FakeKey(children={
        "MSTeams_8wekyb3d8bbwe": FakeKey(holding),
        "Microsoft.WindowsCamera_8wekyb3d8bbwe": FakeKey(released),
        "NonPackaged": FakeKey(children={
            r"C:#Users#me#AppData#Local#BraveSoftware#Brave-Browser#Application#brave.exe":
                FakeKey(holding),
            r"C:#Program Files#Google#Chrome#Application#chrome.exe": FakeKey(released),
            r"C:#Tools#never-used.exe": FakeKey(),
        }),
    })
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg(store))

    assert microphone_use.programs_using_microphone() == {
        "msteams_8wekyb3d8bbwe", "brave.exe"}


def test_only_windows_has_a_microphone_monitor():
    assert microphone_use.create_microphone_monitor("win32") is not None
    assert microphone_use.create_microphone_monitor("darwin") is None


def test_a_meeting_app_on_the_microphone_is_a_call():
    watch = CallWatch(grace=30)
    assert watch.observe(TEAMS, False, False, 0)
    assert not watch.observe(NOBODY, True, False, 5)


def test_a_browser_is_a_call_only_when_a_meeting_window_is_shown_as_it_begins():
    watch = CallWatch(grace=30)
    assert not watch.observe(BROWSER, False, False, 0)
    assert watch.observe(BROWSER, True, False, 5)
    # The meeting tab may leave the screen; the browser's hold keeps the call.
    assert all(watch.observe(BROWSER, False, False, now) for now in range(10, 200, 5))


def test_a_browser_call_survives_a_short_release_but_not_the_grace_time():
    watch = CallWatch(grace=30)
    watch.observe(BROWSER, True, False, 0)
    assert not watch.observe(NOBODY, False, False, 10)
    assert watch.observe(BROWSER, False, False, 20)
    assert not watch.observe(NOBODY, False, False, 30)
    assert not watch.observe(NOBODY, False, False, 60)
    assert not watch.observe(BROWSER, False, False, 65)


def test_without_microphone_records_the_meeting_window_is_the_evidence():
    watch = CallWatch(grace=30)
    assert watch.observe(None, True, False, 0)
    assert not watch.observe(None, False, False, 5)
    assert not watch.should_start(True, exact=False)


def test_one_start_per_call_and_a_recording_in_the_call_counts_as_one():
    watch = CallWatch(grace=30)
    present = watch.observe(TEAMS, False, False, 0)
    assert watch.should_start(present, exact=True)
    watch.started()
    assert not watch.should_start(watch.observe(TEAMS, False, False, 5), exact=True)

    manual = CallWatch(grace=30)
    manual.observe(TEAMS, False, True, 0)
    assert not manual.should_start(manual.observe(TEAMS, False, False, 5), exact=True)


def test_a_new_call_after_the_grace_time_starts_again():
    watch = CallWatch(grace=30)
    watch.observe(TEAMS, False, False, 0)
    watch.started()
    watch.observe(NOBODY, False, False, 10)
    assert not watch.should_start(watch.observe(TEAMS, False, False, 20), exact=True)
    watch.observe(NOBODY, False, False, 25)
    watch.observe(NOBODY, False, False, 60)
    assert watch.should_start(watch.observe(TEAMS, False, False, 70), exact=True)


def test_the_call_log_writes_only_changes_and_no_titles(tmp_path):
    log = CallLog(tmp_path / "calls.log")
    log.record(False, NOBODY, False, "")
    log.record(False, NOBODY, False, "")
    log.record(True, BROWSER, True, "start")
    log.record(True, BROWSER, True, "")
    log.record(True, None, False, "")

    lines = (tmp_path / "calls.log").read_text(encoding="utf-8").splitlines()
    assert [line.split("  ", 1)[1] for line in lines] == [
        "call=no  microphone=none  meeting-window=no",
        "call=yes  microphone=brave.exe  meeting-window=yes  action=start",
        "call=yes  microphone=brave.exe  meeting-window=yes",
        "call=yes  microphone=unknown  meeting-window=no",
    ]


def test_the_call_log_names_where_the_call_plays_and_notes_switches(tmp_path):
    log = CallLog(tmp_path / "calls.log")
    log.record(True, BROWSER, True, "", "Headphones")
    log.record(True, BROWSER, True, "", "Headphones")
    log.note("system audio now from Headphones, because the call plays there")

    lines = (tmp_path / "calls.log").read_text(encoding="utf-8").splitlines()
    assert [line.split("  ", 1)[1] for line in lines] == [
        "call=yes  microphone=brave.exe  meeting-window=yes  call-audio=Headphones",
        "system audio now from Headphones, because the call plays there",
    ]


class FakeMicrophone:
    def __init__(self):
        self.programs = set()

    def use(self):
        return classify(self.programs)


@pytest.fixture
def automatic(window):
    def build(auto_stop=True):
        w = window(rts_translate=False, automatic_recording=True)
        w.config["auto_stop_recording"] = auto_stop
        w._microphone = FakeMicrophone()
        w._sync_call_timer()
        return w

    return build


def test_automatic_recording_watches_calls_between_recordings(automatic):
    w = automatic()
    assert w._call_timer.isActive()
    w.config["automatic_recording"] = False
    w._sync_call_timer()
    assert not w._call_timer.isActive()


def test_a_call_starts_a_recording_and_its_end_saves_it_without_asking(automatic):
    w = automatic()
    w._watch_call()
    assert not w._is_recording

    w._microphone.programs = {"ms-teams.exe"}
    w._watch_call()
    assert w._is_recording

    w._call_end = CallEndWatch(grace=0)
    w._watch_call()
    w._microphone.programs = set()
    w._watch_call()

    assert not w._is_recording
    assert w._call_end_prompt is None
    assert "call=yes  microphone=ms-teams.exe  meeting-window=no  action=start" in (
        w._call_log.path.read_text(encoding="utf-8"))


def test_a_manual_stop_during_the_call_is_not_undone(automatic):
    w = automatic()
    w._microphone.programs = {"ms-teams.exe"}
    w._watch_call()
    w._stop_recording()

    w._watch_call()

    assert not w._is_recording


def test_a_manual_recording_is_also_saved_when_the_call_ends(automatic):
    w = automatic()
    w._start_recording()
    w._call_end = CallEndWatch(grace=0)
    w._microphone.programs = {"zoom.exe"}
    w._watch_call()
    w._microphone.programs = set()
    w._watch_call()

    assert not w._is_recording


def test_without_automatic_stop_the_end_of_the_call_asks(automatic):
    w = automatic(auto_stop=False)
    w._microphone.programs = {"ms-teams.exe"}
    w._watch_call()
    w._call_end = CallEndWatch(grace=0)
    w._watch_call()
    w._microphone.programs = set()
    w._watch_call()

    assert w._is_recording
    assert w._call_end_prompt is not None


def test_a_call_during_dictation_starts_once_dictation_ends(automatic):
    w = automatic()
    w._microphone.programs = {"ms-teams.exe"}
    w._dictation_active = True
    w._watch_call()
    assert not w._is_recording

    w._dictation_active = False
    w._watch_call()
    assert w._is_recording


def test_without_microphone_records_a_meeting_window_never_starts_a_recording(automatic):
    w = automatic()
    w._microphone = None
    w._slide_source = SimpleNamespace(meeting_title=lambda: "Weekly | Microsoft Teams")
    w._watch_call()

    assert not w._is_recording
