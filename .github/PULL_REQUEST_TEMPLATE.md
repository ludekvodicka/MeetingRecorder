## What this changes

<!-- The user-visible behaviour, and why. -->

## How it was tested

<!--
`ruff check .` and `pytest` are the automated half. Recording, dictation, the overlay and the
keyring cannot run in CI, so say what you exercised by hand and on which platform. See
docs/manual-test-checklist.md.
-->

- Platform:
- Ran by hand:

## Checklist

- [ ] `ruff check .` and `pytest` pass
- [ ] `python scripts/generate_third_party.py --check` passes, if dependencies changed
- [ ] Tests added for anything testable without hardware
- [ ] No recordings, transcripts, keys or local machine paths in the diff
- [ ] Platform differences stayed behind the capture backend and the hotkey layer
- [ ] The README support table still matches reality
