# Automatic call recording on Windows

## Decision

On 2026-11-06 a Teams web call in Brave was not detected, and the owner asked for recording
that runs by itself: start as soon as a call is seen, stop when it is gone, no confirmation.
The evidence is the microphone: Windows records which program holds it. The owner chose that
automation starts without a passing Test Audio for the current build (a missing device fails
with the normal error dialog), and that the end of the call also saves recordings started by
hand.

**Why the old signals missed the call.** Brave history of that morning shows the call tab at
`https://teams.cloud.microsoft/` with the title `Calendar | <meeting subject> | Microsoft
Teams`. The join link went through `/l/meetup-join/...`, but once joined Teams v2 shows the call
on its root URL. Browser Agent's Teams rule needs `/meetup-join`, `/meet` or `/call` in the URL,
so it reported nothing, and a Teams tab title is the same with and without a call. The
microphone record of `brave.exe` ran from 08:55:20 to 09:59:55, exactly the call.

## Behavior

- **Who holds the microphone** (`meetings/microphone_use.py`). Windows keeps
  `HKCU\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone`,
  the data behind the taskbar microphone icon. Desktop programs are under `NonPackaged`, keyed by
  their path with `#` for `\`; Store apps by package family name. `LastUsedTimeStop` is 0 while
  a program holds the microphone. Only lowercase program names leave the module.
  `classify` sorts them into meeting apps (`ms-teams.exe`, `teams.exe`, `zoom.exe`, `webex.exe`,
  `ciscocollabhost.exe`, `slack.exe`, package `MSTeams_*`) and browsers (`brave.exe`,
  `chrome.exe`, `msedge.exe`, `firefox.exe`, `opera.exe`, `vivaldi.exe`, `arc.exe`).
- **Is a call running** (`meetings/call_watch.py`, `CallWatch.observe`). A meeting app on the
  microphone is a call. A browser on the microphone is a call when a meeting is shown as it
  begins: a Meet or Teams browser window (`windows_screen.meeting_title`, also minimized), or a
  Browser Agent candidate with meeting detection on. After that the browser's hold alone keeps
  the call until the evidence has been gone for 30 seconds (`GRACE_SECONDS`), so switching tabs
  does not end it. Without microphone records (macOS) the meeting window is the evidence, and
  then nothing starts or stops by itself.
- **Start.** With `automatic_recording` on, `MainWindow._call_timer` runs every 5 seconds also
  between recordings. `_watch_call` starts a recording through `start_recording`, the manual
  path, when `CallWatch.should_start` allows it: one start per call. A recording that ran during
  the call counts as that start, so a manual stop is not undone. When dictation or another
  operation holds the recorder, the next look tries again.
- **Stop.** `CallEndWatch` reports the call gone for 30 seconds. With `auto_stop_recording` on
  and microphone evidence, the recording stops and saves; otherwise the call end prompt asks
  ([call end prompt](call-end-prompt.md)).
- **Log.** `meetings/call_log.py` appends one line per change to `call-detection.log` in the
  settings folder (`%LOCALAPPDATA%\AudioRecorder` on Windows): call yes/no, the programs on the
  microphone, meeting window yes/no, while recording the output devices the call plays on
  (`call-audio=`, see [system audio follows the call](output-following.md)), and the action.
  No titles. It is cut to its newer half past 256 KB. A missed call is explained from this
  file.
- **Settings.** On Windows, Settings → General shows **Record calls automatically, without
  asking** (`automatic_recording`) and **Stop and save the recording when the call ends**
  (`auto_stop_recording`). They do not need meeting detection. On macOS the same keys keep
  their native-lifecycle meaning with countdowns.

## Constraints and trade-offs

- **Not verified on a real call yet:** whether Teams web and Meet keep the microphone while
  you are muted, and while joined with the microphone off. If one releases it, a long mute
  splits the call into two recordings. `call-detection.log` of the next call shows it.
- A browser that holds the microphone for something else while a Meet or Teams window is open
  starts a recording. A manual stop then holds until the browser releases the microphone.
- A call joined with no microphone at all (listen-only) is not seen.
- Desktop apps other than the listed ones (Discord, Telegram) never count as calls.
- The recorder's own capture and dictation are `MeetingRecorder.exe`, never in the lists.
- The automatic start is the manual path, so its capture has no device verification against
  the last Test Audio. A missing device fails with the error dialog once per call.

## Verification

`tests/test_call_watch.py` covers the registry reading with a fake `winreg`, the call rules
(meeting app, browser with and without a meeting window, short release, grace), one start per
call, the log, and the window: start on a call, save without asking at its end, a manual stop
not undone, a manual recording saved at the call end, the prompt with automatic stop off, a
retry after dictation, and no start without microphone records. The Windows settings rows are
in `tests/test_settings_meeting_detection.py`. A live check with a WASAPI microphone stream
showed `python.exe` in the records while the stream ran and gone after it closed.
