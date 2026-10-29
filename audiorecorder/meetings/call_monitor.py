"""Asynchronous native-call snapshots and version-scoped, explicit test qualification."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from contextlib import suppress
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from audiorecorder.config import validated_config
from audiorecorder.meetings.detection import MeetingCandidate
from audiorecorder.meetings.lifecycle import Observation
from audiorecorder.meetings.native_calls import (
    PROFILES,
    NativeCallTracker,
    parse_snapshot,
    qualification_key,
)
from audiorecorder.meetings.window_closure import WindowClosureWatcher


class NativeCallMonitor(QObject):
    updated = pyqtSignal()
    _received = pyqtSignal(object, float, int)
    _window_closed = pyqtSignal(str, str, str, int)

    def __init__(self, parent=None, runner=None, clock=time.monotonic, process_alive=None):
        super().__init__(parent)
        self.clock = clock
        self.runner = runner or self._probe
        self.process_alive = process_alive or self._process_alive
        self.tracker = NativeCallTracker(uuid.uuid4().hex)
        self.samples = ()
        self.permission = "unknown"
        self.observed_at = -1.0
        self.error = ""
        self.config = {}
        self.enabled = False
        self.busy = False
        self.closed = False
        self.generation = 0
        self.test_bundle = ""
        self.test_until = 0.0
        self.test_absence = False
        self.history = {bundle: set() for bundle in PROFILES}
        self.active_counts = {bundle: 0 for bundle in PROFILES}
        self.versions = {}
        self.transitions = {bundle: set() for bundle in PROFILES}
        self.trace = deque(maxlen=256)
        self._window_aliases = {}
        self._process_aliases = {}
        self._observations = ()
        self.closed_windows = {}
        self.closure_watcher = self._new_watcher()
        self._window_closed.connect(self._closed)
        self._received.connect(self._accept)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.request)

    @property
    def testing(self):
        return bool(self.test_bundle and self.clock() < self.test_until)

    def configure(self, config):
        previous = self.enabled
        self.config = validated_config(config)
        self.enabled = sys.platform == "darwin" and (
            config.get("native_call_detection", False) or self.testing
        )
        if self.enabled:
            self.timer.start()
            if not previous:
                self.request()
        else:
            self.timer.stop()
            if previous:
                self.reset()

    def reset(self):
        self.generation += 1
        self.samples = ()
        self.observed_at = -1.0
        self.closed_windows.clear()
        self.closure_watcher.close()
        self.closure_watcher = self._new_watcher()
        # Preserve episode suppression across transient errors/permission gaps.
        self._observations = self.tracker.update((), self.clock())

    def _new_watcher(self):
        generation = self.generation
        return WindowClosureWatcher(
            lambda bundle, process, identifier: self._window_closed.emit(
                bundle, process, identifier, generation
            )
        )

    def _closed(self, bundle, process, identifier, generation):
        if self.closed or not self.enabled or generation != self.generation:
            return
        prior = self.tracker.episodes.get(bundle)
        if prior and prior.active and prior.process == process and identifier in prior.windows:
            self.closed_windows.setdefault((bundle, process), set()).add(identifier)
            self.request()

    def arm_test(self, bundle, include_absence=False):
        if bundle not in PROFILES:
            raise ValueError("unsupported native app")
        self.test_bundle = bundle
        self.test_absence = include_absence
        self.test_until = self.clock() + 1800
        self.configure(self.config)
        self.updated.emit()

    def end_test(self):
        self.test_bundle = ""
        self.test_until = 0
        self.test_absence = False
        self.configure(self.config)
        self.updated.emit()

    def approved(self):
        stored = self.config.get("native_call_qualifications", {})
        approved = {
            bundle
            for bundle, version in self.versions.items()
            if version and stored.get(bundle) == qualification_key(bundle, version)
        }
        if self.testing:
            approved.add(self.test_bundle)
        return frozenset(approved)

    def absence_approved(self):
        stored = self.config.get("native_call_absence_qualifications", {})
        approved = {
            bundle
            for bundle, version in self.versions.items()
            if version and stored.get(bundle) == qualification_key(bundle, version)
        }
        if self.testing and self.test_absence:
            approved.add(self.test_bundle)
        return frozenset(approved) & self.approved()

    def retire(self, episodes, absence=False):
        for bundle, episode in self.tracker.episodes.items():
            if absence and episode.identifier in episodes:
                self.transitions[bundle].add("absence-stop")
        self.tracker.retire(episodes)

    def request(self):
        if self.closed:
            return
        if self.test_bundle and not self.testing:
            self.end_test()
        if not self.enabled or self.busy:
            return
        self.busy = True
        started, generation = self.clock(), self.generation

        def run():
            try:
                value = self.runner()
            except Exception:
                value = {"permission": "unavailable", "apps": [], "error": "probe-failed"}
            with suppress(RuntimeError):
                self._received.emit(value, started, generation)

        threading.Thread(target=run, daemon=True, name="native-call-probe").start()

    @staticmethod
    def _process_alive(pid):
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except (PermissionError, OSError):
            return True  # An unreadable process cannot be declared terminated.

    @staticmethod
    def _probe():
        command = [sys.executable]
        if not getattr(sys, "frozen", False):
            command += ["-m", "audiorecorder"]
        with tempfile.TemporaryDirectory(prefix="recorder-call-probe-") as directory:
            destination = Path(directory) / "snapshot.json"
            command += ["--native-call-snapshot", str(destination)]
            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=6,
                check=False,
            )
            if result.returncode or not destination.is_file() or destination.stat().st_size > 32768:
                raise ValueError("native probe failed")
            return json.loads(destination.read_text(encoding="utf-8"))

    def _accept(self, value, observed_at, generation):
        self.busy = False
        if self.closed or generation != self.generation or not self.enabled:
            return
        try:
            samples = parse_snapshot(value)
            if not 0 <= self.clock() - observed_at <= 8:
                raise ValueError("stale probe")
            checked = []
            for sample in samples:
                prior = self.tracker.episodes.get(sample.bundle)
                if (
                    not sample.running
                    and prior
                    and self.process_alive(int(prior.process.split(":", 1)[0]))
                ):
                    sample = replace(
                        sample,
                        running=True,
                        complete=False,
                        process=prior.process,
                        reason="live-process-not-in-registry",
                    )
                if sample.state == "active":
                    identifiers = {
                        w.identifier
                        for w in sample.windows
                        if w.complete and {"hangup", "microphone", "camera"} <= w.controls
                    }
                    self.closed_windows.setdefault(
                        (sample.bundle, sample.process), set()
                    ).difference_update(identifiers)
                    watched = identifiers | (
                        prior.windows
                        if prior and prior.active and prior.process == sample.process
                        else set()
                    )
                    self.closure_watcher.watch(sample.bundle, sample.process, watched)
                sample = replace(
                    sample,
                    closed_windows=frozenset(
                        self.closed_windows.get((sample.bundle, sample.process), ())
                    ),
                )
                checked.append(sample)
            self.samples = tuple(checked)
            self.permission = value["permission"]
            self.error = "probe-failed" if value.get("error") else ""
        except (ValueError, TypeError):
            self.samples = ()
            self.permission = "unknown"
            self.error = "invalid-or-stale-probe"
        self.observed_at = observed_at
        for sample in self.samples:
            if sample.version:
                if self.versions.get(sample.bundle, sample.version) != sample.version:
                    self.history[sample.bundle].clear()
                    self.transitions[sample.bundle].clear()
                    self.active_counts[sample.bundle] = 0
                self.versions[sample.bundle] = sample.version
            self.history[sample.bundle].add(sample.state)
            self.active_counts[sample.bundle] = (
                self.active_counts[sample.bundle] + 1 if sample.state == "active" else 0
            )
        self._observations = self.tracker.update(
            self.samples,
            observed_at,
            self.approved(),
            self.absence_approved(),
            self.config.get("meeting_end_grace_seconds", 45),
        )
        for bundle, episode in self.tracker.episodes.items():
            if not episode.active:
                self.closure_watcher.unwatch(bundle)
        for observation in self._observations:
            for bundle in PROFILES:
                if f":{bundle}:" in observation.episode:
                    if observation.state == "active" and self.active_counts[bundle] >= 2:
                        self.transitions[bundle].add("joined")
                    elif (
                        observation.state == "ended"
                        and observation.observable
                        and observation.source != "native-call-controls-absence"
                    ):
                        self.transitions[bundle].add("ended")
        self._record_trace()
        self.updated.emit()

    def _record_trace(self):
        apps = []
        for sample in self.samples:
            process = (
                self._process_aliases.setdefault(
                    sample.process, f"{len(self._process_aliases) + 1}:1"
                )
                if sample.process
                else ""
            )
            windows = []
            for window in sample.windows:
                key = (sample.bundle, sample.process, window.identifier)
                alias = self._window_aliases.setdefault(
                    key, f"window-{len(self._window_aliases) + 1}"
                )
                windows.append(
                    {
                        "identifier": alias,
                        "controls": sorted(window.controls),
                        "complete": window.complete,
                    }
                )
            apps.append(
                {
                    "bundle": sample.bundle,
                    "version": sample.version,
                    "process": process,
                    "running": sample.running,
                    "complete": sample.complete,
                    "reason": sample.reason,
                    "empty_windows_confirmed": sample.empty_windows_confirmed,
                    "closed_windows": [
                        self._window_aliases.setdefault(
                            (sample.bundle, sample.process, identifier),
                            f"window-{len(self._window_aliases) + 1}",
                        )
                        for identifier in sorted(sample.closed_windows)
                    ],
                    "windows": windows,
                }
            )
        self.trace.append({"at": self.observed_at, "permission": self.permission, "apps": apps})

    def can_approve(self, bundle):
        return bool(
            self.versions.get(bundle)
            and "joined" in self.transitions[bundle]
            and self.transitions[bundle] & {"ended", "absence-stop"}
        )

    def scan(self):
        if not self.enabled or self.clock() - self.observed_at > 10:
            return []
        return [
            MeetingCandidate(
                "native-controls:" + sample.bundle,
                PROFILES[sample.bundle],
                "Native call controls detected",
                "native-call-controls",
            )
            for sample in self.samples
            if sample.state == "active"
        ]

    def observations(self):
        if not self.enabled or self.clock() - self.observed_at > 10:
            return tuple(
                Observation(o.episode, o.source, "unknown", self.clock())
                for o in self._observations
            )
        approved = self.approved()
        # Qualification/test expiry is checked on every UI tick as well as probe completion.
        result = []
        for observation in self._observations:
            bundle = next((b for b in PROFILES if f":{b}:" in observation.episode), "")
            if observation.state == "absent" and bundle not in self.absence_approved():
                observation = replace(observation, state="unknown", observable=False)
            result.append(
                replace(
                    observation,
                    auto_start=observation.auto_start and bundle in approved,
                    auto_stop=observation.auto_stop and bundle in approved,
                )
            )
        return tuple(result)

    def diagnostics(self):
        return {
            "permission": self.permission,
            "enabled": self.enabled,
            "busy": self.busy,
            "age_seconds": max(0, self.clock() - self.observed_at)
            if self.observed_at >= 0
            else None,
            "testing": self.test_bundle if self.testing else "",
            "testing_absence": bool(self.testing and self.test_absence),
            "error": self.error,
            "apps": [
                {
                    "bundle": s.bundle,
                    "version": s.version,
                    "state": s.state,
                    "complete": s.complete,
                    "empty_windows_confirmed": s.empty_windows_confirmed,
                    "closed_window_count": len(s.closed_windows),
                    "closure_watch": self.closure_watcher.status.get(s.bundle, "not registered"),
                    "reason": s.reason,
                    "controls": sorted({c for w in s.windows for c in w.controls}),
                    "tracked_states": sorted(
                        {o.state for o in self._observations if f":{s.bundle}:" in o.episode}
                    ),
                    "tracked_window_count": len(self.tracker.episodes[s.bundle].windows)
                    if s.bundle in self.tracker.episodes
                    else 0,
                    "windows": [
                        {
                            "controls": sorted(w.controls),
                            "complete": w.complete,
                            "belongs_to_episode": s.bundle in self.tracker.episodes
                            and w.identifier in self.tracker.episodes[s.bundle].windows,
                        }
                        for w in s.windows
                    ],
                    "approved": s.bundle in self.approved(),
                    "observed_states": sorted(self.history[s.bundle]),
                    "transitions": sorted(self.transitions[s.bundle]),
                }
                for s in self.samples
            ],
        }

    def export_diagnostics(self, path, lifecycle=None):
        report = {"schema": 1, "status": self.diagnostics(), "trace": list(self.trace)}
        if lifecycle is not None:
            report["lifecycle"] = lifecycle
        Path(path).write_text(json.dumps(report, indent=2), encoding="utf-8")

    def close(self):
        self.closed = True
        self.timer.stop()
        self.closure_watcher.close()
        self.generation += 1
