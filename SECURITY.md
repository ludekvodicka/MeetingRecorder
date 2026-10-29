# Security policy

## Reporting a vulnerability

Report privately through
[GitHub security advisories](https://github.com/ludekvodicka/MeetingRecorder/security/advisories/new)
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
- **Update notices** ask the GitHub Releases API, without credentials, for this repository's
  latest release 45 seconds after start and every two hours in a packaged build. Source runs
  keep checks off. Release notes are shown as plain text. This custom branch never downloads
  or installs stock upstream binaries automatically on any platform: they omit our features.
  Updates are integrated through a reviewed merge, tested and packaged as a new custom build.
- **Shared updater code** retains upstream's download/checksum/helper implementation and tests,
  but the custom application selects notification-only mode. SHA-256 checksums detect corrupt
  downloads; they do not authenticate a release when supplied by that same release.

## Builds are unsigned

Official upstream releases are unsigned. Our custom macOS packages are ad-hoc signed for
local integrity, without Developer ID or notarization. Verify the checksum of a package before
installing it; ad-hoc signing does not establish a trusted publisher.
