"""Global push-to-talk hotkey.

Ctrl+Space dictates, Ctrl+Shift+Space dictates and translates, and the recording lasts
exactly as long as the keys are held. That needs both the press and the release of a key
the application does not have focus for, which pynput provides on all three platforms with
two limits worth stating plainly:

* Wayland has no global hotkey mechanism at all, so dictation is turned off there rather
  than silently doing nothing. An X11 session works.
* macOS asks for Accessibility permission the first time, and refuses the listener until
  it is granted. It also uses Ctrl+Space for the input source switcher by default.

The decision of what a key event means lives in PushToTalkStateMachine, which imports
nothing and is unit-tested. The listener only translates pynput keys into its vocabulary.
"""

import os
import sys


class HotkeyUnsupportedError(RuntimeError):
    """No global hotkey is available. The message explains why, and is shown to the user."""


def normalize_key(key):
    """A pynput key as one of 'ctrl', 'shift', 'space', 'other'."""
    name = getattr(key, "name", None)
    if name is None:
        return "other"
    if name.startswith("ctrl"):
        return "ctrl"
    if name.startswith("shift"):
        return "shift"
    if name == "space":
        return "space"
    return "other"


class PushToTalkStateMachine:
    """Which key combination starts and ends a dictation. No input library involved."""

    def __init__(self, on_activate, on_release):
        self.on_activate = on_activate
        self.on_release = on_release
        self._modifiers = set()
        self._active = False

    @property
    def active(self):
        return self._active

    def key_down(self, key):
        if key in ("ctrl", "shift"):
            self._modifiers.add(key)
        elif key == "space" and "ctrl" in self._modifiers and not self._active:
            self._active = True
            self.on_activate("translate" if "shift" in self._modifiers else "dictate")

    def key_up(self, key):
        if key in ("ctrl", "shift"):
            self._modifiers.discard(key)
        # Shift is deliberately not a release trigger: letting go of shift while still
        # holding Ctrl+Space should not cut the recording short.
        if self._active and key in ("space", "ctrl"):
            self._active = False
            self.on_release()

    def reset(self):
        self._modifiers.clear()
        self._active = False


class HotkeyListener:
    """Feeds a PushToTalkStateMachine from a global pynput listener."""

    @staticmethod
    def support():
        """``(supported, reason)``. The reason is empty when supported."""
        if sys.platform == "linux":
            wayland = (os.environ.get("WAYLAND_DISPLAY")
                       or os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland")
            if wayland:
                return False, ("Wayland does not allow applications to watch the keyboard "
                               "globally. Dictation needs an X11 session.")
        return True, ""

    def __init__(self, machine):
        self._machine = machine
        self._listener = None

    def start(self):
        supported, reason = self.support()
        if not supported:
            raise HotkeyUnsupportedError(reason)

        from pynput import keyboard

        self._machine.reset()
        try:
            # Non-suppressing: the keystrokes still reach whatever the user is typing into.
            self._listener = keyboard.Listener(
                on_press=lambda key: self._machine.key_down(normalize_key(key)),
                on_release=lambda key: self._machine.key_up(normalize_key(key)),
            )
            self._listener.start()
        except Exception as err:
            self._listener = None
            raise HotkeyUnsupportedError(
                f"The global hotkey could not be registered: {err}"
            ) from err

    def stop(self):
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        self._machine.reset()
