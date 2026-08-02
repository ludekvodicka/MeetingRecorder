# Security policy

## Reporting a vulnerability

Report privately through
[GitHub security advisories](https://github.com/ludekvodicka/AudioRecorder/security/advisories/new)
rather than opening a public issue. Expect an acknowledgement within a week.

This is a spare-time project with one maintainer, so there is no bounty and no guaranteed fix
window. What you will get is an honest answer about whether and when it will be addressed.

## Supported versions

Only the latest release. There are no backports.

## What this application handles

Worth knowing when judging the impact of a finding:

- **The Soniox API key** is held by the operating system keyring, or read from the
  `SONIOX_API_KEY` environment variable when one is set. It is never written to the settings
  file, never bundled into a build, and never logged.
- **Recordings and transcripts** are plain files in the output directory the user chooses.
  They are not encrypted, and anyone who can read that directory can read them.
- **Audio leaves the machine only on an explicit action**: the Transcribe button uploads one
  recording to Soniox, and dictation streams the microphone to Soniox while the hotkey is held.
- **The Cleanup button spawns the local `claude` executable** with the transcript directory as
  its working directory, and runs it with permission prompts disabled so it can read and write
  there unattended. Anyone able to plant an executable named `claude` earlier on the user's PATH
  can therefore have it run. That is the same trust model as typing `claude` in a terminal.
- **The summary prompt is a setting**, so a user who edits their own settings file changes what
  the local Claude CLI is asked to do. It is not reachable by anything outside the machine.
- **The update check** performs one unauthenticated GET against the GitHub Releases API at
  startup and only ever displays a version string. It never downloads or executes anything.

## Builds are unsigned

Releases carry no code signature on any platform. Verify the SHA256 published with each release
before running a download. Signing is a known gap, not an oversight.
