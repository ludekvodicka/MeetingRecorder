"""Serialize blocking capture operations, keeping ownership through unresolved failures."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from PyQt6.QtCore import QObject, pyqtSignal

from audiorecorder.audio.backend import CaptureError, CaptureUnresolved, create_backend


@dataclass(frozen=True)
class CaptureResult:
    recording: str
    backend: object = None
    paths: tuple = ()
    error: str = ""
    unresolved: bool = False
    frames: tuple = ()


class CaptureSession(QObject):
    started = pyqtSignal(object)
    stopped = pyqtSignal(object)
    failed = pyqtSignal(str, str)
    system_level = pyqtSignal(float)
    mic_level = pyqtSignal(float)

    def __init__(self, factory=create_backend):
        super().__init__()
        self.factory = factory
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="capture")
        self.backend = None
        self.recording = ""

    def start(self, recording, config):
        if self.recording:
            self.started.emit(
                CaptureResult(recording, error="Capture owner is busy", unresolved=True)
            )
            return
        self.recording = recording
        config = dict(config)
        self.executor.submit(self._start, recording, config)

    def _start(self, recording, config):
        backend = None
        try:
            if self.backend is not None:
                raise CaptureUnresolved("Previous capture resources have not been released")
            backend = self.factory()
            self.backend = backend
            backend.set_level_callbacks(self.system_level.emit, self.mic_level.emit)
            backend.set_error_callback(lambda message: self.failed.emit(recording, message))
            if config.get("_automatic_capture"):
                self._verify_sources(backend, config)
            backend.start(
                config["output_dir"],
                config.get("system_source_name"),
                config.get("mic_source_name"),
            )
            if config.get("_automatic_capture") and tuple(config["_verified_sources"]) != (
                backend.active_system_source,
                backend.active_mic_source,
            ):
                raise CaptureError("Audio opened a fallback device; run Test Audio again")
            result = CaptureResult(recording, backend=backend)
        except Exception as error:
            unresolved = isinstance(error, CaptureUnresolved)
            if backend is not None and not unresolved:
                try:
                    backend.close()
                except Exception:
                    unresolved = True
            if not unresolved:
                self.backend = None
                self.recording = ""
            result = CaptureResult(
                recording, backend=backend, error=str(error), unresolved=unresolved
            )
        self.started.emit(result)

    @staticmethod
    def _verify_sources(backend, config):
        for key, devices in (
            ("mic_source_name", backend.list_microphones),
            ("system_source_name", backend.list_system_sources),
        ):
            requested = config.get(key)
            if requested and requested not in {device.name for device in devices()}:
                raise CaptureError("A configured audio device is unavailable; test audio manually")
        actual = backend.resolve_sources(
            config.get("system_source_name"), config.get("mic_source_name")
        )
        expected = config.get("_verified_sources")
        if expected is None or tuple(expected) != tuple(actual):
            raise CaptureError("Resolved audio devices changed; run Test Audio before automation")

    def stop(self, recording):
        self.executor.submit(self._stop, recording)

    def _stop(self, recording):
        if recording != self.recording or self.backend is None:
            return
        backend = self.backend
        try:
            paths = backend.stop()
            backend.set_system_tap(None)
            backend.set_mic_tap(None)
            backend.close()
            self.backend = None
            self.recording = ""
            result = CaptureResult(
                recording, paths=paths, frames=tuple(getattr(backend, "frame_counts", {}).items())
            )
        except Exception as error:
            result = CaptureResult(recording, backend=backend, error=str(error), unresolved=True)
        self.stopped.emit(result)

    def wait_idle(self, timeout):
        """Block until queued start/stop work has run; False when timeout passed first."""
        try:
            self.executor.submit(lambda: None).result(timeout)
            return True
        except TimeoutError:
            return False

    def close(self):
        # Called only after the lifecycle has acknowledged released capture resources.
        self.executor.shutdown(wait=False)
