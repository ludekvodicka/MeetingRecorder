# Push-to-talk through Raw Input

## Decision

Windows push-to-talk receives keyboard reports through Raw Input on a dedicated thread.
The previous pynput listener used a low-level keyboard hook, whose Python callback had to
acquire the interpreter lock before Windows could deliver the key to the foreground
application. Raw Input reports arrive independently of that delivery, so this listener
cannot hold up typing in another application.

The push-to-talk state machine and its public listener interface stay the same. pynput
remains a dependency for the macOS and Linux listeners and for synthetic paste on every
platform.

Windows defines the registration rules in
[RegisterRawInputDevices](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerrawinputdevices)
and foreground-message cleanup in
[WM_INPUT](https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-input).

## Behavior

- **Windows listener:** enabling Dictation creates a message-only window on the listener
  thread and registers the keyboard with `RIDEV_INPUTSINK`. It receives `WM_INPUT` while
  another application has focus and passes key presses and releases to the existing state
  machine. Normal keyboard input is not suppressed.
- **Key handling:** left and right Ctrl and Shift map to the same modifiers. Ctrl+Space
  starts dictation and Ctrl+Shift+Space starts translated dictation. Releasing Space or the
  held Ctrl ends it; releasing Shift alone does not.
- **Injected reports:** every keyboard report delivered by Raw Input is handled, including
  injected reports. There is no injected-event filter. The existing `GetAsyncKeyState`
  modifier probe is unchanged, so a synthetic Ctrl release from paste does not clear a Ctrl
  that the user is still holding.
- **Lifecycle:** startup waits for the thread to register and verify its window. A startup
  failure reaches the existing unsupported-hotkey message. Switching Dictation off stops
  the thread, removes its registration and destroys its window. Cleanup leaves a replacement
  registration owned by another component alone. An error after startup is logged, releases
  any active dictation through the existing state machine and resets its modifiers before
  the listener exits. Release-callback errors cannot prevent window and registration cleanup.
- **Other platforms:** macOS and Linux retain their pynput listeners. macOS still needs
  Accessibility permission; Linux needs X11, and Dictation remains unavailable on Wayland.

## Entry points

- `audiorecorder/dictation/hotkeys.py`: platform selection, raw-key normalization, the
  modifier probe and the push-to-talk state machine.
- `audiorecorder/dictation/raw_keyboard.py`: Windows window and registration, message loop,
  keyboard report decoding and thread cleanup. Messages are dispatched to the window's
  default procedure even when report handling fails, so Windows can release their resources.

## Limits

- **Interpreter load:** the listener runs in this process and still needs the Python
  interpreter lock. A busy interpreter can delay the start or end of a dictation. Windows
  does not wait for this thread before delivering keyboard input to another application.
- **Registration ownership:** Windows keeps one keyboard Raw Input target per process;
  the most recent registration wins. Startup verifies the target window and background-input
  flag. A component that replaces the registration later can stop the hotkey from receiving
  reports. Registration is checked when Dictation starts, not continuously.
- **Elevated windows:** hotkey behavior with an elevated foreground application requires a
  live Windows check. That check is pending; this change does not establish support for
  dictating into elevated applications.

## Verification

On Windows with Python 3.14, the complete offscreen test suite passed 498 tests with one
skip, compared with 462 passes and one skip before the change. The 36 added cases cover raw
key mapping, callback wiring, simulated macOS/Linux listener selection, absence of hook
installation, real Windows registration and repeated start/stop, registration failure and
replacement, startup cancellation, message cleanup when processing raises, and release of
active dictation after a runtime reader failure, including a failing release callback.

The real Windows lifecycle test leaves no listener thread or keyboard registration after
either stop. Project Ruff, the diagnostic script's Ruff check, the third-party inventory and
release-version checks passed. The changed public files passed the public-text and secret
scan. Native macOS/Linux execution remains for CI.

Live voice checks are pending: ordinary and translated dictation, clipboard restoration,
one minute of fast typing and mouse movement in another application with Dictation on,
dictation after switching it off and on, and an
elevated foreground window. These checks require a desktop session, microphone and configured
transcription service. See [the Windows checklist](../manual-test-checklist.md#windows).
