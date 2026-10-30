# Manual test checklist

The automated suite covers the pure logic: the transcript format, the mixing and resampling
maths, the settings merge, the recording scanner and the push-to-talk state machine. It cannot
cover the parts that need real hardware, a real desktop session or a paid API, and a CI runner
has none of those. Those parts are listed here, and someone has to run them by hand before a
release.

## What CI can never test, and why

| Area | Why it cannot run in CI |
| --- | --- |
| System audio capture | No sound card, no WASAPI loopback, nothing playing |
| Microphone capture | No microphone on a runner |
| Global hotkeys | No desktop session, and no way to press a key |
| Synthetic paste | Needs a focused window to paste into |
| The overlay window | Needs a compositor, and its correctness is visual |
| Keyring storage | No credential store, and no user to unlock it |
| Soniox transcription and dictation | Paid API, real audio, real network |
| Claude summarization | Needs the Claude CLI installed and authenticated |

## Before a release

Run the Windows column at least. macOS and Linux are community-supported: run what you can,
and say in the release notes what was not exercised.

### Windows

- [ ] `python -m audiorecorder` starts, the title bar shows the version from `version.py`
- [ ] Settings lists the loopback devices and the microphones, both by name
- [ ] Record with the default system source: the system meter moves while something plays,
      the microphone meter moves while you speak
- [ ] Stop: an `.m4a` appears in the list, plays back, and contains both voices
- [ ] Mute Mic during a recording silences the microphone track but not the system track
- [ ] Set the system source to "None (microphone only)": the status bar says the recording
      is microphone only, and the result contains just your voice
- [ ] Pick a specific loopback device by name, restart the app, and confirm it is still selected
- [ ] Transcribe a short recording: a `.md` appears with speakers and timestamps
- [ ] With language `cs` and translation on, transcribe an English recording and confirm the
      Czech translation comes first and the English original follows
- [ ] Cleanup on a transcript writes a `.summary.md` (needs the Claude CLI on PATH)
- [ ] Without the Claude CLI on PATH, Cleanup fails with a readable message and no crash
- [ ] Dictation on, hold Ctrl+Space, speak, release: the text is pasted where the caret is
- [ ] The same again with the Meeting Recorder window focused, and with the Dictation button
      the last thing clicked. The spacebar activates a focused button, so this is where
      dictation used to switch itself off the moment it started
- [ ] The clipboard content you had before the dictation is back afterwards
- [ ] Ctrl+Shift+Space dictates and translates instead
- [ ] With Dictation on, type fast and move the mouse in another application for one
      minute: typing and mouse movement do not lag
- [ ] Switch Dictation off and on, then hold Ctrl+Space, speak and release: dictation
      starts again and the text is pasted where the caret is
- [ ] Dictate once with the language set to Czech, once to English and once to Auto-detect.
      Auto sends no language hint at all, and a wrong hint makes Soniox reject the stream
- [ ] Tap Ctrl+Space instead of holding it: the status bar says it was too short
- [ ] Dictate into silence: the status bar says nothing was recognized
- [ ] Run with `AUDIORECORDER_LOG=debug` when a dictation misbehaves. It logs the config
      sent to Soniox, every reply, the frames sent and dropped, and why a paste did not
      happen
- [ ] The overlay pill appears at the bottom of the screen, animates while recording, and
      does not swallow mouse clicks
- [ ] Switch RTS Translate on outside a recording: the status bar says it starts with the
      next one, and the subtitle area appears
- [ ] Record a call in English with the language set to Czech: the original appears under the
      level meters with the Czech under it, within a couple of seconds of the words
- [ ] Stop the recording: a `.live.md` sits beside the `.m4a` and matches what was on screen
- [ ] Record with RTS Translate on and say nothing: no `.live.md` is written
- [ ] Record with RTS Translate on and the language set to auto: subtitles appear untranslated
      and the heading says so
- [ ] With no system audio source configured, the subtitle area says it needs one instead of
      sitting empty
- [ ] Pull the network out mid-call: the status bar says it is reconnecting, then that it has
      stopped, and the recording carries on to a complete `.m4a` either way
- [ ] Transcribe and summarize a recording made with subtitles on: both behave as before
- [ ] Delete a recording that has a transcript, a summary and subtitles: all four files go
- [ ] Clear the API key from the keyring, restart, and confirm Transcribe explains what to do
      rather than failing obscurely
- [ ] Enter the key in Settings, confirm it is accepted and survives a restart
- [ ] Self-update: copy the previous release's `.exe` into a writable folder and start it. About
      45 seconds later the status bar shows the new version downloading, then ready
- [ ] The Update dialog shows the new release notes as text; View on GitHub opens the release page
- [ ] Restart and install (or quit normally): the same file name now holds the new version, its
      SHA-256 matches the line in the new `SHA256SUMS.txt`, the title shows the new version, and
      `updates/result.json` in the cache folder says installed
- [ ] Start a recording, then Restart and install: the dialog refuses and names the recording
- [ ] With an update ready and Dictation on, close the window: the update installs on that quit
- [ ] Close the window during a recording: it asks first; No keeps recording, Yes saves and exits
- [ ] Sign out of Windows during a recording, sign in and start the app: the status bar says it
      recovered an interrupted recording, and the new `.m4a` plays back
- [ ] Start the app in two Windows accounts at once (fast user switching or Remote Desktop):
      both run; a second start in the same account only brings its window forward
- [ ] Set the output directory to a FAT32 or exFAT USB stick: a recording and its transcript
      are saved there

### macOS

- [ ] The `.dmg` opens and the app starts; for an unsigned local build, confirm the documented
      `xattr -cr` fallback still works
- [ ] On macOS 13+, leave the system source on Default and start a recording: macOS asks for
      Screen & System Audio Recording and microphone permission
- [ ] Grant screen/audio capture, restart the app, and confirm the system meter moves while
      audio plays without BlackHole installed
- [ ] Stop: the resulting `.m4a` contains both system audio and the microphone
- [ ] Set the system source to "None (microphone only)": the status bar says so and the result
      contains no system audio
- [ ] With BlackHole installed and a multi-output device set up, selecting BlackHole as the
      explicit system source still captures system sound
- [ ] Record, stop, then record again in the same app process; native capture starts both times
- [ ] Switch RTS Translate on and confirm native system audio feeds live subtitles
- [ ] Switch Live Speakers on: microphone speech appears as Me and two remote voices remain
      distinct as Remote labels; Name Speakers updates future lines
- [ ] Stop after naming speakers: `.speakers.md` and `.speakers.json` appear only after the
      M4A succeeds, contain the final names for earlier lines too, and follow rename/delete
- [ ] Open a Teams or Zoom meeting window: one prompt appears after the stable detection;
      dismissing it does not produce another prompt until that meeting window disappears
- [ ] Join a modern Teams call whose window title is only the meeting subject: within about
      20 seconds Settings reports Teams media monitor active and one prompt appears; ordinary
      Teams browsing and chat do not trigger it
- [ ] Install Browser Agent and its native host in Brave/Chrome on Windows or macOS. Enable
      Agent access and Google Meet under Meetings. Settings shows the profile and enabled tool;
      open a Google Meet code URL and confirm one prompt on the next 15-second poll. Moving to a
      non-meeting page removes the signal. Other enabled profiles remain independently detectable
- [ ] Disable the meeting tool, Agent access, or close the profile: candidates disappear on the
      next poll and Settings explains the state. Turning detection off stops all browser polling
- [ ] Enable Calendar detection, grant Calendar access, and confirm an imminent attended or
      linked event prompts while a personal reminder and an all-day event do not
- [ ] Enable automatic recording for a qualified native call and cancel the visible countdown;
      repeat and let it finish. Browser Agent and Calendar signals never count down or start audio
- [ ] Begin a countdown and close the meeting before zero: no recording starts
- [ ] Click the menu-bar icon with the main window open, minimized and closed: the menu shows
      Start Recording, Stop Recording and Open Meeting Recorder without raising the window
- [ ] Start and stop recording from that menu: the enabled actions follow the recording state,
      rapid repeated clicks do not duplicate operations, and the saved audio plays back
- [ ] During an automatic start countdown, use Start Recording: capture starts once; Stop
      Recording also works during an automatic stop countdown
- [ ] While dictation is active or capture shutdown is unresolved, Start Recording is disabled;
      Open Meeting Recorder provides access to the controls and any capture recovery
- [ ] Close the macOS main window: it hides, the menu-bar icon remains, and a meeting still
      prompts; choose Quit from the Dock menu and confirm the process and Browser Agent polling stop
- [ ] Enable Launch at login, log out/in, and confirm the app launches and detects a meeting;
      disable it and confirm macOS no longer launches it
- [ ] Dictation asks for Accessibility permission, and works once it is granted
- [ ] Paste sends Cmd+V, not Ctrl+V
- [ ] Note whether Ctrl+Space collides with the input source switcher on this machine

### Linux

- [ ] The AppImage runs on a clean system with PulseAudio or pipewire-pulse
- [ ] Settings lists the `.monitor` sources among the inputs
- [ ] Selecting the monitor of the current output captures the system sound
- [ ] Recording with no system source configured records the microphone only, and says so
- [ ] On X11: dictation works, and paste needs `xclip` or `xsel` to be installed
- [ ] On Wayland: the Dictation button is disabled and its tooltip explains why, and nothing
      crashes
- [ ] Without any Secret Service provider, saving the API key fails with the message telling
      the user to use `SONIOX_API_KEY` instead

## After changing the capture layer

The two backends have to stay interchangeable, so run these on every platform you can reach:

- [ ] Device lists are not empty where devices exist
- [ ] A configured device that has been unplugged falls back to the default instead of
      refusing to record
- [ ] Both meters move independently
- [ ] The temporary `_rec_system_*.wav` and `_rec_mic_*.wav` files are gone after encoding
- [ ] A recording that is stopped immediately still produces a valid file

## Self-built package update policy

- [ ] In a package built locally with `scripts/build.py package`, a newer release shows notes
  and a release link, without a download or Restart and install action, on every platform.
- [ ] Check source-run updates stay off; real capture/transcription acceptance is separate
  from the mocked provider and updater tests.

The automatic updater scenarios above apply to official packages, which the release workflow
marks at build time (`docs/architecture/self-update.md`).
