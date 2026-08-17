"""Small AppKit bridge for showing urgent Qt windows across macOS Spaces."""

import ctypes
import logging
import sys

from PyQt6.QtGui import QGuiApplication

log = logging.getLogger(__name__)


def show_on_all_spaces(widget, platform_name=None, qt_platform_name=None):
    """Let a top-level Qt widget appear on every Space, including full-screen Spaces.

    Qt exposes an ``NSView *`` through ``winId()`` on macOS.  PyObjC can wrap that
    pointer so the owning ``NSWindow`` can receive the AppKit collection behaviors
    which Qt does not expose.  Failure is deliberately non-fatal: meeting detection
    must continue to work even if a future Qt or macOS release changes this bridge.
    """
    platform_name = platform_name or sys.platform
    if platform_name != "darwin":
        return False
    qt_platform_name = qt_platform_name or QGuiApplication.platformName()
    if qt_platform_name != "cocoa":
        return False

    try:
        import AppKit
        import objc

        native_view = objc.objc_object(
            c_void_p=ctypes.c_void_p(int(widget.winId())))
        native_window = native_view.window()
        if native_window is None:
            return False

        behavior = native_window.collectionBehavior()
        # Qt dialogs default to MoveToActiveSpace. AppKit treats that as mutually
        # exclusive with CanJoinAllSpaces and raises an Objective-C exception if both
        # are present, so replace the default instead of only adding another flag.
        behavior &= ~AppKit.NSWindowCollectionBehaviorMoveToActiveSpace
        behavior |= (
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        native_window.setCollectionBehavior_(behavior)
        return True
    except Exception:
        # PyObjC translates AppKit's NSException into objc.error. This native boundary
        # must not prevent the ordinary prompt from appearing if macOS rejects a flag.
        log.warning("could not make the meeting prompt visible on every macOS Space",
                    exc_info=True)
        return False
