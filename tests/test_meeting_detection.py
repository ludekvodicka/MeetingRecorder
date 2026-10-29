import os
import sys
from types import SimpleNamespace

from audiorecorder.meetings.detection import (
    MacOSMeetingDetector,
    MeetingCandidate,
    MeetingPromptPolicy,
    NullMeetingDetector,
    TeamsMediaDetector,
    candidates_from_windows,
    create_meeting_detector,
)


def window(owner, title, *, pid=1, window_id=2, layer=0, on_screen=True, bounds=None):
    return {
        "owner": owner,
        "title": title,
        "pid": pid,
        "window_id": window_id,
        "layer": layer,
        "on_screen": on_screen,
        "bounds": bounds or {},
    }


class TestNativeClassification:
    def test_teams_meeting_is_detected(self):
        found = candidates_from_windows([
            window("Microsoft Teams", "Weekly deployment meeting")])
        assert found[0].app_name == "Microsoft Teams"

    def test_modern_teams_subject_window_is_detected(self):
        found = candidates_from_windows([window(
            "Microsoft Teams",
            "WP EBSI & Smart Contract v1.0 | CTU | haraslub@cvut.cz | Microsoft Teams",
        )])
        assert found[0].app_name == "Microsoft Teams"

    def test_zoom_meeting_is_detected(self):
        found = candidates_from_windows([
            window("zoom.us", "Zoom Meeting", window_id=3)])
        assert found[0].app_name == "Zoom"

    def test_webex_and_slack_huddles_are_detected(self):
        found = candidates_from_windows([
            window("Cisco Webex", "Personal Room meeting", window_id=3),
            window("Slack", "Engineering huddle", window_id=4),
        ])
        assert {candidate.app_name for candidate in found} == {"Webex", "Slack Huddle"}

    def test_an_ordinary_teams_window_does_not_interrupt_the_user(self):
        assert candidates_from_windows([
            window("Microsoft Teams", "Chat | Albert Švehla")]) == []

    def test_full_modern_teams_navigation_titles_are_not_calls(self):
        for section in ("Activity", "Calendar", "Calls", "Chat", "Teams"):
            title = f"{section} | CTU | haraslub@cvut.cz | Microsoft Teams"
            assert candidates_from_windows([window("Microsoft Teams", title)]) == []

    def test_chat_named_after_a_meeting_is_not_treated_as_a_call(self):
        assert candidates_from_windows([window(
            "Microsoft Teams",
            "Chat | Weekly meeting | CTU | haraslub@cvut.cz | Microsoft Teams",
        )]) == []

    def test_ambiguous_channel_title_is_not_treated_as_a_call(self):
        assert candidates_from_windows([window(
            "Microsoft Teams",
            "General | Engineering | CTU | haraslub@cvut.cz | Microsoft Teams",
        )]) == []

    def test_teams_call_controls_work_when_macos_redacts_the_title(self):
        found = candidates_from_windows([window(
            "Microsoft Teams",
            "",
            layer=26,
            bounds={"Width": 1710, "Height": 39},
        )])
        assert found == [MeetingCandidate(
            "1:teams-call-controls",
            "Microsoft Teams",
            "Active Microsoft Teams call",
        )]

    def test_small_teams_notification_overlay_is_not_a_call(self):
        assert candidates_from_windows([window(
            "Microsoft Teams (Notification Center)",
            "Window",
            layer=20,
            bounds={"Width": 320, "Height": 400},
        )]) == []

    def test_call_controls_do_not_duplicate_a_titled_teams_candidate(self):
        found = candidates_from_windows([
            window("Microsoft Teams", "Weekly meeting", window_id=3),
            window(
                "Microsoft Teams",
                "",
                layer=26,
                window_id=4,
                bounds={"Width": 1710, "Height": 39},
            ),
        ])
        assert len(found) == 1
        assert found[0].key == "1:3"

    def test_background_and_overlay_windows_are_ignored(self):
        assert candidates_from_windows([
            window("zoom.us", "Zoom Meeting", layer=8),
            window("zoom.us", "Zoom Meeting", on_screen=False, window_id=3),
        ]) == []

    def test_browser_meet_is_left_for_the_browser_integration(self):
        assert candidates_from_windows([
            window("Google Chrome", "Standup - Google Meet")]) == []


class TestPromptPolicy:
    candidate = MeetingCandidate("1:2", "Zoom", "Zoom Meeting")

    def test_one_observation_is_not_enough_for_a_popup(self):
        policy = MeetingPromptPolicy(observations_required=2)
        assert policy.next_prompt([self.candidate]) is None

    def test_a_stable_meeting_prompts_once(self):
        policy = MeetingPromptPolicy(observations_required=2)
        policy.next_prompt([self.candidate])
        assert policy.next_prompt([self.candidate]) == self.candidate
        assert policy.next_prompt([self.candidate]) is None

    def test_the_same_window_can_prompt_again_after_it_disappears(self):
        policy = MeetingPromptPolicy(observations_required=1)
        assert policy.next_prompt([self.candidate]) == self.candidate
        policy.next_prompt([])
        assert policy.next_prompt([self.candidate]) == self.candidate

    def test_an_active_recording_suppresses_the_current_meeting(self):
        policy = MeetingPromptPolicy(observations_required=1)
        assert policy.next_prompt([self.candidate], blocked=True) is None
        assert policy.next_prompt([self.candidate], blocked=False) is None


def test_other_platforms_use_a_no_op_detector():
    detector = create_meeting_detector("linux")
    assert isinstance(detector, NullMeetingDetector)
    assert detector.scan() == []
    assert detector.diagnostics().teams_media == "not supported"


def test_macos_uses_the_native_window_detector():
    assert isinstance(create_meeting_detector("darwin"), MacOSMeetingDetector)


def test_native_scan_retries_after_a_transient_quartz_failure(monkeypatch):
    calls = [RuntimeError("temporary failure"), [{
        "owner": "zoom.us",
        "title": "Zoom Meeting",
        "pid": 1,
        "window_id": 2,
        "layer": 0,
        "on_screen": True,
    }]]

    def copy_windows(*_args):
        result = calls.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    quartz = SimpleNamespace(
        CGWindowListCopyWindowInfo=copy_windows,
        kCGWindowListOptionOnScreenOnly=1,
        kCGWindowListExcludeDesktopElements=2,
        kCGNullWindowID=0,
        kCGWindowOwnerName="owner",
        kCGWindowName="title",
        kCGWindowOwnerPID="pid",
        kCGWindowNumber="window_id",
        kCGWindowLayer="layer",
        kCGWindowIsOnscreen="on_screen",
        kCGWindowBounds="bounds",
    )
    monkeypatch.setitem(sys.modules, "Quartz", quartz)

    class MediaDetector:
        state = "unavailable"

        def scan(self):
            return []

    detector = MacOSMeetingDetector(MediaDetector())
    assert detector.scan() == []
    assert not detector.diagnostics().native_available
    assert detector.scan()[0].app_name == "Zoom"
    assert detector.diagnostics().native_available


class TestTeamsMediaDetection:
    def test_repeated_media_writes_identify_an_active_call(self, tmp_path):
        now = [100.0]
        media = tmp_path / "media-stack"
        media.mkdir()
        trace = media / "rtmcontrol.blog"
        trace.write_bytes(b"first")
        os.utime(trace, (now[0], now[0]))
        detector = TeamsMediaDetector([media], clock=lambda: now[0])

        assert detector.scan() == []
        now[0] = 101.0
        trace.write_bytes(b"second")
        os.utime(trace, (now[0], now[0]))
        assert detector.scan() == []
        now[0] = 102.0
        trace.write_bytes(b"third")
        os.utime(trace, (now[0], now[0]))
        found = detector.scan()

        assert found == [MeetingCandidate(
            "teams:media-stack",
            "Microsoft Teams",
            "Active Microsoft Teams call",
            source="teams-media",
        )]
        assert detector.state == "active"
        now[0] = 123.0
        assert detector.scan() == []
        assert detector.state == "idle"

    def test_one_isolated_write_does_not_create_a_false_prompt(self, tmp_path):
        now = [100.0]
        trace = tmp_path / "rtmcontrol.blog"
        trace.write_bytes(b"first")
        os.utime(trace, (now[0], now[0]))
        detector = TeamsMediaDetector([tmp_path], clock=lambda: now[0])
        assert detector.scan() == []

        now[0] = 101.0
        trace.write_bytes(b"second")
        os.utime(trace, (now[0], now[0]))
        assert detector.scan() == []
        now[0] = 122.0
        assert detector.scan() == []
        assert detector.state == "idle"

    def test_missing_media_directory_is_reported_without_failure(self, tmp_path):
        detector = TeamsMediaDetector([tmp_path / "missing"])
        assert detector.scan() == []
        assert detector.state == "unavailable"
