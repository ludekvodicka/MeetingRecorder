import pytest

from audiorecorder.meetings.lifecycle import Lifecycle, Observation


class Clock:
    value = 100.0

    def __call__(self):
        return self.value


@pytest.fixture
def run():
    clock = Clock()
    policy = Lifecycle(
        {"meeting_detection": True, "automatic_recording": True, "auto_stop_recording": True},
        clock,
    )
    return policy, clock


def sample(clock, state="active", episode="call", **changes):
    return Observation(
        episode,
        "qualified-test-source",
        state,
        clock(),
        **{"auto_start": True, "auto_stop": True, "observable": True, **changes},
    )


def offer(run):
    policy, clock = run
    policy.observe([sample(clock)])
    clock.value += 1
    policy.observe([sample(clock)])
    assert policy.state == "start-pending"
    operation = policy.operation
    policy.offered(operation, True)
    return operation


def recording(run):
    policy, clock = run
    offer(run)
    clock.value += 5
    policy.observe([sample(clock)])
    assert policy.state == "starting"
    policy.started(policy.recording, True)
    return policy.recording


def test_no_early_commit_and_independent_delays(run):
    policy, clock = run
    policy.configure(
        {**policy.config, "auto_start_delay_seconds": 7, "auto_stop_delay_seconds": 19}
    )
    operation = offer(run)
    clock.value += 6.99
    policy.tick()
    assert policy.state == "start-pending"
    policy.configure({**policy.config, "auto_start_delay_seconds": 5})
    assert policy.remaining == 1
    clock.value += 0.01
    policy.tick()
    policy.started(policy.recording, True)
    clock.value += 1
    policy.observe([sample(clock, "ended")])
    assert policy.state == "stop-pending"
    policy.action(operation, "stop")  # obsolete start operation
    assert policy.state == "stop-pending"
    policy.offered(policy.operation, True)
    clock.value += 18
    policy.observe([sample(clock, "ended")])
    assert policy.state == "stop-pending"
    clock.value += 1
    policy.observe([sample(clock, "ended")])
    assert policy.state == "stopping"


@pytest.mark.parametrize("change", [{"auto_stop": False}, {"observable": False}])
def test_unqualified_source_cannot_stop(run, change):
    policy, clock = run
    recording(run)
    for _ in range(20):
        clock.value += 5
        policy.observe([sample(clock, "absent", **change)])
    assert policy.state == "recording"


def test_unqualified_source_never_starts(run):
    policy, clock = run
    for _ in range(10):
        clock.value += 5
        policy.observe([sample(clock, auto_start=False)])
    assert policy.state == "idle"


def test_unknown_cancels_stop_and_preserves_capture(run):
    policy, clock = run
    recording(run)
    clock.value += 1
    policy.observe([sample(clock, "ended")])
    policy.offered(policy.operation, True)
    clock.value += 1
    policy.observe([sample(clock, "unknown")])
    assert policy.state == "recording"
    assert policy.deadline is None


def test_absence_grace_requires_continuous_fresh_covered_evidence(run):
    policy, clock = run
    recording(run)
    clock.value += 1
    policy.observe([sample(clock, "absent")])
    assert policy.state == "end-grace"
    for _ in range(8):
        clock.value += 5
        policy.observe([sample(clock, "absent")])
        assert policy.state == "end-grace"
    clock.value += 5
    policy.observe([sample(clock, "absent")])
    assert policy.state == "stop-pending"


def test_cancel_survives_detection_gap(run):
    policy, clock = run
    operation = offer(run)
    policy.action(operation, "cancel")
    clock.value += 60
    policy.observe([])
    for _ in range(3):
        clock.value += 1
        policy.observe([sample(clock)])
    assert policy.state == "idle"
    clock.value += 1
    policy.observe([sample(clock, "ended")])
    clock.value += 1
    policy.observe([sample(clock)])
    clock.value += 1
    policy.observe([sample(clock)])
    assert policy.state == "start-pending"


def test_manual_origin_excludes_auto_stop(run):
    policy, clock = run
    offer(run)
    policy.action(policy.operation, "start")
    policy.started(policy.recording, True)
    for _ in range(30):
        clock.value += 5
        policy.observe([sample(clock, "ended")])
    assert policy.state == "recording"
    assert policy.origin == "manual"


def test_duplicate_and_stale_callbacks(run):
    policy, clock = run
    old = recording(run)
    policy.stop()
    policy.stopped(old)
    assert old in policy.finalizing
    policy.start()
    new = policy.recording
    policy.started(old, True)
    policy.stopped(old)
    policy.finalized(old)
    assert policy.recording == new
    assert policy.state == "starting"


def test_cancel_start_then_late_success_stops_same_operation(run):
    policy, clock = run
    recording_id = recording(run)
    policy.stop()
    policy.stopped(recording_id)
    policy.start()
    policy.cancel()
    policy.started(policy.recording, True)
    assert policy.state == "stopping"


def test_unresolved_stop_holds_ownership(run):
    policy, clock = run
    recording(run)
    policy.stop()
    policy.stopped(policy.recording, success=False)
    assert policy.state == "capture-unresolved"
    assert not policy.start()


def test_wake_does_not_execute_overdue_start(run):
    policy, clock = run
    offer(run)
    policy.sleep()
    clock.value += 100
    policy.tick()
    assert policy.state == "idle"


def test_submission_timeout_and_late_ack_cannot_rearm(run):
    policy, clock = run
    offer(run)
    policy.cancel()
    operation = policy.operation
    assert not policy.offered(operation, True)
    assert policy.state == "idle"


def test_config_disable_cancels_countdown(run):
    policy, clock = run
    offer(run)
    policy.configure({**policy.config, "automatic_recording": False})
    assert policy.state == "idle"


def test_other_call_prevents_auto_stop(run):
    policy, clock = run
    recording(run)
    clock.value += 1
    policy.observe([sample(clock, "ended"), sample(clock, episode="other")])
    assert policy.state == "recording"
    assert policy.episodes == {"call", "other"}


def test_missing_snapshot_cancels_start_without_rearming(run):
    policy, clock = run
    offer(run)
    clock.value += 1
    policy.observe([])
    assert policy.state == "idle"
    assert "call" in policy.suppressed


def test_expired_notification_action_cannot_start_capture(run):
    policy, clock = run
    operation = offer(run)
    clock.value += 5
    policy.action(operation, "start")
    assert policy.state == "start-pending"
    assert not any(e.kind == "start" for e in policy.drain())


def test_completed_episode_does_not_block_later_concurrent_call(run):
    policy, clock = run
    recording(run)
    clock.value += 1
    policy.observe([sample(clock, "ended"), sample(clock, episode="second")])
    clock.value += 1
    policy.observe([sample(clock, episode="second")])
    assert policy.observations["call"].state == "ended"
    clock.value += 1
    policy.observe([sample(clock, "ended", episode="second")])
    assert policy.state == "stop-pending"


def test_async_verification_holds_expired_deadline_until_fresh_result(run):
    policy, clock = run
    offer(run)
    policy.evidence_pending = True
    clock.value += 5
    policy.tick()
    assert policy.state == "start-pending"
    policy.evidence_pending = False
    policy.observe([sample(clock)])
    assert policy.state == "starting"
