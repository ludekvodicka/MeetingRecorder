# Native call automation: joint desktop test

Development and fixture checks are complete. This checklist establishes what actually works
with the installed preview, macOS permissions and your Teams/Zoom version. No app version is
preapproved. Start with English application controls; other languages need matcher work.

## Prepare the installed preview

1. Finish any recording, wait for Saved, and quit the current app. Preserve the previous app
   for rollback, then install the DMG's Meeting Recorder.app under Applications. Launch it
   manually. The preview uses ad hoc signing; notification/login behavior needs testing on
   this installation. Source and temporary build copies cannot register for login.
2. Save your chosen audio devices and output folder. Request/grant Microphone and Screen &
   System Audio Recording as needed, restart if macOS requires it, then run **Test Audio
   (3 seconds)**. Play the saved file: verify microphone and another app's audio are audible.
   Frame/encoding checks alone do not prove audible content. Check Settings Readiness.
3. Request/test notifications in Readiness. Check native action delivery with Focus off,
   then on. The in-app countdown must remain usable when banners are unavailable.
4. Enable **Inspect native Teams/Zoom call controls (Accessibility)** and Save. Use **Request
   Accessibility Access** / **Open Accessibility Settings**, grant the installed recorder
   access, then refresh. Diagnostics must show granted. This is separate from screen/audio
   permission. Do not approve an app yet.

## Run a supervised call cycle

Keep the recorder visible and stay present. Select Teams or Zoom, then **Start supervised
automation test (30 minutes)**. This temporarily enables automatic start/stop without saving
those preferences. Start while no recording is active. End test stops the experiment but
preserves any recording already in progress; stop that recording manually if needed.

- Before joining: idle, lobby, prejoin camera preview and chat must not start recording.
- Join: diagnostics should recognize joined controls. Confirm the five-second start
  countdown, Cancel, suppression for that same call, and a fresh countdown on a later call.
  Repeat with Start Now. Confirm both audio sources and the saved file.
- During a fresh automatic recording: mute/unmute, camera off/on, minimize, change Spaces,
  enter full-screen and keep the call hidden for at least 60 seconds. Recording must continue.
  Check reconnect/network interruption and permission loss: unknown must preserve capture.
- Leave: inspect whether a confirmed end is recognized. Process exit is another positive-end
  path. Confirm stop countdown and Keep Recording. A manual-origin recording must not stop
  automatically. A missing window alone is not a confirmed end in the default mode.
- Repeat consecutive calls, alternate full/mini controls, and concurrent supported app calls.
  The last active call must keep an automatic recording running. Check no duplicate capture.
- Exercise microphone removal/default-device changes: automatic start must require another
  audio test when resolved devices change. Test capture failure, recovery and saving before
  quitting; do not discard retained WAVs when recovery is reported.

If normal leave only removes the call window, first complete the hidden/minimized/Spaces
checks above. Then optionally start another supervised test with **Include window-absence
auto-stop**. This separate experiment waits through the grace (45 seconds by default) and
stop countdown. Keep the app's main window open. Test hidden calls longer than grace and
reconnection again; stop and leave this option unapproved if either causes a false stop.
Unreadable window collections must never count as absence. An empty accessible collection
requires the independent all-Spaces inventory to confirm no remaining process-owned windows.

## Export, approve, and verify installation behavior

1. Use **Export detector diagnostics…** after a representative cycle or failure. The rolling
   trace has at most 256 snapshots (roughly eight minutes at the normal polling interval),
   contains normalized control meanings and aliases, and excludes meeting/chat text. Export
   soon after a failure. Replay with `scripts/replay_native_lifecycle.py` for analysis.
2. Only after the checklist passes, tick its attestation and choose **Approve this tested app
   version**, then Save. The app also requires joined and confirmed-ended transitions in
   this session. Absence approval requires a successful absence-stop test and its checkbox.
   Approval is scoped to the installed provider version and matcher. Use **Remove approval
   (apply on Save)** if any case fails. Record the tested macOS/provider versions with results.
3. Enable automatic recording and automatic stopping as desired, Save, and repeat a complete
   cycle outside supervised mode. Retest after a new recorder build (audio test) or provider
   update (provider approval). Keep manual controls available.
4. Separately verify login launch versus manual launch, OS login-item revocation, notification
   Start/Cancel/Keep/dismiss actions and stale actions, sleep/wake, quit during start/stop/save,
   and rollback to the preserved app. Run a longer recording and listen to both sources.

Until those desktop checks pass, use manual recording for meetings that must be captured.
