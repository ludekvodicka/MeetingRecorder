"""Opt-in macOS Calendar signals, fetched asynchronously through EventKit."""

import logging
import re
import sys
import threading
import time
from dataclasses import dataclass

from audiorecorder.meetings.detection import MeetingCandidate

log = logging.getLogger(__name__)

PROMPT_BEFORE_SECONDS = 120
PROMPT_AFTER_START_SECONDS = 600
REFRESH_SECONDS = 30
_CONFERENCE_LINK = re.compile(
    r"https?://(?:meet\.google\.com|(?:[^/\s]+\.)?zoom\.us|teams\.microsoft\.com|"
    r"teams\.cloud\.microsoft|(?:[^/\s]+\.)?webex\.com)/\S+",
    re.I,
)


@dataclass(frozen=True)
class CalendarEvent:
    identifier: str
    title: str
    start: float
    end: float
    all_day: bool = False
    cancelled: bool = False
    declined: bool = False
    attendee_count: int = 0
    url: str = ""
    location: str = ""
    notes: str = ""


def calendar_candidates(events, now):
    candidates = []
    for event in events:
        if event.all_day or event.cancelled or event.declined:
            continue
        if event.start < now - PROMPT_AFTER_START_SECONDS:
            continue
        if event.start > now + PROMPT_BEFORE_SECONDS or event.end <= now:
            continue
        context = " ".join((event.url, event.location, event.notes))
        link = _CONFERENCE_LINK.search(context)
        if event.attendee_count <= 0 and link is None:
            continue
        service = _service_name(link.group(0) if link else "")
        key = f"calendar:{event.identifier}:{int(event.start)}"
        candidates.append(MeetingCandidate(
            key=key,
            app_name=f"Calendar · {service}" if service else "Calendar",
            title=event.title.strip() or "Scheduled meeting",
            source="calendar",
        ))
    return candidates


def _service_name(link):
    lowered = link.lower()
    if "meet.google.com" in lowered:
        return "Google Meet"
    if "zoom.us" in lowered:
        return "Zoom"
    if "teams.microsoft" in lowered or "teams.cloud.microsoft" in lowered:
        return "Microsoft Teams"
    if "webex.com" in lowered:
        return "Webex"
    return ""


class NullCalendarDetector:
    last_error = None

    def start(self):
        return False

    def stop(self):
        pass

    def scan(self):
        return []

    def pop_notice(self):
        return None


class MacOSCalendarDetector:
    """Keep synchronous EventKit queries away from Qt's UI thread."""

    def __init__(self, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._store = None
        self._eventkit = None
        self._foundation = None
        self._enabled = False
        self._authorized = False
        self._permission_pending = False
        self._refreshing = False
        self._next_refresh = 0.0
        self._candidates = []
        self._notice = None
        self.last_error = None

    def start(self):
        with self._lock:
            if self._enabled and (self._authorized or self._permission_pending):
                return True
            self._enabled = True
        try:
            import EventKit
            import Foundation

            store = EventKit.EKEventStore.alloc().init()
            status = EventKit.EKEventStore.authorizationStatusForEntityType_(
                EventKit.EKEntityTypeEvent)
        except Exception as err:
            self._fail(f"Calendar integration is unavailable: {err}")
            return False

        with self._lock:
            self._eventkit = EventKit
            self._foundation = Foundation
            self._store = store
        authorized = {
            getattr(EventKit, "EKAuthorizationStatusFullAccess", object()),
            getattr(EventKit, "EKAuthorizationStatusAuthorized", object()),
        }
        if status in authorized:
            with self._lock:
                self._authorized = True
                self.last_error = None
            return True
        if status == EventKit.EKAuthorizationStatusNotDetermined:
            try:
                with self._lock:
                    self._permission_pending = True
                if hasattr(store, "requestFullAccessToEventsWithCompletion_"):
                    store.requestFullAccessToEventsWithCompletion_(self._permission_finished)
                else:
                    store.requestAccessToEntityType_completion_(
                        EventKit.EKEntityTypeEvent, self._permission_finished)
                return True
            except Exception as err:
                self._fail(f"Could not request Calendar access: {err}")
                return False
        self._fail(
            "Calendar access is off. Enable it in System Settings > Privacy & Security > "
            "Calendars.")
        return False

    def stop(self):
        with self._lock:
            self._enabled = False
            self._candidates = []

    def pop_notice(self):
        with self._lock:
            notice = self._notice
            self._notice = None
            return notice

    def scan(self):
        with self._lock:
            candidates = list(self._candidates)
            due = (self._enabled and self._authorized and not self._refreshing
                   and self._clock() >= self._next_refresh)
            if due:
                self._refreshing = True
        if due:
            threading.Thread(
                target=self._refresh,
                name="calendar-meeting-refresh",
                daemon=True,
            ).start()
        return candidates

    def _permission_finished(self, granted, error):
        if error is not None:
            description = getattr(error, "localizedDescription", None)
            message = description() if description else str(error)
            self._fail(f"Calendar access failed: {message}")
            return
        with self._lock:
            self._permission_pending = False
            self._authorized = bool(granted)
            self.last_error = None if granted else "Calendar access was not granted."
            self._notice = (
                "Calendar meeting detection enabled" if granted
                else "Calendar access was not granted; other meeting detection still works"
            )

    def _refresh(self):
        try:
            now = self._clock()
            start = self._foundation.NSDate.dateWithTimeIntervalSince1970_(
                now - PROMPT_AFTER_START_SECONDS)
            end = self._foundation.NSDate.dateWithTimeIntervalSince1970_(
                now + PROMPT_BEFORE_SECONDS)
            predicate = self._store.predicateForEventsWithStartDate_endDate_calendars_(
                start, end, None)
            events = self._store.eventsMatchingPredicate_(predicate)
            snapshots = [self._snapshot(event) for event in events]
            candidates = calendar_candidates(snapshots, now)
        except Exception as err:
            log.exception("could not refresh Calendar meetings")
            with self._lock:
                self.last_error = str(err)
                self._notice = "Calendar meeting refresh failed; other detection still works"
                self._candidates = []
        else:
            with self._lock:
                if self._enabled:
                    self._candidates = candidates
                    self.last_error = None
        finally:
            with self._lock:
                self._refreshing = False
                self._next_refresh = self._clock() + REFRESH_SECONDS

    def _snapshot(self, event):
        attendees = list(event.attendees() or [])
        declined_status = self._eventkit.EKParticipantStatusDeclined
        declined = any(
            getattr(attendee, "isCurrentUser", lambda: False)()
            and attendee.participantStatus() == declined_status
            for attendee in attendees
        )
        return CalendarEvent(
            identifier=str(event.eventIdentifier() or "event"),
            title=str(event.title() or ""),
            start=float(event.startDate().timeIntervalSince1970()),
            end=float(event.endDate().timeIntervalSince1970()),
            all_day=bool(event.isAllDay()),
            cancelled=event.status() == self._eventkit.EKEventStatusCanceled,
            declined=declined,
            attendee_count=len(attendees),
            url=_optional_text(event.URL()),
            location=str(event.location() or ""),
            notes=str(event.notes() or ""),
        )

    def _fail(self, message):
        with self._lock:
            self.last_error = message
            self._notice = message
            self._authorized = False
            self._permission_pending = False


def _optional_text(value):
    if value is None:
        return ""
    absolute = getattr(value, "absoluteString", None)
    return str(absolute() if absolute else value)


def create_calendar_detector(platform_name=None):
    platform_name = sys.platform if platform_name is None else platform_name
    return MacOSCalendarDetector() if platform_name == "darwin" else NullCalendarDetector()
