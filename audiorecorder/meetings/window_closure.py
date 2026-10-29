"""Observe destruction of recognized call windows without reading their content.

A dedicated run-loop thread retains the native callback/observer until cleanup. Only
identities from active-call snapshots are subscribed; application-wide events are ignored.
"""

import sys
import threading
from contextlib import suppress

from audiorecorder.meetings.native_profiles import PROFILES


def observer_callback(ax, handler):
    import objc

    return objc.callbackFor(ax.AXObserverCreate)(handler)


class WindowClosureWatcher:
    def __init__(self, callback):
        self.callback = callback
        self.desired = {}
        self.status = {}
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.thread = None

    def watch(self, bundle, process, identifiers):
        if sys.platform != "darwin" or bundle not in PROFILES or not identifiers:
            return
        with self.lock:
            self.desired[bundle] = (process, frozenset(identifiers))
            if self.thread is None:
                self.thread = threading.Thread(
                    target=self._run, daemon=True, name="call-window-close"
                )
                self.thread.start()

    def close(self):
        self.stopping.set()

    def unwatch(self, bundle):
        with self.lock:
            self.desired.pop(bundle, None)
        self.status.pop(bundle, None)

    def _run(self, ax=None, cf=None, decorate=observer_callback):
        if ax is None:
            import ApplicationServices as ax
        if cf is None:
            import CoreFoundation as cf

        subscriptions = {}
        run_loop = cf.CFRunLoopGetCurrent()
        ax.AXUIElementSetMessagingTimeout(ax.AXUIElementCreateSystemWide(), 0.08)
        notification = ax.kAXUIElementDestroyedNotification

        def unregister(observer, root):
            with suppress(Exception):
                ax.AXObserverRemoveNotification(observer, root, notification)

        def remove(bundle):
            entry = subscriptions.pop(bundle, None)
            if entry:
                _, observer, _, roots = entry
                for root in roots.values():
                    unregister(observer, root)
                cf.CFRunLoopRemoveSource(
                    run_loop, ax.AXObserverGetRunLoopSource(observer), cf.kCFRunLoopDefaultMode
                )

        def callback_for(bundle, process):
            def received(observer, element, kind, refcon):
                identifier = f"ax-{hash(element):x}"
                entry = subscriptions.get(bundle)
                if (
                    not self.stopping.is_set()
                    and entry
                    and entry[0] == process
                    and identifier in entry[3]
                    and kind == notification
                ):
                    with suppress(RuntimeError):
                        self.callback(bundle, process, identifier)

            return decorate(ax, received)

        try:
            while not self.stopping.is_set():
                with self.lock:
                    desired = dict(self.desired)
                for bundle in subscriptions.keys() - desired.keys():
                    remove(bundle)
                for bundle, (process, identifiers) in desired.items():
                    try:
                        if bundle in subscriptions and subscriptions[bundle][0] != process:
                            remove(bundle)
                        if bundle not in subscriptions:
                            callback = callback_for(bundle, process)
                            error, observer = ax.AXObserverCreate(
                                int(process.split(":", 1)[0]), callback, None
                            )
                            if error:
                                raise RuntimeError("observer-unavailable")
                            subscriptions[bundle] = (process, observer, callback, {})
                            cf.CFRunLoopAddSource(
                                run_loop,
                                ax.AXObserverGetRunLoopSource(observer),
                                cf.kCFRunLoopDefaultMode,
                            )
                        _, observer, callback, roots = subscriptions[bundle]
                        for identifier in roots.keys() - identifiers:
                            unregister(observer, roots.pop(identifier))
                        missing = identifiers - roots.keys()
                        if missing:
                            app = ax.AXUIElementCreateApplication(int(process.split(":", 1)[0]))
                            error, windows = ax.AXUIElementCopyAttributeValue(
                                app, "AXWindows", None
                            )
                            if error or windows is None or len(windows) > 32:
                                raise RuntimeError("window-list-unavailable")
                            for root in windows:
                                identifier = f"ax-{hash(root):x}"
                                if (
                                    identifier in missing
                                    and ax.AXObserverAddNotification(
                                        observer, root, notification, None
                                    )
                                    == 0
                                ):
                                    roots[identifier] = root
                        count = len(identifiers & roots.keys())
                        self.status[bundle] = f"watching {count}/{len(identifiers)} call windows"
                    except Exception:
                        self.status[bundle] = "observer-unavailable"
                cf.CFRunLoopRunInMode(cf.kCFRunLoopDefaultMode, 0.2, False)
        finally:
            for bundle in tuple(subscriptions):
                remove(bundle)
