"""Keep one copy of the application running at a time.

Two copies are worse than merely wasteful: each installs its own global keyboard hook, so
one Ctrl+Space starts two dictations, transcribes twice and pastes the text into the
document twice, with nothing on screen to suggest why.

A lock file in the user's temporary directory does the arbitrating. The first copy holds
it and listens on a local socket; later copies connect to say "you are wanted", and exit.
"""

import hashlib
import logging

from PyQt6.QtCore import QDir, QLockFile, QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

# The original product name, so a copy left running from an older build is still
# recognised as the same application during an upgrade.
LEGACY_SERVER_NAME = "AudioRecorder.single-instance"
CONNECT_TIMEOUT_MS = 500

log = logging.getLogger(__name__)


def user_server_name():
    """The socket name of this user's copy.

    Windows named pipes are machine-wide. With one shared name, a second user signed in
    through Remote Desktop or fast user switching would find the first user's copy and exit.
    """
    user = hashlib.sha256(QDir.homePath().encode()).hexdigest()[:16]
    return f"{LEGACY_SERVER_NAME}.{user}"


class SingleInstance(QObject):
    """Owns the lock, and reports when another copy asked for the window."""

    another_instance_started = pyqtSignal()

    def __init__(self, name, legacy_name=None):
        # The names are parameters so the tests can claim a lock of their own. Sharing the
        # real one would make the suite fail whenever the application happens to be running.
        super().__init__()
        self._name = name
        self._legacy_name = legacy_name
        self._server = None
        self._lock = QLockFile(QDir.tempPath() + "/" + name + ".lock")

    def take_ownership(self):
        """True when this copy is the one that should run."""
        owned = self._lock.tryLock(0)
        for name in (self._name, self._legacy_name):
            if name and self._ask_to_show(name):
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
            # The lock already keeps a second copy out. Without the server a later copy
            # only cannot ask this one to show its window, which is no reason to refuse.
            log.warning("could not listen for other copies: %s", self._server.errorString())
            self._server = None
            return True

        self._server.newConnection.connect(self._on_connection)
        return True

    @staticmethod
    def _ask_to_show(name):
        """True when a copy listens on name; it is then asked to come forward."""
        socket = QLocalSocket()
        socket.connectToServer(name)
        if not socket.waitForConnected(CONNECT_TIMEOUT_MS):
            return False
        socket.write(b"raise")
        socket.waitForBytesWritten(CONNECT_TIMEOUT_MS)
        socket.disconnectFromServer()
        log.info("another copy is already running, asking it to show itself")
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
