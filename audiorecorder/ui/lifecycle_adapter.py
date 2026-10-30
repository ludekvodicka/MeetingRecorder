"""Qt adapter joining local detector, notification and recording policy events."""

import sys
import time
import uuid
from collections import deque

from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from audiorecorder.macos_notifications import Notifications
from audiorecorder.meetings.call_monitor import NativeCallMonitor
from audiorecorder.meetings.lifecycle import Lifecycle
from audiorecorder.meetings.native_calls import PROFILES
from audiorecorder.ui.macos_spaces import show_on_all_spaces
from audiorecorder.ui.meeting_prompt import MeetingPrompt, record_button
from audiorecorder.ui.widgets import CountdownRing


class LifecycleAdapter(QObject):
    def __init__(self, window, notifications=None):
        super().__init__(window)
        self.window = window
        self.controller = Lifecycle(window.config)
        self.notifications = notifications or Notifications()
        self.notifications.response.connect(self._response, Qt.ConnectionType.QueuedConnection)
        self.notifications.submitted.connect(self._submitted, Qt.ConnectionType.QueuedConnection)
        self.notifications.status_changed.connect(self._status, Qt.ConnectionType.QueuedConnection)
        self.native = NativeCallMonitor(self)
        self.native.updated.connect(self._native_updated)
        self.window._meeting_detector.add_detector(self.native)
        self._ending_episodes = {}
        self.actions = {}
        self.events = deque(maxlen=256)
        self.fallback = None
        self.candidates = {}
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self._last_tick = time.monotonic()
        self._last_wall = time.time()

    def _status(self, status):
        self.notifications.status = status
        if (
            not status.actionable
            and self.fallback is None
            and self.controller.state in ("start-pending", "stop-pending")
        ):
            self.controller.cancel()
            self.process()

    def effective_config(self):
        config = dict(self.window.config)
        if self.native.testing:
            config.update(
                meeting_detection=True, automatic_recording=True, auto_stop_recording=True
            )
        return config

    def configure(self):
        self.native.configure(self.window.config)
        self.controller.configure(self.effective_config())
        self.controller.observe(self.window._meeting_detector.observations())
        self.process()

    def _native_updated(self):
        if self.controller.closing:
            return
        self.controller.configure(self.effective_config())
        self.poll()

    def arm_native_test(self, bundle, include_absence=False):
        if self.controller.state != "idle" or self.window._dictation_active:
            return False
        self.native.arm_test(bundle, include_absence)
        self.window._sync_meeting_detection()
        return True

    def end_native_test(self):
        self.native.end_test()
        self.window._sync_meeting_detection()

    def poll(self):
        window = self.window
        self.notifications.refresh()
        window._update_browser_status()
        notice = window._calendar_meeting_detector.pop_notice()
        if notice:
            window._status_bar.showMessage(notice, 8000)
        candidates = window._meeting_detector.scan()
        automatic_apps = {PROFILES[bundle] for bundle in self.native.approved()}
        candidates = [c for c in candidates if c.app_name not in automatic_apps]
        self.candidates = {candidate.key: candidate for candidate in candidates}
        if window._meeting_prompt is not None and (
            window._meeting_prompt.candidate.key not in self.candidates
        ):
            window._meeting_prompt.reject()
        for identifier, (kind, value) in tuple(self.actions.items()):
            if kind == "advisory" and value not in self.candidates:
                self.notifications.withdraw(identifier)
                self.actions.pop(identifier, None)
        self.controller.blocked = window._recording_start_blocked()
        self.controller.evidence_pending = bool(
            self.controller.state in ("start-pending", "stop-pending")
            and self.controller.deadline is not None
            and self.controller.clock() >= self.controller.deadline
            and self.native.enabled
            and self.native.observed_at < self.controller.deadline
        )
        if self.controller.evidence_pending:
            self.native.request()
        self.controller.automatic_ready = window._automatic_capture_ready()[0]
        self.controller.observe(window._meeting_detector.observations())
        self.process()
        # Unqualified signals still offer manual recording without claiming joined state.
        candidate = window._meeting_prompt_policy.next_prompt(
            candidates,
            blocked=window._is_recording
            or self.controller.state != "idle"
            or window._meeting_prompt is not None,
        )
        if candidate is None:
            return
        identifier = f"{self.controller.run}:advisory:{uuid.uuid4().hex}"
        if (
            self.notifications.status.actionable
            and window.config.get("notification_style") == "native"
        ):
            self.actions[identifier] = ("advisory", candidate.key)
            self.notifications.send(
                identifier,
                "advisory",
                "Possible meeting detected",
                f"{candidate.app_name} — choose Start Recording to record.",
            )
        else:
            self._advisory_dialog(candidate)

    def _advisory_dialog(self, candidate):
        window = self.window
        if window._meeting_prompt is not None:
            return
        prompt = MeetingPrompt(
            candidate, None if sys.platform == "darwin" else window,
            cost_note=window.armed_cost_note(),
        )
        window._meeting_prompt = prompt
        prompt.accepted.connect(lambda: self._manual_candidate(candidate.key))
        prompt.finished.connect(lambda result: window._clear_meeting_prompt(prompt))
        prompt.show()

    def _manual_candidate(self, key):
        # Explicit suggestion actions still require fresh evidence.
        active = {c.key for c in self.window._meeting_detector.scan()}
        if key in active:
            self.window._start_recording()

    def _response(self, identifier, category, action):
        owned = self.actions.get(identifier)
        if not owned or category != f"mr.{owned[0]}":
            return
        kind, value = owned
        if action == "open":
            self.window._show_from_tray()
            return
        if kind == "advisory":
            self.actions.pop(identifier, None)
            self.notifications.withdraw(identifier)
            if action == "start":
                self._manual_candidate(value)
        elif kind == "recording":
            if action == "stop" and value == self.controller.recording:
                self.window._stop_recording()
        elif kind == "saved" and action in ("open-recording", "reveal"):
            self.window._open_saved(value, reveal=action == "reveal")
        else:
            self.controller.action(identifier, action)
            self.process()

    def _submitted(self, identifier, success):
        owned = self.actions.get(identifier)
        if owned is None:
            self.notifications.withdraw(identifier)
            return
        if owned[0] in ("start", "stop"):
            if identifier != self.controller.operation:
                self.notifications.withdraw(identifier)
            elif success:
                self.controller.offered(identifier, True)
            else:
                self._countdown_dialog(owned[0], identifier)
        elif not success and owned[0] == "advisory":
            candidate = self.candidates.get(owned[1])
            self.actions.pop(identifier, None)
            if candidate:
                self._advisory_dialog(candidate)

    def _countdown_dialog(self, kind, operation):
        if operation != self.controller.operation:
            return
        if self.fallback is not None:
            self.fallback.close()
        dialog = QDialog(None)
        dialog.setWindowTitle("Automation")
        dialog.setWindowFlag(Qt.WindowType.Tool, True)
        dialog.setFixedWidth(300)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)
        top = QHBoxLayout()
        top.setSpacing(12)
        self._countdown_ring = CountdownRing(44, "rec" if kind == "start" else "text.primary")
        self._countdown_ring.set_remaining(self.controller.remaining or 0)
        top.addWidget(self._countdown_ring)
        text = QVBoxLayout()
        text.setSpacing(2)
        title = QLabel("Start pending" if kind == "start" else "Stop pending")
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        label = QLabel(
            "A qualified call started" if kind == "start" else "The call has ended")
        label.setObjectName("hint")
        text.addWidget(title)
        text.addWidget(label)
        top.addLayout(text, 1)
        layout.addLayout(top)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch()
        if kind == "start":
            buttons = ((QPushButton("Cancel"), "cancel"), (record_button("Start now"), "start"))
        else:
            buttons = ((QPushButton("Keep recording"), "keep"),
                       (record_button("Stop now", "square"), "stop"))
        for button, action in buttons:
            button.clicked.connect(
                lambda checked=False, value=action: self._countdown_action(operation, value)
            )
            row.addWidget(button)
        layout.addLayout(row)
        dialog.rejected.connect(lambda: self._countdown_action(operation, "cancel"))
        self.fallback = dialog
        self._countdown_label = label
        self._countdown_detail = label.text()
        dialog.show()
        show_on_all_spaces(dialog)
        self.controller.offered(operation, dialog.isVisible())

    def _countdown_action(self, operation, action):
        self.controller.action(operation, action)
        self.process()

    def process(self):
        window = self.window
        for effect in self.controller.drain():
            self.events.append(
                {
                    "at": self.controller.clock(),
                    "kind": effect.kind,
                    "detail": effect.detail,
                    "recording": effect.recording,
                    "origin": self.controller.origin,
                }
            )
            if effect.kind.startswith("offer-"):
                kind = effect.kind[6:]
                self.actions[effect.operation] = (kind, effect.recording)
                absence = any(
                    o.source == "native-call-controls-absence" or o.state == "absent"
                    for key, o in self.controller.observations.items()
                    if key in self.controller.episodes
                )
                title = (
                    "Meeting detected"
                    if kind == "start"
                    else "Meeting no longer detected"
                    if absence
                    else "Meeting ended"
                )
                delay = window.config.get(f"auto_{kind}_delay_seconds", 5)
                if window.config.get("notification_style") == "in-app" or (
                    not self.notifications.status.actionable
                ):
                    self._countdown_dialog(kind, effect.operation)
                else:
                    self.notifications.send(
                        effect.operation,
                        kind,
                        title,
                        f"Recording {'starts' if kind == 'start' else 'stops'} in {delay} seconds.",
                    )
            elif effect.kind == "withdraw":
                self.actions.pop(effect.operation, None)
                self.notifications.withdraw(effect.operation)
                if self.fallback:
                    dialog, self.fallback = self.fallback, None
                    dialog.blockSignals(True)
                    dialog.close()
                    dialog.deleteLater()
            elif effect.kind == "start":
                window._begin_capture(effect.recording)
            elif effect.kind == "stop":
                if effect.detail in ("automatic-end", "automatic-absence"):
                    self._ending_episodes[effect.recording] = (
                        set(self.controller.episodes),
                        effect.detail == "automatic-absence"
                        or any(
                            o.source == "native-call-controls-absence"
                            for key, o in self.controller.observations.items()
                            if key in self.controller.episodes
                        ),
                    )
                window._begin_stop(effect.recording)
            elif effect.kind == "started":
                self.notify("recording", "Recording started", effect.recording)
            elif effect.kind == "stopped":
                episodes, absence = self._ending_episodes.pop(effect.recording, (set(), False))
                self.native.retire(episodes, absence)
                for identifier, value in tuple(self.actions.items()):
                    if value == ("recording", effect.recording):
                        self.notifications.withdraw(identifier)
                        self.actions.pop(identifier, None)
        self.update_status()

    def notify(self, kind, title, value="", body=""):
        identifier = f"{self.controller.run}:{kind}:{uuid.uuid4().hex}"
        self.actions[identifier] = (kind, value)
        if kind == "error":
            body = "Open Recorder for details and recovery controls."
        while len(self.actions) > 128:
            old = next(iter(self.actions))
            if old == self.controller.operation:
                break
            self.actions.pop(old, None)
            self.notifications.withdraw(old)
        self.notifications.send(identifier, kind, title, body)

    def tick(self):
        now, wall = time.monotonic(), time.time()
        # Wall/monotonic divergence also catches platforms whose monotonic clock pauses.
        if (
            now - self._last_tick > 10
            or abs((wall - self._last_wall) - (now - self._last_tick)) > 10
        ):
            self.controller.sleep()
            self.native.reset()
            self.native.request()
        self._last_tick, self._last_wall = now, wall
        self.controller.configure(self.effective_config())
        self.controller.automatic_ready = self.window._automatic_capture_ready()[0]
        self.controller.blocked = self.window._recording_start_blocked()
        if (
            self.controller.state in ("start-pending", "stop-pending")
            and self.controller.deadline is not None
            and now >= self.controller.deadline
        ):
            self.poll()  # Final scan before a deadline can commit a capture command.
        else:
            self.controller.tick()
            self.process()

    def update_status(self):
        state = self.controller.state
        remaining = self.controller.remaining
        text = state.replace("-", " ").title()
        if remaining is not None:
            text += f" — {remaining}s"
        if self.controller.evidence_pending:
            text += " — verifying call state"
        if self.fallback:
            self._countdown_ring.set_remaining(remaining or 0)
            self._countdown_label.setText(
                "Verifying the call state" if self.controller.evidence_pending
                else self._countdown_detail)
        self.window._update_lifecycle_tray(text)

    def close(self):
        self.timer.stop()
        self.controller.shutdown()
        self.process()
        self.native.close()
        self.notifications.close()
