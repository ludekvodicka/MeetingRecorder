"""Recording policy with no Qt, platform APIs or wall-clock timers.

Adapters supply fresh observations and execute emitted effects. Unqualified observations
can be displayed to the user, but can never arm unattended capture commands.
"""

import math
import time
import uuid
from dataclasses import dataclass, replace

from audiorecorder.config import validated_config


@dataclass(frozen=True)
class Observation:
    episode: str
    source: str
    state: str  # active, ended, absent, unknown
    observed_at: float
    auto_start: bool = False
    auto_stop: bool = False
    observable: bool = False


@dataclass(frozen=True)
class Effect:
    kind: str
    operation: str
    recording: str = ""
    detail: str = ""


class Lifecycle:
    """One capture owner and independently identified finalization jobs."""

    def __init__(self, config, clock=time.monotonic, token=None):
        self.clock = clock
        self.run = token or uuid.uuid4().hex
        self.config = validated_config(config)
        self.state = "idle"
        self.recording = ""
        self.origin = "manual"
        self.operation = ""
        self.deadline = None
        self.pending_since = None
        self.grace_since = None
        self.episodes = set()
        self.suppressed = set()
        self.observations = {}
        self.counts = {}
        self.finalizing = set()
        self.effects = []
        self.blocked = False
        self.automatic_ready = True
        self.closing = False
        self.evidence_pending = False
        self._cancel_start = False
        self._kept = False
        self._serial = 0

    def _id(self):
        self._serial += 1
        return f"{self.run}:{self._serial}"

    def _emit(self, kind, detail=""):
        self.effects.append(Effect(kind, self.operation, self.recording, detail))

    def drain(self):
        effects, self.effects = self.effects, []
        return effects

    @property
    def remaining(self):
        return max(0, math.ceil(self.deadline - self.clock())) if self.deadline else None

    def configure(self, config):
        self.config = validated_config(config)
        if not self.config["meeting_detection"] or not self.config["automatic_recording"]:
            if self.state == "start-pending":
                self.cancel()
            elif self.state == "starting" and self.origin == "automatic":
                self._cancel_start = True
        if (not self.config["meeting_detection"] or not self.config["auto_stop_recording"]) and (
            self.state in ("end-grace", "stop-pending")
        ):
            self._withdraw()
            self.state = "recording"
            self.grace_since = None

    def _fresh(self, sample):
        return sample is not None and 0 <= self.clock() - sample.observed_at <= 10

    def _active(self):
        return {
            key
            for key, sample in self.observations.items()
            if self._fresh(sample)
            and sample.state == "active"
            and sample.auto_start
            and sample.observable
        }

    def observe(self, samples):
        # Inputs are complete snapshots. Missing evidence is unknown, never an end.
        samples = tuple(samples)
        seen = {sample.episode for sample in samples if self._fresh(sample)}
        for key, prior in tuple(self.observations.items()):
            if key not in seen:
                if prior.state == "ended" and prior.auto_stop and prior.observable:
                    # A positively terminated episode stays terminated when a new call
                    # appears; it must not become an unknown blocker for that new call.
                    self.observations[key] = replace(prior, observed_at=self.clock())
                else:
                    self.observations[key] = replace(prior, state="unknown", observable=False)
                self.counts[key] = 0
        for sample in samples:
            prior = self.observations.get(sample.episode)
            if prior and sample.observed_at <= prior.observed_at:
                continue
            if not self._fresh(sample):
                continue
            self.observations[sample.episode] = sample
            if sample.state == "active" and sample.auto_start:
                self.counts[sample.episode] = self.counts.get(sample.episode, 0) + 1
            else:
                self.counts[sample.episode] = 0
            if sample.state == "ended" and sample.auto_stop and sample.observable:
                self.suppressed.discard(sample.episode)
        active = self._active()
        if self.state in ("recording", "end-grace", "stop-pending"):
            # Any qualified concurrent call extends recording ownership.
            self.episodes.update(active)
            if active:
                if self.state != "recording":
                    self._withdraw()
                    self.state = "recording"
                self.grace_since = None
                self._kept = False
        if self.state == "idle" and not self.blocked and not self.closing:
            ready = active - self.suppressed
            ready = {key for key in ready if self.counts.get(key, 0) >= 2}
            if (
                ready
                and self.automatic_ready
                and self.config["meeting_detection"]
                and self.config["automatic_recording"]
            ):
                self.episodes = set(ready)
                self._pending("start")
        self.tick()

    def _end_kind(self):
        if not self.episodes:
            return "unknown"
        samples = [self.observations.get(key) for key in self.episodes]
        if any(not self._fresh(s) or not s.auto_stop or not s.observable for s in samples):
            return "unknown"
        if any(s.state not in ("ended", "absent") for s in samples):
            return "unknown"
        return "ended" if all(s.state == "ended" for s in samples) else "absent"

    def _pending(self, kind):
        self.state = f"{kind}-pending"
        self.operation = self._id()
        self.deadline = None
        self.pending_since = self.clock()
        self._emit(f"offer-{kind}")

    def offered(self, operation, available):
        if operation != self.operation or self.state not in ("start-pending", "stop-pending"):
            return False
        if not available:
            self.cancel()
            return False
        # A duplicate or delayed submission callback must not extend a deadline.
        if self.deadline is None:
            key = (
                "auto_start_delay_seconds"
                if self.state == "start-pending"
                else ("auto_stop_delay_seconds")
            )
            self.deadline = self.clock() + self.config[key]
        return True

    def _withdraw(self):
        if self.operation:
            self._emit("withdraw")
        self.operation = ""
        self.deadline = None
        self.pending_since = None

    def cancel(self):
        if self.state == "start-pending":
            self.suppressed.update(self.episodes)
            self._withdraw()
            self.state = "idle"
        elif self.state in ("end-grace", "stop-pending"):
            self._withdraw()
            self.state = "recording"
            self.grace_since = None
            self._kept = True
        elif self.state == "starting":
            self._cancel_start = True
            self.suppressed.update(self.episodes)

    def action(self, operation, action):
        if (
            operation != self.operation
            or not operation
            or self.closing
            or (self.deadline is not None and self.clock() >= self.deadline)
        ):
            return
        if action in ("cancel", "keep", "dismiss"):
            self.cancel()
        elif action == "start" and self.state == "start-pending":
            if self.episodes <= self._active() and not self.blocked:
                self.start(manual=True)
            else:
                self.cancel()
        elif action == "stop" and self.state == "stop-pending":
            self.stop()

    @property
    def can_start(self):
        return not self.closing and not self.blocked and self.state in ("idle", "start-pending")

    @property
    def can_stop(self):
        # Startup cancellation is handled separately by stop().
        return self.state in ("recording", "end-grace", "stop-pending")

    def start(self, manual=True):
        if not self.can_start or (not manual and not self.automatic_ready):
            return False
        self._withdraw()
        self.recording = self._id()
        self.operation = self._id()
        self.origin = "manual" if manual else "automatic"
        self.state = "starting"
        self._cancel_start = False
        self._kept = False
        self._emit("start")
        return True

    def started(self, recording, success, unresolved=False):
        if recording != self.recording or self.state != "starting":
            return
        if not success:
            self.suppressed.update(self.episodes)
            self.state = "capture-unresolved" if unresolved else "idle"
            self._emit("start-failed")
            return
        self.state = "recording"
        self._emit("started")
        if self._cancel_start or self.closing:
            self.stop()

    def stop(self, reason="manual"):
        if self.state == "starting":
            self.cancel()
            return
        if not self.can_stop:
            return
        self.suppressed.update(self.episodes)
        self._withdraw()
        self.state = "stopping"
        self.operation = self._id()
        self._emit("stop", reason)

    def stopped(self, recording, success=True, has_audio=True):
        if recording != self.recording or self.state != "stopping":
            return
        if not success:
            self.state = "capture-unresolved"
            self._emit("stop-unresolved")
            return
        if has_audio:
            self.finalizing.add(recording)
        self._emit("stopped")
        self.state = "idle"
        self.recording = ""
        self.operation = ""
        self.episodes.clear()

    def retry_stop(self):
        if self.state == "capture-unresolved":
            self.state = "stopping"
            self.operation = self._id()
            self._emit("stop")

    def finalized(self, recording):
        self.finalizing.discard(recording)

    def sleep(self):
        self.cancel()
        self.observations.clear()
        self.counts.clear()
        self.grace_since = None

    def shutdown(self):
        self.closing = True
        self.cancel()
        self.stop()

    def tick(self):
        now = self.clock()
        if (
            self.state in ("start-pending", "stop-pending")
            and self.deadline is None
            and now - self.pending_since >= 5
        ):
            self.cancel()
            return
        if self.state == "start-pending":
            if not self.automatic_ready or self.blocked or not self.episodes <= self._active():
                self.cancel()
            elif self.deadline is not None and now >= self.deadline and not self.evidence_pending:
                self.start(manual=False)
        elif self.state in ("recording", "end-grace", "stop-pending"):
            if self.origin != "automatic" or self._kept or not self.config["auto_stop_recording"]:
                return
            end = self._end_kind()
            if end == "unknown" or self._active():
                if self.state != "recording":
                    self._withdraw()
                    self.state = "recording"
                self.grace_since = None
            elif self.state == "recording":
                if end == "ended":
                    self._pending("stop")
                else:
                    self.grace_since = now
                    self.state = "end-grace"
            elif self.state == "end-grace":
                if (
                    end == "ended"
                    or now - self.grace_since >= self.config["meeting_end_grace_seconds"]
                ):
                    self._pending("stop")
            elif self.deadline is not None and now >= self.deadline and not self.evidence_pending:
                self.stop("automatic-absence" if end == "absent" else "automatic-end")
