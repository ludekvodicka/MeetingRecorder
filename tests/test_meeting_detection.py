from audiorecorder.meetings.detection import (
    MacOSMeetingDetector,
    MeetingCandidate,
    MeetingPromptPolicy,
    NullMeetingDetector,
    candidates_from_windows,
    create_meeting_detector,
)


def window(owner, title, *, pid=1, window_id=2, layer=0, on_screen=True):
    return {
        "owner": owner,
        "title": title,
        "pid": pid,
        "window_id": window_id,
        "layer": layer,
        "on_screen": on_screen,
    }


class TestNativeClassification:
    def test_teams_meeting_is_detected(self):
        found = candidates_from_windows([
            window("Microsoft Teams", "Weekly deployment meeting")])
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


def test_macos_uses_the_native_window_detector():
    assert isinstance(create_meeting_detector("darwin"), MacOSMeetingDetector)
