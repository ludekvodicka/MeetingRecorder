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
- [ ] The same again with the Audio Recorder window focused, and with the Dictation button
      the last thing clicked. The spacebar activates a focused button, so this is where
      dictation used to switch itself off the moment it started
- [ ] The clipboard content you had before the dictation is back afterwards
- [ ] Ctrl+Shift+Space dictates and translates instead
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

### macOS

- [ ] The unsigned `.dmg` opens after `xattr -cr` on the app, and the app starts
- [ ] macOS asks for microphone permission on the first recording
- [ ] Recording with no system source configured records the microphone only, and says so
- [ ] With BlackHole installed and a multi-output device set up, selecting BlackHole as the
      system source captures the system sound
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
