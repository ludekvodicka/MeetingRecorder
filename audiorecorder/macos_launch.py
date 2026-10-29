"""Observe Cocoa launch context without replacing Qt's application delegate."""

import sys


def login_launch(event):
    if event is None:
        return False
    # Apple event constants: oapp / prdt / lgit.
    return (
        event.eventID() == int.from_bytes(b"oapp", "big")
        and event.paramDescriptorForKeyword_(int.from_bytes(b"prdt", "big")) is not None
        and event.paramDescriptorForKeyword_(int.from_bytes(b"prdt", "big")).enumCodeValue()
        == int.from_bytes(b"lgit", "big")
    )


class LaunchContext:
    def __init__(self):
        self.login = False
        self.finished = sys.platform != "darwin"
        self.observer = None
        self.on_finish = None
        if sys.platform == "darwin":
            from AppKit import NSApplicationDidFinishLaunchingNotification
            from Foundation import NSAppleEventManager, NSNotificationCenter

            def launched(notification):
                self.login = login_launch(
                    NSAppleEventManager.sharedAppleEventManager().currentAppleEvent()
                )
                self.finished = True
                if self.on_finish:
                    self.on_finish()

            self.observer = (
                NSNotificationCenter.defaultCenter().addObserverForName_object_queue_usingBlock_(
                    NSApplicationDidFinishLaunchingNotification, None, None, launched
                )
            )
