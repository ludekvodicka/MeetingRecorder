"""Conservative macOS meeting detection with duplicate suppression."""

import logging
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MeetingCandidate:
    key: str
    app_name: str
    title: str
    source: str = "native-window"


@dataclass(frozen=True)
class MeetingDetectionStatus:
    native_available: bool
    scanned: bool
    visible_apps: tuple[str, ...]
    matched_apps: tuple[str, ...]
    teams_media: str
    error: str | None = None


_TEAMS_OWNER = re.compile(r"microsoft teams", re.I)
_TEAMS_EXPLICIT_CALL = re.compile(r"\b(meeting|call|lobby|pre[- ]?join)\b", re.I)
_TEAMS_NON_CALL_PREFIX = re.compile(
    r"^(activity|apps|calendar|calls|chat|copilot|files|onedrive|teams)\s*\|",
    re.I,
)

_RULES = (
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
        app_name = _classify_window(owner, title)
        if app_name:
            key = f"{window.get('pid', owner)}:{window.get('window_id', title)}"
            if key not in seen:
                candidates.append(MeetingCandidate(key, app_name, title))
                seen.add(key)

    # Without Screen Recording permission macOS may redact another app's title while still
    # exposing owner, layer, and bounds. Modern Teams adds a wide, thin elevated call-control
    # surface during an active call. Use it only when no titled Teams call already matched.
    if not any(candidate.app_name == "Microsoft Teams" for candidate in candidates):
        for window in windows:
            if not _is_teams_call_control(window):
                continue
            key = f"{window.get('pid', 'teams')}:teams-call-controls"
            candidates.append(MeetingCandidate(
                key,
                "Microsoft Teams",
                "Active Microsoft Teams call",
            ))
            break
    return candidates


def _classify_window(owner, title):
    if _TEAMS_OWNER.search(owner) and _is_teams_call_title(title):
        return "Microsoft Teams"
    for owner_pattern, title_pattern, app_name in _RULES:
        if owner_pattern.search(owner) and title_pattern.search(title):
            return app_name
    return None


def _is_teams_call_title(title):
    if _TEAMS_NON_CALL_PREFIX.search(title):
        return False
    if _TEAMS_EXPLICIT_CALL.search(title):
        return True
    # Current Teams uses the meeting subject as the call-window title, followed by the
    # organization, signed-in account, and product name. Requiring that exact structure
    # avoids treating arbitrary Teams content or pop-out chats as calls.
    parts = [part.strip() for part in title.split("|")]
    return (
        len(parts) == 4
        and bool(parts[0])
        and "@" in parts[-2]
        and parts[-1].casefold() == "microsoft teams"
    )


def _is_teams_call_control(window):
    if not window.get("on_screen", True):
        return False
    if not _TEAMS_OWNER.search(str(window.get("owner", ""))):
        return False
    try:
        layer = int(window.get("layer", 0))
        bounds = window.get("bounds") or {}
        width = float(bounds.get("Width", 0))
        height = float(bounds.get("Height", 0))
    except (TypeError, ValueError):
        return False
    return layer >= 20 and width >= 600 and 20 <= height <= 100


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

    def diagnostics(self):
        return MeetingDetectionStatus(
            native_available=False,
            scanned=True,
            visible_apps=(),
            matched_apps=(),
            teams_media="not supported",
        )


class CombinedMeetingDetector:
    def __init__(self, detectors):
        self._detectors = tuple(detectors)
        self.errors = {}

    def scan(self):
        combined = {}
        self.errors = {}
        for detector in self._detectors:
            try:
                for candidate in detector.scan():
                    combined[candidate.key] = candidate
            except Exception as error:
                self.errors[type(detector).__name__] = str(error)
        return list(combined.values())


    def add_detector(self, detector):
        self._detectors += (detector,)

    def observations(self):
        observations = []
        for detector in self._detectors:
            if hasattr(detector, 'observations'):
                try:
                    observations.extend(detector.observations())
                except Exception as error:
                    self.errors[type(detector).__name__] = str(error)
        return tuple(observations)


class TeamsMediaDetector:
    """Recognize active modern Teams media without inspecting meeting content."""

    _DEFAULT_DIRECTORIES = (
        Path.home() / "Library/Containers/com.microsoft.teams2/Data/Library/"
        "Application Support/Microsoft/Teams/media-stack",
        Path.home() / "Library/Application Support/Microsoft/Teams/media-stack",
    )

    def __init__(self, directories=None, clock=time.time, changes_required=2,
                 activity_ttl=20):
        self._directories = tuple(directories or self._DEFAULT_DIRECTORIES)
        self._clock = clock
        self._changes_required = changes_required
        self._activity_ttl = activity_ttl
        self._last_signature = None
        self._consecutive_changes = 0
        self._active_until = 0.0
        self.state = "unavailable"

    def scan(self):
        now = self._clock()
        signature = self._latest_signature()
        if signature is None:
            self._last_signature = None
            self._consecutive_changes = 0
            self._active_until = 0.0
            self.state = "unavailable"
            return []

        changed = self._last_signature is not None and signature != self._last_signature
        self._last_signature = signature
        _path, _size, modified_at = signature
        recent = -5 <= now - modified_at <= self._activity_ttl
        if changed and recent:
            self._consecutive_changes += 1
            if self._consecutive_changes >= self._changes_required:
                self._active_until = now + self._activity_ttl
        else:
            self._consecutive_changes = 0

        active = self._active_until > 0 and now <= self._active_until
        self.state = "active" if active else "idle"
        if not active:
            return []
        return [MeetingCandidate(
            "teams:media-stack",
            "Microsoft Teams",
            "Active Microsoft Teams call",
            source="teams-media",
        )]

    def _latest_signature(self):
        latest = None
        for directory in self._directories:
            try:
                files = directory.glob("*.blog")
                for path in files:
                    stat = path.stat()
                    signature = (str(path), stat.st_size, stat.st_mtime)
                    if latest is None or signature[2] > latest[2]:
                        latest = signature
            except OSError:
                log.debug("could not inspect Teams media activity in %s", directory)
        return latest


class MacOSMeetingDetector:
    def __init__(self, teams_media_detector=None):
        self._teams_media_detector = teams_media_detector or TeamsMediaDetector()
        self._status = MeetingDetectionStatus(
            native_available=True,
            scanned=False,
            visible_apps=(),
            matched_apps=(),
            teams_media=self._teams_media_detector.state,
        )

    def scan(self):
        teams_candidates = self._teams_media_detector.scan()
        try:
            import Quartz

            raw = Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionOnScreenOnly
                | Quartz.kCGWindowListExcludeDesktopElements,
                Quartz.kCGNullWindowID,
            )
            if raw is None:
                raise RuntimeError("Window scan returned no result; coverage is unknown")
            windows = [
                {
                    "owner": item.get(Quartz.kCGWindowOwnerName, ""),
                    "title": item.get(Quartz.kCGWindowName, ""),
                    "pid": item.get(Quartz.kCGWindowOwnerPID, ""),
                    "window_id": item.get(Quartz.kCGWindowNumber, ""),
                    "layer": item.get(Quartz.kCGWindowLayer, 0),
                    "on_screen": item.get(Quartz.kCGWindowIsOnscreen, True),
                    "bounds": item.get(Quartz.kCGWindowBounds, {}),
                }
                for item in raw or []
            ]
            native_candidates = candidates_from_windows(windows)
            visible_apps = tuple(sorted({
                str(window["owner"])
                for window in windows
                if window["owner"] and _is_meeting_app(str(window["owner"]))
            }))
            self._status = MeetingDetectionStatus(
                native_available=True,
                scanned=True,
                visible_apps=visible_apps,
                matched_apps=tuple(sorted({
                    candidate.app_name for candidate in native_candidates
                })),
                teams_media=self._teams_media_detector.state,
            )
            return native_candidates + teams_candidates
        except Exception as err:
            # Detection is a convenience. A framework or permission problem must never
            # interfere with manually starting a recording.
            log.exception("could not inspect native meeting windows")
            self._status = MeetingDetectionStatus(
                native_available=False,
                scanned=True,
                visible_apps=(),
                matched_apps=(),
                teams_media=self._teams_media_detector.state,
                error=str(err),
            )
            return teams_candidates

    def diagnostics(self):
        return self._status


def _is_meeting_app(owner):
    return _TEAMS_OWNER.search(owner) is not None or any(
        owner_pattern.search(owner) for owner_pattern, _title, _app in _RULES
    )


def create_meeting_detector(platform_name=None):
    platform_name = sys.platform if platform_name is None else platform_name
    return MacOSMeetingDetector() if platform_name == "darwin" else NullMeetingDetector()
