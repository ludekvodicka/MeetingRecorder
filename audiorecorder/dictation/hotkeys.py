"""Global push-to-talk hotkey.

Ctrl+Space dictates, Ctrl+Shift+Space dictates and translates, and the recording lasts
exactly as long as the keys are held. That needs both the press and the release of a key
the application does not have focus for.

On Windows, pynput's listener uses a WH_KEYBOARD_LL hook: Windows holds every key press
until the Python callback gets the GIL and returns. Raw Input never waits for our code,
so Windows uses a Raw Input listener thread. macOS and Linux keep pynput, with two limits
worth stating plainly:

* Wayland has no global hotkey mechanism at all, so dictation is turned off there rather
  than silently doing nothing. An X11 session works.
* macOS asks for Accessibility permission the first time, and refuses the listener until
  it is granted. It also uses Ctrl+Space for the input source switcher by default.

The decision of what a key event means lives in PushToTalkStateMachine, which imports
nothing and is unit-tested. The listener only translates key reports into its vocabulary.
"""

import os
import sys

# Virtual key codes for the modifiers, either side of the keyboard.
_WINDOWS_MODIFIER_KEYS = {"ctrl": 0x11, "shift": 0x10}
_RAW_KEYS = {
    0x11: "ctrl", 0xA2: "ctrl", 0xA3: "ctrl",
    0x10: "shift", 0xA0: "shift", 0xA1: "shift",
    0x20: "space",
}
_RI_KEY_BREAK = 0x01


class HotkeyUnsupportedError(RuntimeError):
    """No global hotkey is available. The message explains why, and is shown to the user."""


def held_modifiers():
    """Modifiers physically down right now, or None where the platform cannot say.

    Tracking key events alone is not enough: pasting a dictation sends a synthetic Ctrl+V,
    the global listener sees those events like any other, and the synthetic release would
    clear a Ctrl the user is still holding. The next Ctrl+Space would then be ignored until
    they let go and pressed Ctrl again. Asking the operating system avoids the whole class
    of problem, and is what the previous implementation did.
    """
    if sys.platform != "win32":
        return None
    import ctypes

    user32 = ctypes.windll.user32
    return {
        name for name, key in _WINDOWS_MODIFIER_KEYS.items()
        if user32.GetAsyncKeyState(key) & 0x8000
    }


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


def normalize_raw_key(vkey, flags):
    """A Windows raw report as ``(pressed, 'ctrl' | 'shift' | 'space' | 'other')``."""
    return not flags & _RI_KEY_BREAK, _RAW_KEYS.get(vkey, "other")


class PushToTalkStateMachine:
    """Which key combination starts and ends a dictation. No input library involved.

    ``modifier_probe`` returns the modifiers actually held, or None when the platform
    cannot tell, in which case the ones seen in the event stream are used instead.
    """

    def __init__(self, on_activate, on_release, modifier_probe=None):
        self.on_activate = on_activate
        self.on_release = on_release
        self._modifier_probe = modifier_probe
        self._modifiers = set()
        self._active = False

    @property
    def active(self):
        return self._active

    def _held(self):
        if self._modifier_probe is not None:
            live = self._modifier_probe()
            if live is not None:
                return live
        return self._modifiers

    def key_down(self, key):
        if key in ("ctrl", "shift"):
            self._modifiers.add(key)
        elif key == "space" and not self._active:
            held = self._held()
            if "ctrl" in held:
                self._active = True
                self.on_activate("translate" if "shift" in held else "dictate")

    def key_up(self, key):
        if key in ("ctrl", "shift"):
            self._modifiers.discard(key)
        if not self._active:
            return
        # Shift is deliberately not a release trigger: letting go of shift while still
        # holding Ctrl+Space should not cut the recording short.
        if key == "space":
            self._active = False
            self.on_release()
        elif key == "ctrl" and "ctrl" not in self._held():
            # A release the operating system disagrees with is our own synthetic paste
            # keystroke, not the user letting go, and must not end the dictation.
            self._active = False
            self.on_release()

    def reset(self):
        self._modifiers.clear()
        self._active = False


class HotkeyListener:
    """Feeds a PushToTalkStateMachine from Raw Input on Windows and pynput elsewhere."""

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

        self._machine.reset()
        try:
            self._listener = (
                self._raw_listener() if sys.platform == "win32" else self._pynput_listener()
            )
            self._listener.start()
        except Exception as err:
            self._listener = None
            raise HotkeyUnsupportedError(
                f"The global hotkey could not be registered: {err}"
            ) from err

    def _raw_listener(self):
        from audiorecorder.dictation.raw_keyboard import RawKeyboardListener

        return RawKeyboardListener(self._on_raw_key, on_error=self._on_raw_error)

    def _on_raw_key(self, vkey, flags):
        pressed, key = normalize_raw_key(vkey, flags)
        if pressed:
            self._machine.key_down(key)
        else:
            self._machine.key_up(key)

    def _on_raw_error(self):
        try:
            self._machine.key_up("space")
        finally:
            self._machine.reset()

    def _pynput_listener(self):
        from pynput import keyboard

        # Non-suppressing: the keystrokes still reach whatever the user is typing into.
        return keyboard.Listener(
            on_press=lambda key: self._machine.key_down(normalize_key(key)),
            on_release=lambda key: self._machine.key_up(normalize_key(key)),
        )

    def stop(self):
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        self._machine.reset()
