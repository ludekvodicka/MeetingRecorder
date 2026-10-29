# Session end, recovery and one copy per user

## Decision

A recording must survive a Windows sign-out or a crash, and a second Windows user on the same
machine must be able to run the application. Closing the window during a recording asks first.

## Behavior

- **Sign-out during a recording.** `QApplication.commitDataRequest` (Windows
  `WM_QUERYENDSESSION`) calls `MainWindow.end_session`. It stops the capture and waits up to
  `SESSION_END_STOP_SECONDS` (4 s) for the capture thread, so both WAV files are closed before
  Windows can kill the process. Encoding may not finish in time.
- **Recovery at start-up.** `RecordingManager.orphaned_captures` finds `_rec_mic_<id>.wav`
  files, with their `_rec_system_<id>.wav` partner when one exists, in the output directory.
  The encoder deletes both after a successful encode, so any left at start-up belong to a
  session that ended or crashed mid-recording. `MainWindow._recover_orphaned_captures` encodes
  each pair into a new recording named after the last write. libsndfile reads the audio even
  when the process died before it wrote the final WAV header.
- **A capture that cannot be encoded** is renamed `Unrecovered_mic_<id>.wav` /
  `Unrecovered_system_<id>.wav`, so start-up does not retry it, and the user is told where it is.
- **Close during a recording.** A close the user starts asks "Stop, save it and exit?". Quit
  from the tray menu, a quit already in progress and a session end do not ask, because nobody
  is there to answer during a sign-out.
- **One copy per user.** A `QLockFile` in the user's temporary directory arbitrates. The local
  socket that lets a second start bring the window forward is named per user
  (`user_server_name`, a hash of the home directory), because Windows named pipes are
  machine-wide. A copy of an older build listens on `LEGACY_SERVER_NAME`; a new start checks
  that name too, so two builds do not install two keyboard hooks. If the socket cannot listen,
  the copy still runs: the lock already keeps a second copy out.

## Entry points

- `audiorecorder/app.py`: `end_session` on `commitDataRequest`, `SingleInstance` setup.
- `audiorecorder/ui/main_window.py`: `end_session`, `_recover_orphaned_captures`, `closeEvent`.
- `audiorecorder/recordings/manager.py`: `orphaned_captures`, `set_aside_capture`.
- `audiorecorder/audio/session.py`: `CaptureSession.wait_idle`.
- `audiorecorder/single_instance.py`: `SingleInstance`, `user_server_name`.

## Constraints and trade-offs

- Recovery scans only the current output directory. Capture files left in a previous output
  directory stay where they are.
- A cancelled sign-out leaves the recording stopped and saved; the window does not ask again
  on close in that run, because the session-end flag stays set.
- Live subtitles and speaker sidecars of an interrupted recording are lost; only the audio
  is recovered.
- Tests: `tests/test_session_recovery.py`, `tests/test_single_instance.py`.
