# Meeting Recorder

## Headless transcription

Run from a source checkout with dependencies installed:

```sh
python -m audiorecorder.transcription.cli "/path/to/meeting.m4a" --language auto --json
```

Installing the package also provides `meeting-transcribe` with the same arguments.
This uploads the specified recording to Soniox using the existing OS keyring credential
or `SONIOX_API_KEY` (which takes precedence). No GUI, recording device, or prompt is used.
Credential lookup runs in an isolated subprocess with a ten-second timeout. On macOS,
Keychain interaction is disabled for this lookup. If access requires approval or unlock,
the command exits with an error and releases its lock before any upload. Unlock/approve
the existing credential interactively, or provide `SONIOX_API_KEY` through the job's
secret environment; never put the key in a command argument, repository, or transcript.
`--language` overrides the app setting; `auto` enables language identification without a hint.
The command produces the original-language transcript, without requesting translation.
To resume a GUI job, keep the same language and translation settings (CLI translation is off).
`--dry-run` validates the input and destination without reading credentials or uploading.
`--help` and `--version` are available.

The sibling `.md` is published atomically without overwriting an existing file. Existing
nonempty transcripts are skipped; empty output files require manual review before retry.
GUI and CLI jobs share an OS lock in `.transcription.lock`; the small file remains on disk,
but the OS releases ownership even after a crash. Do not remove it to bypass an active job.
Legacy `.md.transcribing` files still block processing: verify the old CLI job has stopped
before removing one.

A sibling `.transcription.json` saves remote file/job IDs and settings, without credentials
or transcript content. Click **Resume**, or repeat the CLI command with matching settings,
to retrieve an existing job after a crash or network failure. Jobs are resumed on explicit
action, never uploaded automatically at startup. If a submission response was lost before
the job ID was saved, the app stops and asks you to check Soniox rather than submit another
paid job. Older app versions did not save IDs, so their interrupted jobs cannot be recovered
from local state. Retrying those recordings may incur another charge.

The GUI queues Transcribe and Cleanup actions and shows progress on each recording.
Duplicate actions and move/delete are disabled for queued and active recordings. Quit
waits for the queue and its threads to finish. Checkpoint sidecars follow audio when moved,
renamed or deleted; lock files remain at their original paths.

Recording moves, renames and deletes share this lock and preserve sidecars.
See [batch transcription architecture](docs/architecture/batch-transcription.md) for ownership
and recovery contracts.

Exit codes: `0` completed/skipped/dry-run, `1` operational failure, `2` invalid input,
`130` interrupted. With `--json`, the final result contains `status`, `audio`, `transcript`
and an optional `message`. Success results go to stdout, errors and progress to stderr.
Provider error bodies are not printed because they may contain sensitive data.

[![CI](https://github.com/ludekvodicka/MeetingRecorder/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/ludekvodicka/MeetingRecorder/actions/workflows/ci.yml)
[![Release build](https://github.com/ludekvodicka/MeetingRecorder/actions/workflows/release.yml/badge.svg)](https://github.com/ludekvodicka/MeetingRecorder/actions/workflows/release.yml)
[![Latest release](https://img.shields.io/github/v/release/ludekvodicka/MeetingRecorder)](https://github.com/ludekvodicka/MeetingRecorder/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/ludekvodicka/MeetingRecorder/total)](https://github.com/ludekvodicka/MeetingRecorder/releases)
[![License](https://img.shields.io/github/license/ludekvodicka/MeetingRecorder)](LICENSE)

A desktop application that records a call from both sides at once: what comes out of your
speakers and what goes into your microphone, mixed into one file. Afterwards it can send the
recording to [Soniox](https://soniox.com) for a transcript with speaker labels and
timestamps, and hand that transcript to the Claude CLI for a summary.

It also does push-to-talk dictation. Hold Ctrl+Space anywhere, speak, let go, and the text
appears where your caret is.

Written for Google Meet and Teams calls in Czech and English. It captures system audio
natively on Windows and modern macOS; Linux and older macOS releases have the routing
caveats in the support table below.

![The main window with a few recordings](docs/screenshot.png)

## What it does

**Records both sides of a call.** The system output and the microphone are captured as two
separate tracks and mixed when you stop, each with its own volume. You can mute your
microphone mid-recording without stopping. Level meters show both tracks while recording, so
you can tell before the meeting whether the right devices are selected.

**Transcribes.** One click sends a recording to Soniox, which identifies the language, splits
the text by speaker and timestamps it. The result lands next to the recording as a `.md` file.
If translation is on and the call turns out to be in a different language than the one you
configured, you get both: the translation first, the original underneath.

**Summarizes.** If you have the [Claude CLI](https://claude.com/claude-code) installed, the
Cleanup button asks it to read the transcript and write a summary beside it. The prompt is a
setting, so you can ask for whatever shape of summary you want. Without the CLI installed the
button reports that plainly and nothing else breaks.

**Dictates.** Hold Ctrl+Space and speak: the text is transcribed live through Soniox and
pasted where you are typing. Ctrl+Shift+Space translates as it goes. A small pill at the
bottom of the screen shows that it is listening, and your clipboard is put back afterwards.

**Subtitles a call as it happens.** Switch on **RTS Translate** and, while a recording runs,
what the other side says appears under the level meters, newest at the top: the original
above, the translation into your configured language below. A sentence already spoken in
that language is shown once, on its own. The recording is untouched, so the transcript and
the summary still happen afterwards as they always did, and what the subtitles showed is
kept beside the recording as a `.live.md` file.

It is off by default, and it should be: it streams the whole call to Soniox in real time,
and the recording is transcribed again afterwards for the summary. You are billed for both.
The button is green whenever a live session is running, and switching it off ends that
session immediately, mid-recording included.

**Labels speakers live.** Switch on **Live Speakers** to send the microphone and system
audio through separate realtime streams. Microphone speech is labelled **Me**; the remote
track is diarized as **Remote 1**, **Remote 2**, and so on. As labels appear, **Name
Speakers** maps them to participant names. The final mapping applies to the complete call and
is saved as both `.speakers.md` and structured `.speakers.json` after the M4A safely encodes.
Live labels are immediate aids; the later batch transcription remains more accurate. This
feature is off by default and adds up to two paid Soniox realtime streams.

**Offers recording when a meeting appears.** On macOS, stable Teams, Zoom, Webex, and Slack
Huddle windows produce a non-modal recording prompt. An optional least-privilege Brave/Chrome/Edge
companion covers Google Meet, Zoom Web, and Teams Web. It can see only those meeting pages,
has no history/tab-capture permission, and reports to `127.0.0.1` only. macOS Calendar is a
separate opt-in signal for imminent events with attendees or a recognized conference link.
Modern Teams is also recognized from repeated changes to its local media-stack file metadata;
the detector does not read call logs, messages, or meeting content. Settings shows the latest
native-window and Teams-media diagnostic state. If macOS redacts Teams window titles, the
detector can still recognize the call-control surface from its non-content window geometry.

On macOS, closing the main window keeps Meeting Recorder in the menu bar so detection remains
available. Click the menu-bar icon to start or stop recording, or open the app. Quit the app
from its Dock menu. **Launch at login** is a
separate opt-in in Settings, managed by macOS Service Management and visible in the system's
Login Items controls.

The installed macOS app uses native notification actions, with an in-app fallback when
notifications are unavailable. Settings reports notification authorization, capture test
results, selected/resolved devices and observed login-item status. Focus can suppress banners.

Start and stop countdowns default independently to **5 seconds** (configurable from 5–120).
Automatic recording and automatic stopping are separate opt-ins. The macOS Teams/Zoom
call-control detector uses Accessibility with explicit inspection opt-in. No app version is
approved by default: use the temporary supervised test, then approve only after completing
[the real-call checklist](docs/native-call-test-checklist.md). Approval expires when the app
version or matcher changes. Unattended capture also requires a successful audio test for this
build and device configuration. Window/media, Calendar and browser signals remain advisory.
Unknown or hidden controls preserve recording; optional window-absence stopping has a
separate test/approval. See [native lifecycle notes](docs/native-lifecycle.md) for boundaries.

Recording startup and shutdown run outside the GUI thread. Saving uses a separate bounded
encoder queue and atomically publishes validated audio. Empty capture is reported as an
error; failed encodes retain source WAVs for recovery.

![Live subtitles during a recording](docs/screenshot-subtitles.png)

**Keeps the library on disk.** There is no database. An `.m4a` in the output directory is a
recording, a `.md` next to it means it has been transcribed. Rename, move or delete from the
list and the transcript, the summary and the subtitles follow along.

![Settings](docs/screenshot-settings.png)

## What works where

The maintainer runs this on Windows. macOS and Linux are built by CI and exercised by the
automated tests, but nobody is using them daily, so treat them as community-supported and
please report what you find.

| | Windows | macOS | Linux |
| --- | --- | --- | --- |
| Microphone recording | yes | yes, asks for permission | yes |
| System audio | yes, straight from the default output | native on macOS 13+; BlackHole fallback | needs a `.monitor` source, see below |
| Transcription and summaries | yes | yes | yes |
| Live subtitles (RTS Translate) | yes | yes on macOS 13+ | only with a monitor source |
| Live speaker labels | yes | yes on macOS 13+ | only with a monitor source |
| Native meeting popup | no | Teams, Zoom, Webex, Slack Huddles | no |
| Browser meeting popup | optional Brave/Chrome/Edge companion | optional Brave/Chrome/Edge companion | optional Brave/Chrome/Edge companion |
| Calendar meeting signal | no | opt-in through EventKit | no |
| Dictation hotkey | yes | yes, needs Accessibility permission | X11 only, not on Wayland |
| Paste after dictation | yes | yes, sends Cmd+V | X11, and needs `xclip` or `xsel` |
| Tested by the maintainer | yes | no | no |

**System audio.** Windows records its default output through WASAPI loopback. On macOS 13 or
newer, leaving the system source on **Default** uses ScreenCaptureKit and needs no virtual
audio device. The first recording asks for Screen & System Audio Recording permission; grant
it in System Settings > Privacy & Security and restart Meeting Recorder when macOS requests
that.

The explicit routed-input choices remain available:

- **Linux** - PulseAudio and pipewire-pulse create a `.monitor` source for every output. Pick
  the monitor of the output you are listening on. If it does not appear in the list, route the
  application to it with `pavucontrol` instead.
- **macOS 12 or an explicit compatibility setup** - install a virtual audio device such as
  [BlackHole](https://github.com/ExistentialAudio/BlackHole), create a multi-output device in
  Audio MIDI Setup that feeds both your speakers and BlackHole, then pick BlackHole here.

On Linux and macOS 12, without a routed system source the recording is microphone-only. The
application says so in the status bar rather than pretending it captured the call.

**Wayland.** No application can watch the keyboard globally on Wayland, so dictation is turned
off there and the button says why. Recording and transcription are unaffected.

## Install

Download the build for your platform from
[Releases](https://github.com/ludekvodicka/MeetingRecorder/releases).

Unless a release explicitly says it is notarized, its builds are unsigned. Check the SHA256
of your download against `SHA256SUMS.txt` in the same release before running it:

```sh
sha256sum Meeting-Recorder-0.3.3-x64.exe                    # Linux or Git Bash
shasum -a 256 Meeting-Recorder-0.3.3-arm64.dmg              # macOS
certutil -hashfile Meeting-Recorder-0.3.3-x64.exe SHA256    # Windows Command Prompt
```

- **Windows** - a single `.exe`, or the same executable in a `.zip`. SmartScreen will warn
  about an unknown publisher.
- **macOS** - a `.dmg`, **Apple Silicon only**, because that is what the build runners are.
  On an Intel Mac, build from source. Gatekeeper refuses unsigned applications, so run
  `xattr -cr "/Applications/Meeting Recorder.app"` after copying it across. The DMG also
  contains the optional unpacked browser companion and a next-meeting quick start. The app
  keeps its own permanent companion copy: open **Settings > Reveal Companion Folder** before
  loading it in Brave, Chrome, or Edge.
- **Linux** - an `.AppImage`, or a `.tar.gz` if you prefer to unpack it yourself. Needs
  PulseAudio or pipewire-pulse, and `xclip` or `xsel` for dictation.

## Updates

Packaged builds check GitHub Releases 45 seconds after start and then every two hours; the
version indicator shows the result and the release notes. Source runs do not check.

The release workflow marks the packages it publishes as official. Official Windows and Linux
AppImage packages download, verify and install a new release themselves; macOS and the
unpacked Linux package only notify. A package you build yourself, or one built in a fork, is
not official and only notifies, so an official release never silently replaces your own
changes. Set `MEETING_RECORDER_BUILD_CHANNEL=official` at build time only when official
releases should replace that build.


## Set it up

**Soniox API key.** Transcription and dictation both go through Soniox, which is a paid
service. Create an account, generate a key, and paste it into Settings. It is stored in the
operating system keyring, not in a configuration file. For development or on a machine with no
keyring you can set `SONIOX_API_KEY` in the environment instead, which takes precedence.

**Claude CLI, optional.** Only needed for the Cleanup button. Install
[Claude Code](https://claude.com/claude-code). Shell launches use your PATH; on macOS, Finder
launches also look in the Homebrew and standard Claude/per-user installation locations.

**Audio devices.** Settings lists what your system offers. Leaving both on Default is right for
most Windows machines and for macOS 13 or newer. Devices are remembered by name, so unplugging
and replugging a headset does not silently switch you to a different microphone.

**macOS permissions.** The first recording asks for Microphone and Screen & System Audio
Recording access. After granting screen/audio capture, restart the app. Dictation separately
needs Accessibility access so the global hotkey and Cmd+V can reach other applications.
Calendar access is requested only after its Settings checkbox is enabled; EventKit exposes no
read-only tier, but Meeting Recorder only reads a narrow upcoming-event window and never edits
events.

For a complete install-to-first-call walkthrough, use
[the next-meeting quick start](docs/next-meeting-quick-start.md).

## Your data stays on your machine

Recordings, transcripts and summaries are files in the output directory you choose, and nothing
scans, syncs or uploads them on its own.

Audio leaves your computer only when you ask for it: the Transcribe button uploads that one
recording to Soniox, dictation streams your microphone to Soniox while you hold the key, and
RTS Translate streams the other side of the call to Soniox for as long as it is switched on
and a recording is running. Live Speakers streams the microphone and system tracks to separate
Soniox realtime sessions while its toggle and a recording are active. Meeting detection stays
local: native window titles, optional Calendar events, and sanitized browser-companion
heartbeats are not uploaded.
The Cleanup button runs the Claude CLI on your machine, which sends the transcript to Anthropic
under whatever agreement your Claude account has. Read the privacy terms of both services and
decide whether the calls you record belong there. There is no telemetry and no account of our
own.

The Soniox key is held by the operating system keyring: Credential Manager on Windows, Keychain
on macOS, Secret Service on Linux. Settings live in your user configuration directory
(`%LOCALAPPDATA%\AudioRecorder` on Windows, `~/Library/Application Support/AudioRecorder` on
macOS, `~/.config/AudioRecorder` on Linux), never next to the executable. That directory and
the keyring entry still carry the application's original name, so an upgrade finds the
settings and the key that are already there.

Recording a call may need everyone's consent where you live. That is on you, not on the tool.

## Build from source

Python 3.12 or newer.

```sh
pip install -e ".[dev]"
python -m audiorecorder
```

Checks and packaging:

```sh
ruff check .
pytest
python scripts/build.py package   # writes installers into release/
```

On macOS, PyInstaller ad-hoc signs local builds and `package` smoke-tests the frozen runtime
before creating the DMG. The packaging command removes the unpackaged app from `dist/` after
the DMG succeeds so macOS cannot register two app bundles with the same identifier and attach
Screen Recording permission to the wrong copy. Run `build` instead when you explicitly need
the unpackaged app. For a distributable Developer ID build, first store a `notarytool` profile
in Keychain, then run:

```sh
MACOS_CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
MACOS_NOTARY_PROFILE="meeting-recorder-notary" \
python scripts/build.py package
```

The command signs with hardened runtime, submits the DMG to Apple, staples the ticket, and
verifies both the ticket and Gatekeeper assessment. Credentials stay in Keychain; the profile
name is the only notarization value passed to the build.

Linux also needs the Qt and PortAudio system libraries; the list CI installs is in
`.github/workflows/ci.yml`. The AppImage step needs `appimagetool` on PATH.

The tests cover the parts that need no hardware: the transcript format, mixing maths, device
selection, native PCM conversion, settings, recording scanner and push-to-talk logic. macOS
CI also builds and starts the frozen runtime in smoke-test mode. Real capture still needs a
machine, and `docs/manual-test-checklist.md` lists what to try before a release.

## Contributing

Bug reports and focused pull requests are welcome, see [CONTRIBUTING.md](CONTRIBUTING.md). For
security issues follow [SECURITY.md](SECURITY.md) rather than opening a public issue.

## Licence

GPL-3.0-only, see [LICENSE](LICENSE). The application links PyQt6, which is itself GPL-3.0, so
the builds are GPL v3 and so is the source. Everything else that ships inside a build is listed
in [THIRD-PARTY.md](THIRD-PARTY.md).
