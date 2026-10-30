import time
from datetime import datetime
from threading import Event

from PyQt6.QtCore import QCoreApplication, QObject, pyqtSignal

from audiorecorder.recordings.manager import Recording
from audiorecorder.ui.processing import ProcessingQueue
from audiorecorder.ui.recording_list import RecordingListWidget


def until(condition):
    deadline = time.monotonic() + 3
    while not condition():
        QCoreApplication.processEvents()
        assert time.monotonic() < deadline, 'Qt operation timed out'
        time.sleep(.005)
    QCoreApplication.processEvents()


class Worker(QObject):
    progress = pyqtSignal(str)
    completed = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, fail=False):
        super().__init__()
        self.started = Event()
        self.release = Event()
        self.fail = fail

    def run(self):
        self.started.set()
        self.progress.emit('Transcribing...')
        self.release.wait(3)
        if self.fail:
            self.error.emit('test failure')
        else:
            self.completed.emit('saved.md')


def test_multiple_recordings_and_cleanup_are_serialized_until_threads_stop():
    queue = ProcessingQueue()
    workers = [Worker(), Worker(fail=True), Worker()]
    completed, failed = [], []
    queue.completed.connect(lambda *result: completed.append(result))
    queue.failed.connect(lambda *result: failed.append(result))
    try:
        queue.enqueue('transcribe', 'one.m4a', workers[0])
        queue.enqueue('transcribe', 'two.m4a', workers[1])
        queue.enqueue('cleanup', 'three.m4a', workers[2])
        until(workers[0].started.is_set)
        assert not workers[1].started.is_set()
        assert queue.is_running()
        first_thread = queue.active[3]
        workers[0].release.set()
        until(workers[1].started.is_set)
        assert completed == [('transcribe', 'one.m4a', 'saved.md')]
        # The queue has retained ownership through thread completion.
        assert queue.active[3] is not first_thread
        assert not workers[2].started.is_set()
        workers[1].release.set()
        until(workers[2].started.is_set)
        assert failed == [('transcribe', 'two.m4a', 'test failure')]
        workers[2].release.set()
        until(lambda: not queue.is_running())
        assert completed[-1] == ('cleanup', 'three.m4a', 'saved.md')
    finally:
        for worker in workers:
            worker.release.set()
        until(lambda: not queue.is_running())


def test_recording_progress_survives_refresh_and_all_mutating_buttons_disable():
    listing = RecordingListWidget()
    rec = Recording('one.m4a', 'one', datetime.now(), 10, False, '')
    listing.set_recordings([rec])
    listing.set_processing(rec.path, 'Uploading audio...', busy=True)
    listing.set_recordings([rec])
    row = listing._item_widgets[0]
    assert row._status_chip.text() == 'Uploading audio'
    assert row._primary.isHidden() and not row._more.isEnabled()
    listing.set_failure(rec.path, 'transcribe', 'test failure')
    assert row._status_chip.text() == 'Failed'
    assert row._more.isEnabled() and row._primary.text().strip() == 'Retry'
    assert listing.failure(rec.path) == ('transcribe', 'test failure')
    listing.set_processing(rec.path, 'Queued', busy=True)
    assert row._status_chip.text() == 'Queued' and listing.failure(rec.path) is None
    listing.set_processing(rec.path, '', busy=False)
    assert row._status_chip.text() == 'Not transcribed'
    assert row._primary.text().strip() == 'Transcribe'


def test_transcribe_handler_guards_duplicate_and_queues_other_recording(monkeypatch):
    from PyQt6.QtWidgets import QWidget

    from audiorecorder.ui import main_window
    from audiorecorder.ui.main_window import MainWindow

    class Window(QWidget):
        _transcribe_recording = MainWindow._transcribe_recording
        _background_threads_running = MainWindow._background_threads_running

    window = Window()
    window._quitting = False
    window._busy_paths = set()
    window._busy_kinds = {}
    window.config = {}
    window._processing = ProcessingQueue(window)
    workers, warnings = [], []

    def make_worker(**kwargs):
        worker = Worker()
        workers.append(worker)
        return worker

    monkeypatch.setattr(main_window, 'SonioxWorker', make_worker)
    monkeypatch.setattr(main_window.secrets, 'get_api_key', lambda: 'fake-key')
    monkeypatch.setattr(main_window.QMessageBox, 'warning', lambda *args: warnings.append(args))
    window._processing.completed.connect(
        lambda kind, path, result: window._busy_paths.discard(path))
    try:
        window._transcribe_recording('first.m4a')
        until(workers[0].started.is_set)
        window._transcribe_recording('first.m4a')
        window._transcribe_recording('second.m4a')
        assert len(workers) == 2
        assert len(warnings) == 1
        assert not workers[1].started.is_set()
        assert window._busy_paths == {'first.m4a', 'second.m4a'}
        assert window._background_threads_running()
        # A quit in progress refuses new work, while existing queued jobs drain.
        window._quitting = True
        window._transcribe_recording('third.m4a')
        assert len(workers) == 2
        workers[0].release.set()
        until(workers[1].started.is_set)
        assert window._busy_paths == {'second.m4a'}
        workers[1].release.set()
        until(lambda: not window._background_threads_running())
        assert not window._busy_paths
    finally:
        for worker in workers:
            worker.release.set()
        until(lambda: not window._processing.is_running())
