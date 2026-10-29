import threading

import pytest
from PyQt6.QtWidgets import QApplication

from audiorecorder.audio.backend import CaptureUnresolved
from audiorecorder.audio.session import CaptureSession


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class Backend:
    def set_level_callbacks(self, *args):
        pass

    def set_error_callback(self, callback):
        self.callback = callback

    def start(self, *args):
        pass

    def close(self):
        pass

    def stop(self):
        raise CaptureUnresolved("still running")


def test_unresolved_stop_retains_backend_and_paths(app):
    backend = Backend()
    session = CaptureSession(lambda: backend)
    results = []
    session.stopped.connect(results.append)
    session.recording = "a"
    session._start("a", {"output_dir": "unused"})
    session._stop("a")
    assert results[0].unresolved
    assert session.backend is backend
    session.close()
    assert app is not None


def test_queued_capture_errors_preserve_origin(app):
    backend = Backend()
    session = CaptureSession(lambda: backend)
    errors = []
    session.failed.connect(lambda *args: errors.append(args))
    session.recording = "a"
    session._start("a", {"output_dir": "unused"})
    session.recording = "b"
    backend.callback("late")
    assert errors == [("a", "late")]
    session.close()
    assert app is not None


def test_blocking_start_does_not_block_qt_event_loop(app):
    entered, release = threading.Event(), threading.Event()
    backend = Backend()
    backend.start = lambda *args: entered.set() or release.wait(2)
    session = CaptureSession(lambda: backend)
    session.start("a", {"output_dir": "unused"})
    assert entered.wait(1)
    # A GUI-thread invocation can execute while capture waits in the worker.
    from PyQt6.QtCore import QTimer

    observed = []
    QTimer.singleShot(0, lambda: observed.append(True))
    app.processEvents()
    assert observed
    release.set()
    session.executor.shutdown(wait=True)
    app.processEvents()


def test_unattended_start_rejects_changed_devices_before_opening_audio(app):
    backend = Backend()
    opened = []
    backend.start = lambda *args: opened.append(True)
    backend.list_microphones = lambda: []
    backend.list_system_sources = lambda: []
    backend.resolve_sources = lambda *args: ("Native", "New default microphone")
    session = CaptureSession(lambda: backend)
    results = []
    session.started.connect(results.append)
    session.recording = "a"
    session._start(
        "a",
        {
            "output_dir": "unused",
            "_automatic_capture": True,
            "_verified_sources": ("Native", "Old default microphone"),
        },
    )
    assert not opened
    assert results[0].error and not results[0].unresolved
    assert session.backend is None
    session.close()


def test_unattended_fallback_is_closed_before_start_is_acknowledged(app):
    backend = Backend()
    closed = []
    backend.close = lambda: closed.append(True)
    backend.list_microphones = lambda: []
    backend.list_system_sources = lambda: []
    backend.resolve_sources = lambda *args: ("Native", "Tested microphone")
    backend.active_system_source = "Native"
    backend.active_mic_source = "Fallback microphone"
    session = CaptureSession(lambda: backend)
    results = []
    session.started.connect(results.append)
    session.recording = "a"
    session._start(
        "a",
        {
            "output_dir": "unused",
            "_automatic_capture": True,
            "_verified_sources": ("Native", "Tested microphone"),
        },
    )
    assert closed == [True]
    assert "fallback" in results[0].error
    assert not results[0].unresolved and session.backend is None
    session.close()
