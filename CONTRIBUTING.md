# Contributing to Audio Recorder

Bug reports and focused pull requests are welcome. For security issues, follow
[SECURITY.md](SECURITY.md) instead of opening a public issue.

## Development setup

Python 3.12 or newer:

```sh
pip install -e ".[dev]"
python -m audiorecorder
```

Before submitting a change, run:

```sh
ruff check .
pytest
python scripts/generate_third_party.py --check
```

On Linux you also need the Qt and PortAudio system libraries; the exact list is in
`.github/workflows/ci.yml`.

## What the tests can and cannot cover

The suite covers the pure logic: the transcript format, the mixing and resampling maths, the
settings merge, the recording scanner and the push-to-talk state machine. Nothing else can run
on a CI machine, which has no sound card, no desktop session and no API key.

So a change that touches recording, dictation, the overlay or the keyring has to be exercised
by hand. `docs/manual-test-checklist.md` lists what to try, per platform. Say in the pull
request which platform you ran it on and what you actually did.

That is especially true for the two capture backends. They have to stay interchangeable, and
only one of them can be running on any given machine.

## Pull requests

- Keep each pull request focused, and explain the user-visible behaviour.
- Add tests for anything that can be tested without hardware. If a change is untestable as
  written, consider whether the logic can move into a pure function first, the way the
  transcript renderers and the push-to-talk state machine did.
- Never commit recordings, transcripts, API keys or local machine paths.
- Keep the platform differences behind `audiorecorder/audio/backend.py` and
  `audiorecorder/dictation/hotkeys.py`. The user interface must not branch on the platform.
- Where a platform genuinely cannot do something, say so in the interface and in the README
  support table. Do not let it look like it worked.

The maintainer's internal source of truth is SVN, and GitHub is a reviewed public projection.
After a pull request merges, the maintainer copies it into the internal tree before the next
public sync. Contributors need no SVN access and no private tooling.
