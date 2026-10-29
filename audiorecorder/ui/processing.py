"""Own queued workers until their threads have actually stopped."""

from collections import deque

from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot


class ProcessingQueue(QObject):
    progress = pyqtSignal(str, str)
    completed = pyqtSignal(str, str, str)
    failed = pyqtSignal(str, str, str)
    drained = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pending = deque()
        self.active = None
        self.outcome = None

    def is_running(self):
        return self.active is not None or bool(self.pending)

    def enqueue(self, kind, path, worker):
        self.pending.append((kind, path, worker))
        self.progress.emit(path, 'Queued')
        if self.active is None:
            self._start_next()

    def _start_next(self):
        if self.active is not None:
            return
        if not self.pending:
            self.drained.emit()
            return
        kind, path, worker = self.pending.popleft()
        thread = QThread(self)
        self.active = (kind, path, worker, thread)
        self.outcome = None
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._progress)
        worker.completed.connect(self._completed)
        worker.error.connect(self._failed)
        worker.completed.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._finished)
        message = 'Starting transcription...' if kind == 'transcribe' else 'Starting cleanup...'
        self.progress.emit(path, message)
        thread.start()

    @pyqtSlot(str)
    def _progress(self, message):
        self.progress.emit(self.active[1], message)

    @pyqtSlot(str)
    def _completed(self, result):
        self.outcome = (True, result)

    @pyqtSlot(str)
    def _failed(self, error):
        self.outcome = (False, error)

    @pyqtSlot()
    def _finished(self):
        kind, path, worker, thread = self.active
        success, result = self.outcome or (False, 'Worker stopped without a result')
        self.active = None
        thread.deleteLater()
        if success:
            self.completed.emit(kind, path, result)
        else:
            self.failed.emit(kind, path, result)
        self._start_next()
