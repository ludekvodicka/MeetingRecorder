"""Keep one copy of the application running at a time.

Two copies are worse than merely wasteful: each installs its own global keyboard hook, so
one Ctrl+Space starts two dictations, transcribes twice and pastes the text into the
document twice, with nothing on screen to suggest why.

A local socket does the arbitrating. The first copy listens on it, later copies fail to
listen, connect instead to say "you are wanted", and exit.
"""

import logging

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

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

    def take_ownership(self):
        """True when this copy is the one that should run."""
        socket = QLocalSocket()
        socket.connectToServer(self._name)
        if socket.waitForConnected(CONNECT_TIMEOUT_MS):
            # Somebody is already listening, so ask them to come forward and step aside.
            socket.write(b"raise")
            socket.waitForBytesWritten(CONNECT_TIMEOUT_MS)
            socket.disconnectFromServer()
            log.info("another copy is already running, asking it to show itself")
            return False

        # A copy that crashed leaves the name behind on Unix sockets and nothing can bind
        # to it again until it is removed.
        QLocalServer.removeServer(self._name)

        self._server = QLocalServer(self)
        if not self._server.listen(self._name):
            # Losing the race is not a reason to refuse to start: better two windows than
            # none. The hook duplication is the thing worth avoiding, and it is unlikely
            # enough here to accept.
            log.warning("could not claim the single-instance lock: %s",
                        self._server.errorString())
            self._server = None
            return True

        self._server.newConnection.connect(self._on_connection)
        return True

    def _on_connection(self):
        connection = self._server.nextPendingConnection()
        if connection is not None:
            connection.disconnectFromServer()
        self.another_instance_started.emit()
