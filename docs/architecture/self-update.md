# Self-update

## Decision

Releases up to 0.2.0 only told the user that a newer release existed; installing it was left to
the user. From 0.3.0 the Windows executable and the Linux AppImage download, verify and install
new releases themselves, the way the maintainer's other desktop applications do. macOS and the
Linux `.tar.gz` keep the notice, because an unsigned macOS application cannot replace itself
without Gatekeeper getting in the way, and an unpacked folder has no single file to replace.

The update logic lives in `audiorecorder/shared/desktop/autoupdate`, a package without Qt that
another desktop application uses as well. This application adds only its Qt layer
(`audiorecorder/updates.py`, `audiorecorder/ui/update_indicator.py`) and its facts: the
repository, the release file names and where a packaged build lives.

## Behavior

| Build | Mode | What happens |
|---|---|---|
| Windows `.exe`, also run from the portable `.zip` | automatic | download, verify, install on restart or quit |
| Linux AppImage | automatic | the same |
| Folder of the program not writable | notify | state and link to the release page |
| macOS `.app`, Linux `.tar.gz` | notify | state and link to the release page |
| Run from source | off | no network access, no files |

- **Timing:** first check 45 s after start, then two hours after each completed check. A failed
  check in the background changes nothing on screen; a failed **Check now** shows the error.
- **Integrity:** the release file is downloaded to a `.part` file while it is hashed, and kept
  only when its SHA-256 equals the line in `SHA256SUMS.txt` of the same release. Downloads come
  only from this repository's release download URLs on `github.com`.
- **Release notes:** the release text up to the marker `<!-- update-notes-end -->`, shown as plain
  text. The release workflow writes the tag summary above the marker and the checksums below it.
- **Install:** the verified new build is started with `--apply-update <pending.json>`. The entry
  point (`audiorecorder/__main__.py`) recognizes the flag before it imports Qt, so in this mode
  the new build only waits for the old process (on Windows also its PyInstaller bootloader
  parent), replaces the file and starts it again. The file is replaced where it is and keeps its
  name. The new build is first copied next to it as `<name>.new`; on Windows the old file is then
  renamed to `<name>.old` and `<name>.new` takes its name, with a rollback if the second rename
  fails; on Linux `<name>.new` replaces the file atomically.
- **When:** on **Restart and install**, or on the next normal quit without restart. Both are
  refused while a recording runs or is being saved, or while a transcription or cleanup runs
  (`MainWindow.busy_reason()`). Dictation does not block: it holds no unsaved work and some users
  keep it on all day. Nothing installs while the operating system
  session ends (Qt `commitDataRequest`), because the helper would race the logoff.
- **Result:** the helper writes `result.json` before it starts the program again, so the next
  start of either the new or the restored old build reads it. A failure shows as
  "Update failed" with the message in the status bar button and its dialog.

## Files

| Location | Content |
|---|---|
| user cache `AudioRecorder/updates/` (Windows `%LOCALAPPDATA%\AudioRecorder\Cache\updates`, Linux `~/.cache/AudioRecorder/updates`) | `<version>/<release file>`, `pending.json`, `result.json`, `helper.log` |
| folder of the program | `<name>.new` and `<name>.old` during a swap only |

The next start removes downloads that are not newer than the running version and any `.old` or
`.new` leftovers. If a swap is cut off between its two renames, renaming `<name>.old` back to
`<name>` restores the previous program.

## Entry points

- `audiorecorder/__main__.py`: helper dispatch before any Qt import.
- `audiorecorder/app.py`: composition, the status bar button, `aboutToQuit` and
  `commitDataRequest`.
- `audiorecorder/updates.py`: `QtUpdateHost` (timers, worker threads, delivery on the GUI thread)
  and `AppUpdates` (facts, busy guard, quit hook).
- `audiorecorder/ui/update_indicator.py`: the status bar button and the Update dialog.
- `.github/workflows/release.yml`: every Windows and Linux release build runs
  `python -m audiorecorder.shared.desktop.autoupdate.verify file <built program>`, which makes the
  real artifact swap a scratch copy in helper mode before the release is drafted.

## Constraints and trade-offs

- `pending.json` and `result.json` (schema 1) are the only data an old and a new build share;
  every later build keeps reading schema 1.
- The builds are unsigned. The checksum detects a corrupted or truncated download, not a
  compromised release.
- The first release with this updater (0.3.0) is installed by hand; only the release after it
  proves the update path on a real installation.
- No setting turns update checks off, there is no snooze and no automatic restart.
