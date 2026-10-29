import importlib.util
from pathlib import Path

from audiorecorder.meetings.native_profiles import signing_identity

path = Path(__file__).resolve().parent.parent / "scripts/replay_native_lifecycle.py"
spec = importlib.util.spec_from_file_location("native_replay", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
replay = module.replay


def sample(at, *, active=False, window="main", complete=True):
    return {
        "at": at,
        "permission": "granted",
        "apps": [
            {
                "bundle": "com.microsoft.teams2",
                "version": "test-version",
                "process": "1:1",
                "signer": signing_identity("com.microsoft.teams2"),
                "running": True,
                "complete": complete,
                "windows": [
                    {
                        "identifier": window,
                        "complete": complete,
                        "controls": ["hangup", "microphone", "camera"] if active else [],
                    }
                ],
            }
        ],
    }


def test_leave_trace_only_stops_with_explicit_absence_coverage():
    trace = [
        sample(at, active=at < 12, window="call" if at < 12 else "main") for at in range(0, 66, 2)
    ]
    baseline = replay(trace)
    assert [c["command"] for c in baseline["commands"]] == ["start"]
    result = replay(trace, allow_window_absence=True)
    assert result["simulation_only"] and result["window_absence_enabled"]
    start, stop = result["commands"]
    assert start["command"] == "start" and stop["command"] == "stop"
    assert stop["at"] >= 12 + 45 + 5


def test_incomplete_or_still_present_call_window_cannot_stop_by_absence():
    for incomplete, window in ((True, "main"), (False, "call")):
        trace = [
            sample(
                at,
                active=at < 12,
                window="call" if at < 12 else window,
                complete=at < 12 or not incomplete,
            )
            for at in range(0, 80, 2)
        ]
        assert [c["command"] for c in replay(trace, allow_window_absence=True)["commands"]] == [
            "start"
        ]
