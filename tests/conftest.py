"""Keep one QApplication alive across mixed QtCore/QtWidgets test modules."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session", autouse=True)
def qt_application():
    from PyQt6.QtWidgets import QApplication

    # A QCoreApplication created by an earlier module cannot later be upgraded to a
    # QApplication. A session owner also prevents collection between UI test modules.
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture(autouse=True)
def isolate_native_window_watchers(monkeypatch):
    # Unit/integration fixtures use synthetic process IDs, never subscribe to desktop apps.
    from audiorecorder.meetings import call_monitor

    class Watcher:
        def __init__(self, callback):
            self.callback = callback
            self.status = {}

        def watch(self, bundle, process, identifiers):
            self.status[bundle] = f"watching {len(identifiers)}/{len(identifiers)} call windows"

        def close(self):
            pass

        def unwatch(self, bundle):
            self.status.pop(bundle, None)

    monkeypatch.setattr(call_monitor, "WindowClosureWatcher", Watcher)
