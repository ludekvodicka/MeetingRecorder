from types import SimpleNamespace

import pytest

from audiorecorder.meetings.window_closure import WindowClosureWatcher

BUNDLE = "com.microsoft.teams2"


@pytest.mark.parametrize("supported", [True, False])
def test_observer_ignores_other_elements_and_cleans_up_registered_call_window(supported):
    root, other = object(), object()
    identifier = f"ax-{hash(root):x}"
    events, removed, sources = [], [], []
    watcher = WindowClosureWatcher(lambda *event: events.append(event))
    watcher.desired = {BUNDLE: ("42:100", frozenset({identifier}))}
    callbacks = []

    def create(pid, callback, unused):
        assert pid == 42
        callbacks.append(callback)
        return 0, object()

    def run(mode, seconds, return_after_source):
        cb = callbacks[-1]
        cb(None, other, "destroyed", None)
        cb(None, root, "unrelated-notification", None)
        cb(None, root, "destroyed", None)
        watcher.close()
        cb(None, root, "destroyed", None)  # Teardown callbacks cannot escape.

    ax = SimpleNamespace(
        kAXUIElementDestroyedNotification="destroyed",
        AXUIElementCreateSystemWide=lambda: object(),
        AXUIElementSetMessagingTimeout=lambda *args: None,
        AXObserverCreate=create,
        AXObserverGetRunLoopSource=lambda observer: observer,
        AXUIElementCreateApplication=lambda pid: pid,
        AXUIElementCopyAttributeValue=lambda *args: (0, [root, other]),
        AXObserverAddNotification=lambda *args: 0 if supported else -25205,
        AXObserverRemoveNotification=lambda observer, element, notification: removed.append(
            element
        ),
    )
    cf = SimpleNamespace(
        kCFRunLoopDefaultMode="default",
        CFRunLoopGetCurrent=lambda: object(),
        CFRunLoopAddSource=lambda *args: sources.append("add"),
        CFRunLoopRemoveSource=lambda *args: sources.append("remove"),
        CFRunLoopRunInMode=run,
    )
    watcher._run(ax, cf, lambda ax, callback: callback)
    assert events == ([(BUNDLE, "42:100", identifier)] if supported else [])
    assert removed == ([root] if supported else [])
    assert sources == ["add", "remove"]
    assert watcher.status[BUNDLE] == f"watching {int(supported)}/1 call windows"
