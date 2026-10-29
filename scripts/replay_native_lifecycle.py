"""Replay exported, content-free detector traces without capture, Qt or permissions.

Usage: python scripts/replay_native_lifecycle.py diagnostics.json [--output replay.json]
The replay approves supported matchers only inside this simulation. It never modifies the
recorder's configuration or installs qualification evidence in the real application.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audiorecorder.meetings.lifecycle import Lifecycle  # noqa: E402
from audiorecorder.meetings.native_calls import (  # noqa: E402
    NativeCallTracker,
    parse_snapshot,
)
from audiorecorder.meetings.native_profiles import PROFILES  # noqa: E402


def replay(trace, *, allow_window_absence=False):
    now = 0.0
    policy = Lifecycle(
        {"automatic_recording": True, "auto_stop_recording": True},
        clock=lambda: now,
        token="replay",
    )
    tracker = NativeCallTracker("trace")
    commands = []
    states = []
    last = -1.0
    for entry in trace:
        at = entry.get("at")
        if type(at) not in (int, float) or not at > last:
            raise ValueError("trace timestamps must increase")
        now, last = float(at), float(at)
        samples = parse_snapshot(entry)
        policy.observe(
            tracker.update(
                samples,
                now,
                frozenset(PROFILES),
                frozenset(PROFILES) if allow_window_absence else frozenset(),
            )
        )
        while effects := policy.drain():
            for effect in effects:
                if effect.kind.startswith("offer-"):
                    policy.offered(effect.operation, True)
                elif effect.kind == "start":
                    commands.append({"at": now, "command": "start", "recording": effect.recording})
                    policy.started(effect.recording, True)
                elif effect.kind == "stop":
                    commands.append({"at": now, "command": "stop", "recording": effect.recording})
                    policy.stopped(effect.recording, has_audio=False)
        states.append({"at": now, "state": policy.state})
    return {
        "simulation_only": True,
        "window_absence_enabled": allow_window_absence,
        "commands": commands,
        "states": states,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("diagnostics", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--allow-window-absence",
        action="store_true",
        help="Simulate separately enabled window-absence stopping with 45-second grace",
    )
    args = parser.parse_args()
    value = json.loads(args.diagnostics.read_text(encoding="utf-8"))
    result = replay(value["trace"], allow_window_absence=args.allow_window_absence)
    text = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
