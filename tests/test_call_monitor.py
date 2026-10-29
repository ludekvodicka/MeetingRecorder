from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QApplication

from audiorecorder.meetings.call_monitor import NativeCallMonitor, parse_snapshot
from audiorecorder.meetings.native_calls import qualification_key

BUNDLE = "com.microsoft.teams2"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def snapshot(controls=("hangup", "microphone", "camera"), *, version="1", running=True):
    return {
        "permission": "granted",
        "apps": [
            {
                "bundle": BUNDLE,
                "process": "123:100",
                "version": version,
                "complete": True,
                "running": running,
                "windows": [{"controls": list(controls), "complete": True, "identifier": "call"}],
            }
        ],
    }


def test_schema_rejects_arbitrary_text_and_unknown_providers():
    for value in (
        None,
        {},
        {"permission": "granted", "apps": [{}]},
        snapshot(("private participant name",)),
    ):
        with pytest.raises(ValueError):
            parse_snapshot(value)


def test_monitor_qualification_and_expiring_test_are_explicit(app):
    clock = SimpleNamespace(value=10.0)
    monitor = NativeCallMonitor(clock=lambda: clock.value, process_alive=lambda pid: False)
    monitor.enabled = True
    monitor._accept(snapshot(), 10, 0)
    assert not monitor.observations()[0].auto_start
    monitor.test_bundle, monitor.test_until = BUNDLE, 100
    clock.value = 12
    monitor._accept(snapshot(), 12, 0)
    assert monitor.observations()[0].auto_start
    clock.value = 101
    assert not monitor.testing
    assert not monitor.observations()[0].auto_start
    monitor.close()


def test_version_change_invalidates_qualification_and_test_evidence(app):
    clock = SimpleNamespace(value=10.0)
    monitor = NativeCallMonitor(clock=lambda: clock.value, process_alive=lambda pid: False)
    monitor.enabled = True
    monitor.config = {"native_call_qualifications": {BUNDLE: qualification_key(BUNDLE, "1")}}
    monitor._accept(snapshot(), 10, 0)
    assert monitor.observations()[0].auto_start
    clock.value = 12
    monitor._accept(snapshot(version="2"), 12, 0)
    assert not monitor.observations()[0].auto_start
    assert not monitor.can_approve(BUNDLE)
    monitor.close()


def test_process_exit_keeps_version_qualification_for_stop(app):
    clock = SimpleNamespace(value=10.0)
    monitor = NativeCallMonitor(clock=lambda: clock.value, process_alive=lambda pid: False)
    monitor.enabled = True
    monitor.config = {"native_call_qualifications": {BUNDLE: qualification_key(BUNDLE, "1")}}
    monitor._accept(snapshot(), 10, 0)
    clock.value = 11
    monitor._accept(snapshot(), 11, 0)
    ended = snapshot((), version="", running=False)
    for value in (12.0, 14.0):
        clock.value = value
        monitor._accept(ended, value, 0)
    assert monitor.observations()[0].state == "ended"
    assert monitor.observations()[0].auto_stop
    assert monitor.can_approve(BUNDLE)
    monitor.close()


def test_invalid_and_stale_results_preserve_unknown_and_do_not_leak_content(app):
    clock = SimpleNamespace(value=10.0)
    monitor = NativeCallMonitor(clock=lambda: clock.value, process_alive=lambda pid: False)
    monitor.enabled = True
    monitor._accept(snapshot(), 10, 0)
    clock.value = 20
    monitor._accept(snapshot(), 11, 0)
    assert monitor.error == "invalid-or-stale-probe"
    assert monitor.observations()[0].state == "unknown"
    assert monitor.diagnostics()["apps"] == []
    monitor.close()


def test_registry_omission_cannot_stop_a_still_live_process(app):
    clock = SimpleNamespace(value=10.0)
    monitor = NativeCallMonitor(clock=lambda: clock.value, process_alive=lambda pid: True)
    monitor.enabled = True
    monitor.test_bundle, monitor.test_until = BUNDLE, 100
    monitor._accept(snapshot(), 10, 0)
    for at in (12.0, 14.0, 16.0):
        clock.value = at
        monitor._accept(snapshot((), version="", running=False), at, 0)
    assert monitor.observations()[0].state == "unknown"
    assert not monitor.can_approve(BUNDLE)
    monitor.close()


def test_exported_trace_has_replayable_aliases_and_no_raw_process_identifiers(app, tmp_path):
    import importlib.util
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts/replay_native_lifecycle.py"
    spec = importlib.util.spec_from_file_location("native_replay", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    replay = module.replay

    clock = SimpleNamespace(value=10.0)
    monitor = NativeCallMonitor(clock=lambda: clock.value, process_alive=lambda pid: False)
    monitor.enabled = True
    monitor.test_bundle, monitor.test_until = BUNDLE, 1000
    for at, controls in [
        (10, ("join", "microphone", "camera")),
        (12, ("hangup", "microphone", "camera")),
        (14, ("hangup", "microphone", "camera")),
        (20, ("hangup", "microphone", "camera")),
        (22, ("rejoin",)),
        (24, ("rejoin",)),
        (30, ("rejoin",)),
    ]:
        clock.value = at
        monitor._accept(snapshot(controls), at, 0)
    destination = tmp_path / "diagnostics.json"
    monitor.export_diagnostics(destination)
    text = destination.read_text()
    assert "123:100" not in text
    report = json.loads(text)
    assert report["schema"] == 1
    result = replay(report["trace"])
    assert [entry["command"] for entry in result["commands"]] == ["start", "stop"]
    assert result["simulation_only"]
    monitor.close()


def test_absence_approval_is_separate_and_revocation_is_immediate(app):
    clock = SimpleNamespace(value=10.0)
    monitor = NativeCallMonitor(clock=lambda: clock.value, process_alive=lambda pid: False)
    monitor.enabled = True
    monitor.test_bundle, monitor.test_until, monitor.test_absence = BUNDLE, 100, True
    monitor._accept(snapshot(), 10, 0)
    gone = snapshot(())
    gone["apps"][0]["windows"][0]["identifier"] = "main"
    clock.value = 12
    monitor._accept(gone, 12, 0)
    assert monitor.observations()[0].state == "absent"
    monitor.test_absence = False
    assert monitor.observations()[0].state == "unknown"
    monitor.close()


def test_window_closure_only_accepts_current_generation_process_and_known_call(app):
    clock = SimpleNamespace(value=10.0)
    monitor = NativeCallMonitor(clock=lambda: clock.value, process_alive=lambda pid: False)
    monitor.enabled = True
    monitor.request = lambda: None
    monitor.test_bundle, monitor.test_until, monitor.test_absence = BUNDLE, 100, True
    monitor._accept(snapshot(), 10, 0)
    for process, identifier, generation in [
        ("other", "call", 0),
        ("123:100", "other", 0),
        ("123:100", "call", -1),
    ]:
        monitor._closed(BUNDLE, process, identifier, generation)
    assert not any(monitor.closed_windows.values())
    monitor._closed(BUNDLE, "123:100", "call", 0)
    empty = snapshot(())
    empty["apps"][0]["windows"] = []
    clock.value = 12
    monitor._accept(empty, 12, 0)
    assert monitor.observations()[0].state == "absent"
    monitor.reset()
    assert not monitor.closed_windows
    monitor._closed(BUNDLE, "123:100", "call", 0)
    assert not monitor.closed_windows
    monitor.close()
