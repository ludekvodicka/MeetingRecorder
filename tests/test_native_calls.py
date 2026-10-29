from dataclasses import replace

import pytest

from audiorecorder.meetings.native_calls import (
    NativeAppSample,
    NativeCallTracker,
    WindowControls,
    control_kind,
    qualification_key,
)
from audiorecorder.meetings.native_profiles import signing_identity

TEAMS = "com.microsoft.teams2"
ZOOM = "us.zoom.xos"
ACTIVE = {"hangup", "microphone", "camera"}
TEAMS_SIGNER = signing_identity(TEAMS)
ZOOM_SIGNER = signing_identity(ZOOM)


def app(
    controls=ACTIVE,
    *,
    complete=True,
    window="call",
    process="1:100",
    running=True,
    bundle=TEAMS,
    extra=(),
):
    return NativeAppSample(
        bundle,
        "1.2",
        process,
        running,
        complete,
        (WindowControls(frozenset(controls), complete, window), *extra),
        signer=signing_identity(bundle),
    )


@pytest.mark.parametrize(
    "label,kind",
    [
        ("Leave", "hangup"),
        ("Mute (Ctrl+Shift+M)", "microphone"),
        ("Camera", "camera"),
        ("Join now", "join"),
        ("Rejoin", "rejoin"),
        ("Chat with Jane", None),
        ("Leave this channel", None),
        ("Meeting notes", None),
    ],
)
def test_control_meanings_are_an_exact_allowlist(label, kind):
    assert control_kind(label) == kind


def test_control_sets_must_belong_to_same_complete_window():
    assert app().state == "active"
    assert app(ACTIVE | {"join"}).state == "prejoin"
    assert app(ACTIVE, complete=False).state == "unknown"
    assert (
        app({"hangup"}, extra=(WindowControls(frozenset({"microphone", "camera"})),)).state
        == "idle"
    )


def test_qualification_bound_to_provider_and_app_version():
    assert qualification_key(TEAMS, "1", TEAMS_SIGNER) != qualification_key(
        TEAMS, "2", TEAMS_SIGNER
    )
    assert qualification_key(TEAMS, "1", TEAMS_SIGNER) != qualification_key(
        ZOOM, "1", ZOOM_SIGNER
    )
    assert not qualification_key(TEAMS, "1", "forged:com.microsoft.teams2")
    assert not qualification_key("browser", "1", "forged")


def test_diagnostics_never_qualifies_provider_implicitly():
    tracker = NativeCallTracker("test")
    (observation,) = tracker.update([app()], 1)
    assert observation.state == "active"
    assert not observation.auto_start and not observation.auto_stop


def test_hiding_or_closing_a_call_window_is_unknown_not_ended():
    tracker = NativeCallTracker("test")
    (original,) = tracker.update([app()], 1, {TEAMS})
    (hidden,) = tracker.update([app(set())], 2, {TEAMS})
    assert original.auto_start
    assert hidden.state == "unknown"
    assert hidden.episode == original.episode
    (resumed,) = tracker.update([app(window="mini-panel")], 3, {TEAMS})
    assert resumed.episode == original.episode


def test_rejoin_must_match_tracked_window_and_repeat():
    tracker = NativeCallTracker("test")
    tracker.update([app()], 1, {TEAMS})
    (unrelated,) = tracker.update([app({"rejoin"}, window="other")], 2, {TEAMS})
    assert unrelated.state == "unknown"
    (first,) = tracker.update([app({"rejoin"})], 3, {TEAMS})
    assert first.state == "unknown"
    (ended,) = tracker.update([app({"rejoin"})], 4, {TEAMS})
    assert ended.state == "ended" and ended.auto_stop
    (new,) = tracker.update([app()], 5, {TEAMS})
    assert new.episode != ended.episode


def test_other_active_window_prevents_terminal_group():
    tracker = NativeCallTracker("test")
    tracker.update([app()], 1, {TEAMS})
    (observed,) = tracker.update(
        [app({"rejoin"}, extra=(WindowControls(frozenset(ACTIVE), True, "other-call"),))],
        2,
        {TEAMS},
    )
    assert observed.state == "active"


def test_process_exit_requires_two_observations_and_rearms_new_process():
    tracker = NativeCallTracker("test")
    (original,) = tracker.update([app()], 1, {TEAMS})
    exiting = app(set(), running=False)
    assert tracker.update([exiting], 2, {TEAMS})[0].state == "unknown"
    assert tracker.update([exiting], 3, {TEAMS})[0].state == "ended"
    observations = tracker.update([app(process="2:200")], 4, {TEAMS})
    assert observations[-1].state == "active"
    assert observations[-1].episode != original.episode


def test_missing_provider_and_out_of_order_samples_cannot_end_call():
    tracker = NativeCallTracker("test")
    tracker.update([app()], 5, {TEAMS})
    assert tracker.update([], 6, {TEAMS})[0].state == "unknown"
    assert tracker.update([app({"rejoin"})], 4, {TEAMS})[0].state == "unknown"


def test_window_absence_requires_separately_verified_coverage():
    tracker = NativeCallTracker("test")
    tracker.update([app()], 1, {TEAMS})
    assert tracker.update([app(set(), window="main")], 2, {TEAMS})[0].state == "unknown"
    (absence,) = tracker.update([app(set(), window="main")], 3, {TEAMS}, {TEAMS})
    assert absence.state == "absent" and absence.observable
    # A hidden/unreadable window is unknown even with absence coverage enabled.
    (unknown,) = tracker.update([app(set(), window="main", complete=False)], 4, {TEAMS}, {TEAMS})
    assert unknown.state == "unknown"
    # If the tracked call window still exists with hidden controls, it is not absence.
    assert tracker.update([app(set())], 5, {TEAMS}, {TEAMS})[0].state == "unknown"
    tracker.retire({absence.episode})
    (resumed,) = tracker.update([app()], 6, {TEAMS}, {TEAMS})
    assert resumed.episode != absence.episode


def test_verified_absence_completes_episode_even_after_manual_stop():
    tracker = NativeCallTracker("test")
    (old,) = tracker.update([app()], 1, {TEAMS}, {TEAMS})
    tracker.update([app(set(), window="main")], 2, {TEAMS}, {TEAMS})
    for at in range(7, 48, 5):
        (ended,) = tracker.update([app(set(), window="main")], at, {TEAMS}, {TEAMS})
    assert ended.state == "ended"
    assert ended.source == "native-call-controls-absence"
    (new,) = tracker.update([app()], 49, {TEAMS}, {TEAMS})
    assert new.episode != old.episode


def test_unknown_health_resets_absence_grace():
    tracker = NativeCallTracker("test")
    tracker.update([app()], 1, {TEAMS}, {TEAMS})
    tracker.update([app(set(), window="main")], 2, {TEAMS}, {TEAMS})
    tracker.update([], 40, {TEAMS}, {TEAMS})
    (still_absent,) = tracker.update([app(set(), window="main")], 47, {TEAMS}, {TEAMS})
    assert still_absent.state == "absent"


@pytest.mark.parametrize("corroborated", [False, True])
def test_zero_windows_need_independent_coverage_before_absence(corroborated):
    tracker = NativeCallTracker("zero-windows")
    tracker.update([app()], 1, {TEAMS}, {TEAMS})
    empty = replace(app(), windows=(), empty_windows_confirmed=corroborated)
    for at in range(2, 54, 2):
        observations = tracker.update([empty], at, {TEAMS}, {TEAMS})
        if observations:
            (observation,) = observations
    assert observation.state == ("ended" if corroborated else "unknown")


def test_independent_empty_inventory_cannot_override_unhealthy_accessibility():
    tracker = NativeCallTracker("zero-windows")
    tracker.update([app()], 1, {TEAMS}, {TEAMS})
    empty = replace(app(complete=False), windows=(), empty_windows_confirmed=True)
    assert tracker.update([empty], 2, {TEAMS}, {TEAMS})[0].state == "unknown"


def test_known_window_destruction_allows_grace_despite_unrelated_ghost_windows():
    tracker = NativeCallTracker("closed-call")
    tracker.update([app()], 1, {TEAMS}, {TEAMS})
    empty = replace(app(), windows=())
    assert tracker.update([empty], 2, {TEAMS}, {TEAMS})[0].state == "unknown"
    unrelated = replace(empty, closed_windows=frozenset({"not-the-call"}))
    assert tracker.update([unrelated], 3, {TEAMS}, {TEAMS})[0].state == "unknown"
    closed = replace(empty, closed_windows=frozenset({"call"}))
    assert tracker.update([closed], 4, {TEAMS}, {TEAMS})[0].state == "absent"
    for at in range(6, 50, 2):
        assert tracker.update([closed], at, {TEAMS}, {TEAMS})[0].state == "absent"
    assert tracker.update([closed], 50, {TEAMS}, {TEAMS})[0].state == "ended"


def test_closure_cannot_override_an_active_alternate_call_window():
    tracker = NativeCallTracker("closed-call")
    tracker.update([app()], 1, {TEAMS}, {TEAMS})
    active = replace(app(window="mini"), closed_windows=frozenset({"call"}))
    assert tracker.update([active], 2, {TEAMS}, {TEAMS})[0].state == "active"
