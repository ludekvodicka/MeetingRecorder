"""Content-free call-control recognition and conservative native call episodes.

These matchers are implemented candidates for desktop qualification. No application
version is approved implicitly; the monitor supplies explicit qualification/test scope.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field

from audiorecorder.meetings.lifecycle import Observation
from audiorecorder.meetings.native_profiles import MATCHER_REVISION, PROFILES

CONTROL_KINDS = frozenset({"hangup", "microphone", "camera", "join", "rejoin"})
# Exact control names, optionally followed by an accessibility keyboard shortcut.
_NAMES = {
    "hangup": {"leave", "leave meeting", "end", "end meeting", "hang up"},
    "microphone": {
        "mic",
        "microphone",
        "mute",
        "unmute",
        "mute mic",
        "unmute mic",
        "mute microphone",
        "unmute microphone",
    },
    "camera": {"camera", "start video", "stop video", "turn camera on", "turn camera off"},
    "join": {"join now", "join meeting", "join with computer audio"},
    "rejoin": {"rejoin", "join again", "rejoin meeting"},
}


def control_kind(text):
    """Return only an allowlisted meaning; never retain arbitrary accessible text."""
    if not isinstance(text, str) or len(text) > 160:
        return None
    text = re.sub(r"\s+", " ", text.strip().casefold())
    text = re.sub(r"\s*\([^)]{1,60}\)$", "", text).strip()
    for kind, names in _NAMES.items():
        if text in names:
            return kind
    return None


def qualification_key(bundle, version):
    if bundle not in PROFILES or not version or len(version) > 80:
        return ""
    rules = json.dumps({key: sorted(values) for key, values in _NAMES.items()}, sort_keys=True)
    return hashlib.sha256(f"{bundle}:{version}:{MATCHER_REVISION}:{rules}".encode()).hexdigest()


@dataclass(frozen=True)
class WindowControls:
    controls: frozenset[str] = frozenset()
    complete: bool = True
    identifier: str = ""


@dataclass(frozen=True)
class NativeAppSample:
    bundle: str
    version: str = ""
    process: str = ""
    running: bool = True
    complete: bool = False
    windows: tuple[WindowControls, ...] = ()
    reason: str = ""
    empty_windows_confirmed: bool = False
    closed_windows: frozenset[str] = frozenset()

    @property
    def state(self):
        if not self.running:
            return "not-running" if self.complete else "unknown"
        if not self.process or not self.version:
            return "unknown"
        # The three controls must occur in the same fully inspected window. A prejoin
        # window or an unrelated chat's Mute button cannot supply joined-call evidence.
        active = any(
            w.complete
            and {"hangup", "microphone", "camera"} <= w.controls
            and not w.controls & {"join", "rejoin"}
            for w in self.windows
        )
        if active:
            return "active"
        if not self.complete or any(not w.complete for w in self.windows):
            return "unknown"
        if any("rejoin" in w.controls for w in self.windows):
            return "ended"
        if any("join" in w.controls for w in self.windows):
            return "prejoin"
        return "idle"


@dataclass
class Episode:
    identifier: str
    process: str
    active: bool = True
    end_count: int = 0
    absent_since: float | None = None
    windows: set[str] = field(default_factory=set)


@dataclass
class NativeCallTracker:
    """Group a provider's call windows; ambiguity preserves the existing capture.

    This intentionally does not infer per-participant identity or equate a disappearing
    window with a completed call. Another active call in that provider keeps the group alive.
    """

    run: str
    serial: int = 0
    episodes: dict[str, Episode] = field(default_factory=dict)
    last_sample: float = -1.0
    observations: tuple[Observation, ...] = ()

    def update(
        self,
        samples,
        observed_at,
        approved=frozenset(),
        absence_approved=frozenset(),
        absence_grace=45,
    ):
        if observed_at <= self.last_sample:
            return self.observations
        if self.last_sample >= 0 and observed_at - self.last_sample > 10:
            for episode in self.episodes.values():
                episode.absent_since = None
                episode.end_count = 0
        self.last_sample = observed_at
        by_bundle = {s.bundle: s for s in samples if s.bundle in PROFILES}
        output = []
        for bundle in PROFILES:
            sample = by_bundle.get(bundle)
            prior = self.episodes.get(bundle)
            if sample is None:
                if prior is not None and prior.active:
                    prior.absent_since = None
                    output.append(
                        Observation(
                            prior.identifier, "native-call-controls", "unknown", observed_at
                        )
                    )
                continue
            if (
                prior is not None
                and sample.complete
                and (not sample.running or sample.process != prior.process)
            ):
                # A verified process exit/replacement is positive end evidence for the
                # old group, even if the replacement already has another call active.
                prior.end_count += 1
                terminal = prior.end_count >= 2
                output.append(
                    Observation(
                        prior.identifier,
                        "native-call-controls",
                        "ended" if terminal else "unknown",
                        observed_at,
                        False,
                        bundle in approved,
                        terminal,
                    )
                )
                if not terminal:
                    continue
                prior.active = False
            if sample.state == "active":
                if prior is None or not prior.active:
                    self.serial += 1
                    prior = Episode(f"{self.run}:{bundle}:{self.serial}", sample.process)
                    self.episodes[bundle] = prior
                prior.end_count = 0
                prior.absent_since = None
                prior.windows.update(
                    w.identifier
                    for w in sample.windows
                    if w.identifier
                    and w.complete
                    and {"hangup", "microphone", "camera"} <= w.controls
                    and not w.controls & {"join", "rejoin"}
                )
                output.append(
                    Observation(
                        prior.identifier,
                        "native-call-controls",
                        "active",
                        observed_at,
                        bundle in approved,
                        bundle in approved,
                        True,
                    )
                )
            elif prior is not None and prior.active:
                # Rejoin is only a candidate terminal state; require two complete samples
                # after observed active state. Idle/hidden/prejoin alone remains unknown.
                ended_windows = {
                    w.identifier
                    for w in sample.windows
                    if w.complete and "rejoin" in w.controls and w.identifier
                }
                if sample.state == "ended" and prior.windows and prior.windows <= ended_windows:
                    prior.end_count += 1
                    terminal = prior.end_count >= 2
                    prior.active = not terminal
                    output.append(
                        Observation(
                            prior.identifier,
                            "native-call-controls",
                            "ended" if terminal else "unknown",
                            observed_at,
                            False,
                            bundle in approved,
                            terminal,
                        )
                    )
                elif (
                    sample.running
                    and sample.process == prior.process
                    and bundle in absence_approved
                    and sample.complete
                    and (
                        sample.windows
                        or sample.empty_windows_confirmed
                        or prior.windows <= sample.closed_windows
                    )
                    and prior.windows
                    and not any(w.identifier in prior.windows for w in sample.windows)
                ):
                    # This path requires separate tested coverage for hidden/minimized
                    # windows. It is absence with grace, never a confirmed immediate end.
                    prior.end_count = 0
                    if prior.absent_since is None:
                        prior.absent_since = observed_at
                    terminal = observed_at - prior.absent_since >= absence_grace
                    prior.active = not terminal
                    output.append(
                        Observation(
                            prior.identifier,
                            "native-call-controls-absence" if terminal else "native-call-controls",
                            "ended" if terminal else "absent",
                            observed_at,
                            False,
                            bundle in approved,
                            True,
                        )
                    )
                elif sample.running and sample.process == prior.process:
                    prior.end_count = 0
                    prior.absent_since = None
                    output.append(
                        Observation(
                            prior.identifier, "native-call-controls", "unknown", observed_at
                        )
                    )
        self.observations = tuple(output)
        return self.observations

    def retire(self, episodes):
        for episode in self.episodes.values():
            if episode.identifier in episodes:
                episode.active = False


def parse_snapshot(value):
    """Accept a small normalized vocabulary; reject incomplete/unknown child schemas."""
    if not isinstance(value, dict) or value.get("permission") not in (
        "granted",
        "not-granted",
        "unavailable",
    ):
        raise ValueError("invalid native snapshot")
    apps = value.get("apps")
    if not isinstance(apps, list) or len(apps) > len(PROFILES):
        raise ValueError("invalid native apps")
    output = []
    seen = set()
    for app in apps:
        if not isinstance(app, dict) or app.get("bundle") not in PROFILES:
            raise ValueError("invalid native provider")
        if app["bundle"] in seen:
            raise ValueError("duplicate provider")
        seen.add(app["bundle"])
        windows = app.get("windows", [])
        if type(app.get("empty_windows_confirmed", False)) is not bool:
            raise ValueError("invalid empty-window coverage")
        closed = app.get("closed_windows", [])
        if (
            not isinstance(closed, list)
            or len(closed) > 32
            or any(not isinstance(w, str) or len(w) > 80 for w in closed)
        ):
            raise ValueError("invalid closed windows")
        if not isinstance(windows, list) or len(windows) > 32:
            raise ValueError("invalid native windows")
        for key in ("running", "complete"):
            if type(app.get(key)) is not bool:
                raise ValueError("invalid native health")
        for key in ("version", "process", "reason"):
            if not isinstance(app.get(key, ""), str) or len(app.get(key, "")) > 100:
                raise ValueError("invalid native metadata")
        parsed_windows = []
        for window in windows:
            if not isinstance(window, dict) or type(window.get("complete")) is not bool:
                raise ValueError("invalid window health")
            controls = window.get("controls")
            identifier = window.get("identifier", "")
            if (
                not isinstance(controls, list)
                or len(controls) > len(CONTROL_KINDS)
                or any(not isinstance(c, str) or c not in CONTROL_KINDS for c in controls)
                or not isinstance(identifier, str)
                or len(identifier) > 80
            ):
                raise ValueError("invalid control vocabulary")
            parsed_windows.append(
                WindowControls(frozenset(controls), window["complete"], identifier)
            )
        if app["running"] and app["complete"] and not app.get("process"):
            raise ValueError("missing process identity")
        output.append(
            NativeAppSample(
                app["bundle"],
                app.get("version", ""),
                app.get("process", ""),
                app["running"],
                app["complete"],
                tuple(parsed_windows),
                app.get("reason", ""),
                app.get("empty_windows_confirmed", False),
                frozenset(closed),
            )
        )
    return tuple(output)
