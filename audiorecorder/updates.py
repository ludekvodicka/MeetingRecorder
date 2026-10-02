"""Self-update through the shared autoupdate member, in this application's Qt binding.

The member decides and does everything that needs no Qt: when to check, what to download,
how to verify and how to swap. This module supplies the event loop it runs on and the
facts only this application knows: its repository, its release file names and where a
packaged build lives.
"""

import functools
import os
import ssl
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import platformdirs
import requests
from PyQt6 import sip
from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QApplication

from audiorecorder.shared.desktop.autoupdate.feed import UpdateFeedGithub
from audiorecorder.shared.desktop.autoupdate.installer import UpdateInstaller
from audiorecorder.shared.desktop.autoupdate.manager import UpdateManager
from audiorecorder.shared.desktop.autoupdate.runtime import (
    UpdateRuntime,
    UpdateRuntimeFacts,
    UpdateTarget,
    UpdateTargetFile,
)
from audiorecorder.shared.desktop.autoupdate.stager import UpdateStager
from audiorecorder.shared.desktop.autoupdate.transport import UpdateTransport
from audiorecorder.shared.desktop.autoupdate.view import UpdateView, UpdateViewModel
from audiorecorder.version import __version__


class QtUpdateHost(QObject):
    """The member's UpdateHost on the Qt event loop of the thread that creates it."""

    _posted = pyqtSignal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # A bound method of an object on the GUI thread: an emit from a worker thread is
        # queued onto the GUI thread, an emit from the GUI thread runs at once.
        self._posted.connect(self._run)

    def call_later(self, seconds: float, callback: Callable[[], None]) -> Callable[[], None]:
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(callback)
        timer.timeout.connect(timer.deleteLater)
        timer.start(round(seconds * 1000))

        def cancel() -> None:
            if not sip.isdeleted(timer):
                timer.stop()
                timer.deleteLater()

        return cancel

    def run_in_background[T](self, work: Callable[[], T], on_done: Callable[[T], None],
                             on_error: Callable[[Exception], None]) -> None:
        def run() -> None:
            try:
                result = work()
            except Exception as error:
                # partial binds the error now: the except name is unbound after the block.
                self.post(functools.partial(on_error, error))
                return
            self.post(functools.partial(on_done, result))

        threading.Thread(target=run, name="update-worker", daemon=True).start()

    def post(self, callback: Callable[[], None]) -> None:
        self._posted.emit(callback)

    def _run(self, callback: Callable[[], None]) -> None:
        callback()


class AppUpdates(QObject):
    """What the window sees of the updater: a view model, three actions and the quit hook."""

    changed = pyqtSignal(object)

    owner = "ludekvodicka"
    repository = "MeetingRecorder"
    _manager: UpdateManager
    _busy: Callable[[], str | None]

    def __init__(self, manager: UpdateManager, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._busy = lambda: None
        self._manager.subscribe(lambda status: self.changed.emit(UpdateView.describe(status)))

    @staticmethod
    def create(parent: QObject) -> "AppUpdates":
        """The updater with this application's facts; off in a source run."""
        target = AppUpdates.target()
        root = Path(platformdirs.user_cache_dir("AudioRecorder", appauthor=False)) / "updates"
        # requests' CA bundle, so an AppImage does not depend on where the distribution
        # keeps its certificates.
        context = ssl.create_default_context(cafile=requests.certs.where())
        transport = UpdateTransport(f"MeetingRecorder/{__version__}", context)
        facts = UpdateRuntimeFacts(getattr(sys, "frozen", False), sys.platform, target)
        manager = UpdateManager(
            running=__version__,
            resolution=UpdateRuntime.resolve(facts, UpdateRuntime.writable),
            target=target,
            host=QtUpdateHost(parent),
            feed=UpdateFeedGithub(AppUpdates.owner, AppUpdates.repository, transport),
            stager=UpdateStager(transport, root),
            installer=UpdateInstaller(root),
            release_page=AppUpdates.release_page,
            on_quit=QApplication.quit,
        )
        return AppUpdates(manager, parent)

    @staticmethod
    def target() -> UpdateTarget | None:
        if not getattr(sys, "frozen", False):
            return None
        match sys.platform:
            case "win32":
                return UpdateTargetFile(Path(sys.executable), "Meeting-Recorder-{version}-x64.exe",
                                        onefile_parent=True)
            case "linux":
                appimage = os.environ.get("APPIMAGE")
                if appimage is None:
                    # The unpacked .tar.gz build: there is no single file to replace.
                    return None
                return UpdateTargetFile(Path(appimage),
                                        "Meeting-Recorder-{version}-x86_64.AppImage",
                                        onefile_parent=False)
            case "darwin":
                return None
            case _:
                raise ValueError(f"Unsupported platform: {sys.platform}")

    @staticmethod
    def release_page(version: str | None) -> str:
        base = f"https://github.com/{AppUpdates.owner}/{AppUpdates.repository}/releases"
        return f"{base}/tag/v{version}" if version else f"{base}/latest"

    def set_busy_check(self, busy: Callable[[], str | None]) -> None:
        """busy() returns why installing has to wait, or None."""
        self._busy = busy

    def start(self) -> None:
        self._manager.start()

    def view(self) -> UpdateViewModel:
        return UpdateView.describe(self._manager.status)

    def check(self) -> None:
        self._manager.check()

    def open_release_page(self) -> None:
        url = self._manager.release_page_url()
        if url is not None:
            QDesktopServices.openUrl(QUrl(url))

    def install(self) -> str | None:
        """Restart and install; returns the reason when the application is busy."""
        reason = self._busy()
        if reason is None:
            self._manager.install()
        return reason

    def quit(self, session_ending: bool) -> None:
        """From aboutToQuit: a ready update installs without relaunch, never at session end."""
        if not session_ending and self._busy() is None:
            self._manager.install_on_quit()
        self._manager.stop()
