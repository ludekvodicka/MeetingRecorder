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
- **The Cleanup button spawns the local `claude` executable** with tools and MCP servers disabled.
  The transcript is passed as untrusted input, and the application writes the returned summary.
  Anyone able to plant an executable named `claude` earlier on the user's PATH can still have it
  run. That is the same trust model as typing `claude` in a terminal.
- **The summary prompt is a setting**, so a user who edits their own settings file changes what
  the local Claude CLI is asked to do. It is not reachable by anything outside the machine.
- **Updates** check GitHub Releases without credentials in packaged builds. Self-built and fork
  packages only notify; the release workflow marks official packages at build time. Official
  Windows and Linux AppImage packages install updates themselves, while macOS and unpacked Linux
  builds notify only. Source runs keep checks off. SHA-256 checksums detect corrupt downloads;
  they do not authenticate a release when supplied by that same release.
- **Meeting detection is opt-in.** Browser detection queries Browser Agent's enabled meeting
  tools every 15 seconds through Windows named pipes or macOS Unix sockets. It opens no network
  listener. Browser Agent owns site permissions and its Agent access switch. The channel trusts
  processes of the same OS user; a local process impersonating a profile can offer a meeting
  prompt. Browser candidates never start or stop recording automatically. Responses are bounded,
  version-checked and treated as data; no meeting page content or audio is requested.

## Builds are unsigned

Windows and Linux releases carry no code signature. macOS packages are ad-hoc signed for local
integrity, without Developer ID or notarization, unless `MACOS_CODESIGN_IDENTITY` is set at build
time. Verify the checksum of a package before installing it; ad-hoc signing does not establish a
trusted publisher.
