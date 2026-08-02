"""Deliver dictated text into whatever the user is typing in.

The text goes on the clipboard and is pasted with a synthetic keystroke, then the previous
clipboard content is put back, so dictating does not cost the user what they had copied.
On Linux this needs xclip or xsel for the clipboard and an X11 session for the keystroke.
"""

import contextlib
import logging
import sys
import time

import pyperclip

PASTE_SETTLE = 0.05
PASTE_DELIVER = 0.1

log = logging.getLogger(__name__)


def paste_text(text):
    if not text or not text.strip():
        return

    from pynput.keyboard import Controller, Key

    original = None
    with contextlib.suppress(Exception):
        original = pyperclip.paste()

    pyperclip.copy(text)
    time.sleep(PASTE_SETTLE)
    log.debug("clipboard set, sending the paste keystroke")

    modifier = Key.cmd if sys.platform == "darwin" else Key.ctrl
    controller = Controller()
    with controller.pressed(modifier):
        controller.tap("v")
    time.sleep(PASTE_DELIVER)
    log.debug("paste keystroke sent")

    if original is not None:
        with contextlib.suppress(Exception):
            pyperclip.copy(original)
