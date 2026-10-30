# UI design system

The main window, its floating windows and Settings share one set of design tokens, one Qt
style sheet and a few themed widgets. The look follows a Claude Design proposal; this page
records how it maps onto Qt.

## Decision

- **Tokens, not colours in widgets.** `audiorecorder/ui/theme.py` holds a dark and a light
  token set (`bg.*`, `border.*`, `text.*`, `accent*`, `rec*`, `live*`, status, speaker and
  meter colours). The QSS template there uses `@token` placeholders that `apply_tokens()`
  replaces, longest key first. Widgets set an `objectName` or a dynamic property
  (`variant`, `size`, `toggle`, `state`, `chip`, `playing`) and leave the colours to the
  style sheet. After a property change, `widgets.set_prop()` re-polishes the widget.
- **Three colours carry meaning.** Red (`rec*`) is only for recording. Violet (`live*`) is
  only for paid Soniox realtime functions: the Dictation, Translate and Speakers toggles, the
  cost strip and the dictation card. Blue (`accent*`) is for actions, focus and progress.
- **The theme follows the operating system.** `theme.apply(app)` reads
  `styleHints().colorScheme()` and follows `colorSchemeChanged`. A scheme Qt cannot read
  stays dark. Painted widgets and tinted icons read `theme.color()` / `theme.solid()` and
  repaint on `theme.notifier.changed`.
- **Bundled fonts.** Inter (UI) and JetBrains Mono (timer, durations, times, version) load
  from `audiorecorder/assets/fonts/` with their SIL OFL texts. Without them the style sheet
  falls back to the system font.
- **Lucide icons as path data.** `audiorecorder/ui/icons.py` keeps the SVG paths with the ISC
  notice and renders them tinted at twice the device pixel ratio. QSS needs a few icons as
  files (check box tick, combo and spin arrows), so `theme.stylesheet()` writes them to the
  temp folder for each theme.

## Main window

| Part | Widget | Behaviour |
|---|---|---|
| Header | `QFrame#header` in `main_window.py` | Mark, title, three live toggles (`toggle="live"`, checked = violet), Add and Settings as icon buttons |
| Cost strip | `CostStrip` | Hidden when nothing paid is on. "Armed for next recording" while a toggle is on and nothing records. "Live, billing now" with the session count while engines run, its icon pulsing. "Dictation on" during dictation |
| State card | `state_card.StateCard` | States `idle`, `starting`, `recording` (with `muted`), `stopping`, `failed`, `dictation`. Owns the record, mute and Details buttons, the timer and the meters |
| Live panel | `subtitles.SubtitleView` | Shown while Translate or Speakers is on and a recording runs, or while it still holds lines. Shares a `GripSplitter` with the library; the library starts at 196 px, 118 px below a 700 px window |
| Library | `recording_list.RecordingListWidget` | One `RecordingItemWidget` per recording in a scroll area. The empty state sits in the same scroll area |
| Status bar | `QStatusBar` | Messages, the Browser Agent dot and summary, the update indicator |

## Library row

- **Status chip** from the row state, first match wins: failed → Failed (with See details),
  queued → Queued, busy cleanup → Cleaning up, busy transcription → the worker's phase with a
  spinning icon, transcribed → Transcribed, saved Soniox job → Interrupted, other checkpoint
  text → Needs review, else Not transcribed.
- **One primary action:** Retry after a failure (it repeats the failed kind), Open when
  transcribed, Resume for a saved job, else Transcribe. A busy row has none, and its ⋯ menu is
  disabled with a tooltip.
- **⋯ menu:** Clean up with AI (only when transcribed), Move to folder, Delete.
- **Output badges** for `.md`, `.summary.md`, `.live.md` and `.speakers.md` come from
  `Recording.outputs`. A click opens the file.
- **Playback:** the one `AudioPlayerWidget` moves into the row that plays and back out when
  playback stops or the list reloads. There is no separate player bar.
- A failed job shows its error dialog once; the row keeps it for See details until the next
  job on that recording starts.

## Constraints

- **QSS has no shadows, blur, transitions or CSS variables.** Motion comes from Qt:
  `widgets.Pulse` (REC dot and cost icon), `widgets.fade()` (just-saved row), `BusyBar`,
  `ProgressDots` and the chip spinner. All of them are short and stop when hidden.
- **The icon-to-text gap on buttons comes from leading spaces** (`widgets.spaced()`), because
  Qt draws that gap at a fixed width that style sheets cannot change.
- **A slot that raises ends the PyQt6 process.** Signals that call into the window are
  connected only after every widget they touch exists.
- **Light theme values** are in `TOKENS["light"]`. A new token needs a value in both sets.

## Entry points

- `audiorecorder/app.py`: `theme.apply(app)` after the Fusion style is set.
- `audiorecorder/ui/theme.py`: tokens, template, palette, fonts.
- `audiorecorder/ui/widgets.py`: chips, dots, pulse, busy bar, countdown ring, elided label.
- Tests: `tests/test_rts_toggle.py` (cost strip), `tests/test_processing_queue.py` (row
  states), `tests/test_meeting_prompt.py`, `tests/test_updates.py` (indicator tones).
