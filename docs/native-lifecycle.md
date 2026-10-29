# Native recording lifecycle

The macOS notification, lifecycle and native call-control detector are implemented. The
preview is ready for supervised desktop acceptance. No Teams or Zoom version is approved
by default, and automated fixtures do not establish real-provider reliability.

## Evidence and qualification

| Source | Lifecycle use |
| --- | --- |
| Native Teams/Zoom Accessibility controls | Candidate joined/end evidence; requires explicit inspection opt-in, permission and tested app-version approval or a temporary supervised test |
| Existing window/geometry and Teams media metadata | Advisory recording prompts |
| Webex/Slack windows, browser companion, Calendar | Advisory recording prompts |

`macos_call_probe.py` runs in a disposable child with time, node, window and output bounds.
It reads control roles/enabled state and matches exact English labels. It excludes text
fields, window titles, participants and chat values; only allowlisted control meanings leave
the child. Localized labels and unrecognized layouts remain unqualified. Permission requests
occur only through the user's Settings action.

`native_calls.py` owns pure recognition and provider call-group identity. Joined evidence
requires hangup, microphone and camera controls in the same complete window without join or
rejoin controls. Two complete samples showing rejoin in the tracked windows, or verified
process exit/replacement, can end a group. Alternate full/mini windows are grouped; another
active window keeps the group alive. A vanished app-registry entry cannot end a still-live
process. Unreadable, hidden or ambiguous surfaces yield unknown, preserving capture.

Window-absence stopping is a separate experimental opt-in/approval. A background native
observer subscribes only to the specific accessibility window identities that exposed joined
controls. Destruction events for all tracked call windows corroborate their closure even if
Teams retains unrelated WindowServer surfaces. Unknown processes, unrelated window events,
stale callbacks after sleep, incomplete scans and returning active controls cannot stop a
recording. The same 45-second grace and five-second stop countdown apply. A complete nonempty
remaining window collection or an independently empty Quartz inventory can also establish
absence under the same explicit scope. Test minimized/hidden calls beyond grace before
permanent approval. Observer status and closed-call-window counts are visible in Settings;
exports also include lifecycle effect history and capture origin.

`call_monitor.py` schedules one bounded child at a time, rejects stale/generation-invalid
results and expires evidence. A supervised test lasts 30 minutes and does not save automatic
preferences. Persistent approval is bound to app version and matcher rules; changes invalidate
it. Approval requires observed joined/end transitions plus explicit real-call checklist
attestation. Absence approval additionally requires an acknowledged absence stop. Exported
traces contain normalized controls and aliased process/window identities, capped at 256
snapshots. Replay them without Qt, permissions or capture:

```sh
.venv/bin/python scripts/replay_native_lifecycle.py native-call-diagnostics.json --output replay.json
```

Replay never changes real approvals. Add `--allow-window-absence` to simulate the separately
enabled absence path with its 45-second grace; the default still uses positive-end evidence.

## Lifecycle and capture ownership

`meetings/lifecycle.py` owns monotonic deadlines, suppression, capture state and operation IDs.
`ui/lifecycle_adapter.py` marshals detector/native events and offers notification or in-app
actions. Deadlines require a new scan before committing. Sleep/unknown health invalidate
pending actions. Start and stop countdowns default independently to five seconds. Manual
recordings do not auto-stop; cancel/manual stop suppress the current call episode. Native
explicit dismissal cancels/keeps, while banner timeout does not imply a user choice.

Unattended start additionally requires granted microphone permission and a successful Test
Audio for this build/OS, saved devices and output folder. The test must receive frames from
each configured input and finish validated encoding. Device resolution is checked before
opening, and a fallback after opening is rejected and closed. Failed capture/finalization
invalidates readiness. These checks cannot substitute for listening to the audio or testing
system-capture permission revocation in the real app.

`audio/session.py` serializes blocking start/stop outside the GUI. Unresolved shutdown retains
ownership and exposes Retry Capture Shutdown. Jobs keep recording IDs and immutable settings.
One encoder runs at a time; at most three finalizations, including queued work, are accepted.
The encoder mixes/resamples in bounded blocks and publishes validated M4A through a staging
file and atomic no-overwrite hard link. Unsupported publication retains source WAVs. Cleanup
failure after publication is a warning; empty capture is an error, while silent frames are
valid. Recovery sidecars preserve failure details and same-run transcripts. Power-loss
recovery is not guaranteed. Synthetic eight-hour input checks bound allocation; real
long-duration desktop acceptance remains outstanding.

Native callbacks acknowledge malformed responses exactly once. Settings independently
observes notifications, capture/device readiness and login approval. Unrelated Save cannot
reverse OS login-item revocation. Source/build copies cannot register login items; install
under Applications. Single-instance startup fails closed before validated atomic config saves.
Optional credential diagnostics use a bounded child with credential UI disabled.

## Verification and desktop acceptance

Run `QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q`, `.venv/bin/ruff check .`,
`.venv/bin/python scripts/generate_third_party.py --check` and packaged runtime smoke.
`--notification-smoke PATH` queries authorization and initializes bundle/delegate setup
without requesting permissions, sending banners, capturing audio or registering login items.

Follow [the real-call checklist](native-call-test-checklist.md). Provider layouts, permission
revocation, Focus/actions, login/manual launch, upgrade/rollback and actual decoded dual-input
audio require desktop testing. The existing installed app is preserved during preview build.

## Refactor review

The requested `planning-safe-refactors` review preserves separate pure policy, Qt transport,
native IO and capture/encoding ownership. Findings fixed include partial-start cleanup,
late-event job identity, encoder backlog, atomic publication, shutdown ownership, stale
deadline evidence, consecutive call groups, sleep invalidation, app-version qualification,
resolved-device changes/fallback, safe diagnostic export and long-recording encoder memory.
Regression tests cover those material paths; real UI/provider behavior remains explicitly
outside fixture coverage.
