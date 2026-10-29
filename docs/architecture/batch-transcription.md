# Batch transcription and recording-file ownership

The CLI and Qt UI share a synchronous use case instead of making headless callers construct
QObject workers. The boundaries are:

- `transcription/client.py`: Soniox HTTP requests, authentication headers and status polling.
  Polling uses elapsed monotonic time; it never publishes local transcript files.
- `transcription/service.py`: options, upload/resume/translation orchestration, rendering and
  atomic publication. It accepts a progress callback and an injectable client, and imports no Qt.
- `transcription/soniox.py`: the Qt adapter. It converts service progress, return values and
  exceptions into signals; thread ownership remains with `ui/processing.py`.
- `transcription/cli.py`: argument/result formatting and headless credential lookup. JSON keys,
  exit codes, dry-run behavior, no-clobber output and provider-error redaction remain stable.
- `transcription/jobs.py`: process-shared locks and checkpoint schema validation. Both recovery
  and recording-list status use the same parser. Unknown versions, invalid field types and
  polling stages with missing provider IDs are rejected before network work begins.

`TranscriptionOptions.from_config` owns language-hint and automatic-language rules for both
entry points. The CLI deliberately disables translation; GUI recovery of a translated job
still requires matching settings. Version-1 checkpoints and existing transcripts remain
compatible. A submission whose response was lost remains blocked for manual provider review.

Transcript publication is the success commit point. If writing the final completed checkpoint
fails after publication, the operation still succeeds: the earlier checkpoint retains remote
IDs, and another invocation preserves/skips the existing transcript.

## File operations

`RecordingManager` owns move, rename and delete, including every sidecar in
`SIDECAR_SUFFIXES`. UI code selects paths and presents errors. It does not move sidecars itself.
All mutations acquire the same recording lock used by GUI and CLI transcription; transfers
hold both source and destination locks until audio and sidecars have moved together.

Transfers preflight every destination, including orphan sidecars and dangling symlinks, so
another recording's transcript/recovery state is never intentionally overwritten. An ordinary
transfer failure rolls back completed file moves while locks remain held. If rollback also
fails, the error names the remaining destination paths for recovery. This is not a multi-file
transaction across a machine crash or filesystem failure.

Permanent `.transcription.lock` inodes remain after moves/deletes. Never unlink an active lock
or use its mere existence as evidence that a job is running. The kernel releases ownership
when its process exits. Legacy `.md.transcribing` markers retain their manual-review behavior.

## Validation

The existing provider-mocked CLI, Qt-worker, queue, recovery, translation and sidecar tests
characterize the shared behavior. Additional regression cases deny all PyQt6 imports while
running both CLI dry-run and successful transcription, reject malformed checkpoints without
network work, inject a failed final status write, refuse mutations under active source or
destination locks, and restore files after an injected sidecar-transfer failure. No real
provider upload, new audio capture or user-file mutation is required for these checks.
