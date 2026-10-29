"""Keep one copy of the application running at a time.

Two copies are worse than merely wasteful: each installs its own global keyboard hook, so
one Ctrl+Space starts two dictations, transcribes twice and pastes the text into the
document twice, with nothing on screen to suggest why.

A local socket does the arbitrating. The first copy listens on it, later copies fail to
listen, connect instead to say "you are wanted", and exit.
"""

import logging

from PyQt6.QtCore import QDir, QLockFile, QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

# The original product name, so a copy left running from an older build is still
# recognised as the same application during an upgrade.
SERVER_NAME = "AudioRecorder.single-instance"
CONNECT_TIMEOUT_MS = 500

log = logging.getLogger(__name__)


class SingleInstance(QObject):
    """Owns the lock, and reports when another copy asked for the window."""

    another_instance_started = pyqtSignal()

    def __init__(self, name=SERVER_NAME):
        # The name is a parameter so the tests can claim a lock of their own. Sharing the
        # real one would make the suite fail whenever the application happens to be running.
        super().__init__()
        self._name = name
        self._server = None
        self._lock = QLockFile(QDir.tempPath() + "/" + name + ".lock")

    def take_ownership(self):
        """True when this copy is the one that should run."""
        owned = self._lock.tryLock(0)
        socket = QLocalSocket()
        socket.connectToServer(self._name)
        if socket.waitForConnected(CONNECT_TIMEOUT_MS):
            # Somebody is already listening, so ask them to come forward and step aside.
            socket.write(b"raise")
            socket.waitForBytesWritten(CONNECT_TIMEOUT_MS)
            socket.disconnectFromServer()
            log.info("another copy is already running, asking it to show itself")
            if owned:
                self._lock.unlock()
            return False

        if not owned:
            return False

        # A copy that crashed leaves the name behind on Unix sockets and nothing can bind
        # to it again until it is removed.
        QLocalServer.removeServer(self._name)

        self._server = QLocalServer(self)
        if not self._server.listen(self._name):
            # Arbitration failure must never create a second capture/keyboard-hook owner.
            log.warning("could not claim the single-instance lock: %s",
                        self._server.errorString())
            self._server = None
            self._lock.unlock()
            return False

        self._server.newConnection.connect(self._on_connection)
        return True

    def _on_connection(self):
        connection = self._server.nextPendingConnection()
        if connection is not None:
            connection.disconnectFromServer()
        self.another_instance_started.emit()

    def close(self):
        if self._server is not None:
            self._server.close()
            self._server = None
        self._lock.unlock()
