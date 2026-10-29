import sys
import threading
import time
from types import SimpleNamespace

from audiorecorder.meetings.calendar_detection import (
    CalendarEvent,
    MacOSCalendarDetector,
    NullCalendarDetector,
    calendar_candidates,
    create_calendar_detector,
)

NOW = 1_000_000.0


def event(**overrides):
    values = {
        "identifier": "event-1",
        "title": "Deployment sync",
        "start": NOW + 60,
        "end": NOW + 3600,
        "attendee_count": 2,
    }
    values.update(overrides)
    return CalendarEvent(**values)


class TestCandidateSelection:
    def test_an_attended_event_near_its_start_is_a_signal(self):
        found = calendar_candidates([event()], NOW)
        assert found[0].app_name == "Calendar"
        assert found[0].source == "calendar"

    def test_a_conference_link_is_enough_without_attendees(self):
        found = calendar_candidates([
            event(attendee_count=0, url="https://meet.google.com/abc-defg-hij")], NOW)
        assert found[0].app_name == "Calendar · Google Meet"

    def test_links_in_location_and_notes_are_recognized(self):
        found = calendar_candidates([
            event(identifier="zoom", attendee_count=0,
                  location="https://example.zoom.us/wc/join/123"),
            event(identifier="teams", attendee_count=0,
                  notes="Join https://teams.microsoft.com/l/meetup-join/abc"),
            event(identifier="teams-cloud", attendee_count=0,
                  notes="Join https://teams.cloud.microsoft/meet/abc"),
        ], NOW)
        assert {candidate.app_name for candidate in found} == {
            "Calendar · Zoom", "Calendar · Microsoft Teams"}

    def test_root_zoom_and_subdomain_webex_links_are_recognized(self):
        found = calendar_candidates([
            event(identifier="zoom", attendee_count=0, url="https://zoom.us/wc/join/1"),
            event(identifier="webex", attendee_count=0,
                  url="https://company.webex.com/meet/albert"),
        ], NOW)
        assert {candidate.app_name for candidate in found} == {
            "Calendar · Zoom", "Calendar · Webex"}

    def test_a_personal_reminder_without_people_or_a_link_is_ignored(self):
        assert calendar_candidates([event(attendee_count=0)], NOW) == []

    def test_all_day_cancelled_and_declined_events_are_ignored(self):
        assert calendar_candidates([
            event(identifier="all-day", all_day=True),
            event(identifier="cancelled", cancelled=True),
            event(identifier="declined", declined=True),
        ], NOW) == []

    def test_the_time_window_is_narrow(self):
        assert calendar_candidates([
            event(identifier="early", start=NOW + 121),
            event(identifier="old", start=NOW - 601),
            event(identifier="ended", start=NOW - 60, end=NOW),
        ], NOW) == []

    def test_recurring_occurrences_have_distinct_keys(self):
        found = calendar_candidates([
            event(start=NOW), event(start=NOW + 60),
        ], NOW)
        assert found[0].key != found[1].key


class TestAsyncRefresh:
    class Date:
        @staticmethod
        def dateWithTimeIntervalSince1970_(value):
            return value

    class Foundation:
        pass

    Foundation.NSDate = Date

    class SlowStore:
        def __init__(self, release):
            self.release = release

        def predicateForEventsWithStartDate_endDate_calendars_(self, start, end, calendars):
            return start, end

        def eventsMatchingPredicate_(self, predicate):
            self.release.wait(timeout=2)
            return []

    def test_eventkit_query_never_blocks_the_ui_poll(self):
        release = threading.Event()
        detector = MacOSCalendarDetector(clock=lambda: NOW)
        detector._enabled = True
        detector._authorized = True
        detector._foundation = self.Foundation
        detector._store = self.SlowStore(release)

        started = time.perf_counter()
        assert detector.scan() == []
        elapsed = time.perf_counter() - started

        assert elapsed < 0.1
        assert detector._refreshing
        release.set()
        deadline = time.time() + 2
        while detector._refreshing and time.time() < deadline:
            time.sleep(0.01)
        assert not detector._refreshing


class TestPermissionLifecycle:
    class Store:
        def __init__(self):
            self.requests = 0
            self.completion = None

        def init(self):
            return self

        def requestFullAccessToEventsWithCompletion_(self, completion):
            self.requests += 1
            self.completion = completion

    def frameworks(self):
        store = self.Store()

        class StoreClass:
            status = 0

            @classmethod
            def alloc(cls):
                return store

            @classmethod
            def authorizationStatusForEntityType_(cls, entity):
                return cls.status

        eventkit = SimpleNamespace(
            EKEventStore=StoreClass,
            EKEntityTypeEvent=1,
            EKAuthorizationStatusNotDetermined=0,
            EKAuthorizationStatusFullAccess=3,
            EKAuthorizationStatusAuthorized=3,
        )
        return store, StoreClass, eventkit

    def test_a_pending_permission_request_is_not_duplicated(self, monkeypatch):
        store, _, eventkit = self.frameworks()
        monkeypatch.setitem(sys.modules, "EventKit", eventkit)
        monkeypatch.setitem(sys.modules, "Foundation", SimpleNamespace())
        detector = MacOSCalendarDetector()

        assert detector.start()
        assert detector.start()
        assert store.requests == 1

        store.completion(True, None)
        assert detector.start()
        assert store.requests == 1

    def test_denied_access_is_rechecked_without_restarting_the_app(self, monkeypatch):
        store, store_class, eventkit = self.frameworks()
        monkeypatch.setitem(sys.modules, "EventKit", eventkit)
        monkeypatch.setitem(sys.modules, "Foundation", SimpleNamespace())
        detector = MacOSCalendarDetector()
        detector.start()
        store.completion(False, None)
        store_class.status = 2

        assert not detector.start()
        assert "System Settings" in detector.last_error


def test_non_macos_platforms_do_not_touch_calendar_frameworks():
    detector = create_calendar_detector("linux")
    assert isinstance(detector, NullCalendarDetector)
    assert detector.scan() == []


def test_macos_uses_eventkit_detector():
    assert isinstance(create_calendar_detector("darwin"), MacOSCalendarDetector)
