"""Conservative native meeting-window detection with duplicate suppression."""

import logging
import re
import sys
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MeetingCandidate:
    key: str
    app_name: str
    title: str
    source: str = "native-window"


_RULES = (
    (re.compile(r"microsoft teams", re.I),
     re.compile(r"\b(meeting|call|lobby|pre[- ]?join)\b", re.I), "Microsoft Teams"),
    (re.compile(r"^(zoom|zoom\.us)$", re.I),
     re.compile(r"\b(zoom meeting|webinar|waiting for host)\b", re.I), "Zoom"),
    (re.compile(r"webex", re.I),
     re.compile(r"\b(meeting|personal room|webinar|call)\b", re.I), "Webex"),
    (re.compile(r"slack", re.I), re.compile(r"\bhuddle\b", re.I), "Slack Huddle"),
)


def candidates_from_windows(windows):
    """Classify normalized macOS windows without importing platform frameworks."""
    candidates = []
    seen = set()
    for window in windows:
        if window.get("layer", 0) != 0 or not window.get("on_screen", True):
            continue
        owner = str(window.get("owner", ""))
        title = str(window.get("title", ""))
        if not owner or not title:
            continue
        for owner_pattern, title_pattern, app_name in _RULES:
            if not owner_pattern.search(owner) or not title_pattern.search(title):
                continue
            key = f"{window.get('pid', owner)}:{window.get('window_id', title)}"
            if key not in seen:
                candidates.append(MeetingCandidate(key, app_name, title))
                seen.add(key)
            break
    return candidates


class MeetingPromptPolicy:
    """Require stable detection and prompt once until the meeting window disappears."""

    def __init__(self, observations_required=2):
        self._observations_required = observations_required
        self._counts = {}
        self._prompted = set()

    def next_prompt(self, candidates, blocked=False):
        by_key = {candidate.key: candidate for candidate in candidates}
        active = set(by_key)
        self._counts = {
            key: count for key, count in self._counts.items() if key in active
        }
        self._prompted.intersection_update(active)
        for key in active:
            self._counts[key] = self._counts.get(key, 0) + 1

        # A meeting already being recorded must not prompt the moment recording stops.
        if blocked:
            self._prompted.update(active)
            return None

        ready = [candidate for candidate in candidates
                 if self._counts[candidate.key] >= self._observations_required
                 and candidate.key not in self._prompted]
        if not ready:
            return None
        self._prompted.update(active)
        return ready[0]


class NullMeetingDetector:
    def scan(self):
        return []


class CombinedMeetingDetector:
    def __init__(self, detectors):
        self._detectors = tuple(detectors)

    def scan(self):
        combined = {}
        for detector in self._detectors:
            for candidate in detector.scan():
                combined[candidate.key] = candidate
        return list(combined.values())


class MacOSMeetingDetector:
    def __init__(self):
        self._available = True

    def scan(self):
        if not self._available:
            return []
        try:
            import Quartz

            raw = Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionOnScreenOnly
                | Quartz.kCGWindowListExcludeDesktopElements,
                Quartz.kCGNullWindowID,
            )
            windows = [
                {
                    "owner": item.get(Quartz.kCGWindowOwnerName, ""),
                    "title": item.get(Quartz.kCGWindowName, ""),
                    "pid": item.get(Quartz.kCGWindowOwnerPID, ""),
                    "window_id": item.get(Quartz.kCGWindowNumber, ""),
                    "layer": item.get(Quartz.kCGWindowLayer, 0),
                    "on_screen": item.get(Quartz.kCGWindowIsOnscreen, True),
                }
                for item in raw or []
            ]
            return candidates_from_windows(windows)
        except Exception:
            # Detection is a convenience. A framework or permission problem must never
            # interfere with manually starting a recording.
            log.exception("could not inspect native meeting windows")
            self._available = False
            return []


def create_meeting_detector(platform_name=None):
    platform_name = sys.platform if platform_name is None else platform_name
    return MacOSMeetingDetector() if platform_name == "darwin" else NullMeetingDetector()
