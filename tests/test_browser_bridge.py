import http.client
import json

import pytest

from audiorecorder.meetings.browser_bridge import (
    BRIDGE_HEADER,
    BRIDGE_HEADER_VALUE,
    BRIDGE_PATH,
    BrowserMeetingBridge,
)
from audiorecorder.meetings.detection import CombinedMeetingDetector, MeetingCandidate


@pytest.fixture
def bridge():
    running = BrowserMeetingBridge(port=0)
    assert running.start()
    yield running
    running.stop()


def post(bridge, payload, *, origin="chrome-extension://test-id", header=True):
    connection = http.client.HTTPConnection(bridge.host, bridge.port, timeout=2)
    headers = {"Content-Type": "application/json"}
    if origin is not None:
        headers["Origin"] = origin
    if header:
        headers[BRIDGE_HEADER] = BRIDGE_HEADER_VALUE
    connection.request("POST", BRIDGE_PATH, json.dumps(payload), headers)
    response = connection.getresponse()
    status = response.status
    response.read()
    connection.close()
    return status


MEETING = {
    "key": "meet.google.com/abc-defg-hij",
    "app_name": "Google Meet",
    "title": "Project sync",
    "active": True,
}


class TestAuthenticatedLoopbackEndpoint:
    def test_an_extension_heartbeat_becomes_a_candidate(self, bridge):
        assert post(bridge, MEETING) == 204
        assert bridge.scan() == [MeetingCandidate(
            "browser:meet.google.com/abc-defg-hij",
            "Google Meet",
            "Project sync",
            source="browser-extension",
        )]

    def test_an_ordinary_website_origin_is_rejected(self, bridge):
        assert post(bridge, MEETING, origin="https://malicious.example") == 403
        assert bridge.scan() == []

    def test_extension_requests_without_an_origin_are_supported(self, bridge):
        assert post(bridge, MEETING, origin=None) == 204

    def test_the_custom_extension_header_is_required(self, bridge):
        assert post(bridge, MEETING, header=False) == 403

    def test_disconnect_removes_the_candidate_immediately(self, bridge):
        assert post(bridge, MEETING) == 204
        assert post(bridge, {**MEETING, "active": False}) == 204
        assert bridge.scan() == []

    def test_malformed_payload_is_rejected_without_killing_the_server(self, bridge):
        connection = http.client.HTTPConnection(bridge.host, bridge.port, timeout=2)
        connection.request("POST", BRIDGE_PATH, b"not-json", {
            "Origin": "chrome-extension://test-id",
            BRIDGE_HEADER: BRIDGE_HEADER_VALUE,
        })
        assert connection.getresponse().status == 400
        connection.close()
        assert post(bridge, MEETING) == 204


def test_stale_heartbeats_expire():
    now = [100.0]
    bridge = BrowserMeetingBridge(heartbeat_ttl=10, clock=lambda: now[0])
    bridge.update(MEETING)
    now[0] = 111.0
    assert bridge.scan() == []


def test_text_is_collapsed_and_bounded():
    bridge = BrowserMeetingBridge()
    bridge.update({
        "key": "x",
        "app_name": "Google\nMeet",
        "title": "  Project\n sync " + "x" * 500,
    })
    candidate = bridge.scan()[0]
    assert candidate.app_name == "Google Meet"
    assert "\n" not in candidate.title
    assert len(candidate.title) == 300


def test_combined_detector_preserves_native_and_browser_signals():
    class Detector:
        def __init__(self, candidates):
            self.candidates = candidates

        def scan(self):
            return self.candidates

    native = MeetingCandidate("native:1", "Zoom", "Meeting")
    browser = MeetingCandidate("browser:2", "Google Meet", "Sync")
    combined = CombinedMeetingDetector([Detector([native]), Detector([browser])])
    assert combined.scan() == [native, browser]
