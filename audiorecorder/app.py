import logging
import os
import sys
from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from audiorecorder.config import load_config, save_config
from audiorecorder.single_instance import SingleInstance
from audiorecorder.ui.main_window import MainWindow
from audiorecorder.ui.update_indicator import UpdateIndicator
from audiorecorder.updates import AppUpdates


def asset_path(name):
    """Bundled asset, both from source and from a PyInstaller build.

    PyInstaller unpacks the package tree under sys._MEIPASS, so the path relative to this
    module resolves in both cases and no frozen-vs-source branch is needed.
    """
    return Path(__file__).parent / "assets" / name


def _configure_logging():
    """Diagnostics on demand: AUDIORECORDER_LOG=debug turns on the running commentary.

    Off by default, and skipped outright when there is nowhere to write: a windowed build
    has no stderr, and handing None to basicConfig would take the application down on
    startup instead of merely losing the logs.
    """
    level = os.environ.get("AUDIORECORDER_LOG", "").upper()
    if not level or sys.stderr is None:
        return
    logging.basicConfig(
        level=getattr(logging, level, logging.DEBUG),
        format="%(asctime)s.%(msecs)03d  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    # websocket-client logs every frame at debug level, which buries everything else.
    logging.getLogger("websocket").setLevel(logging.INFO)


def _runtime_smoke_test(platform_name=None):
    """Import platform-only runtime pieces without touching hardware or user settings."""
    if platform_name is None:
        platform_name = sys.platform
    if not asset_path("icon.svg").is_file():
        raise RuntimeError("bundled application assets are missing")

    if platform_name == "darwin":
        import ApplicationServices
        import CoreAudio  # noqa: F401
        import CoreFoundation
        import CoreMedia  # noqa: F401
        import EventKit  # noqa: F401
        import Quartz  # noqa: F401
        import ScreenCaptureKit  # noqa: F401
        import ServiceManagement  # noqa: F401
        import UserNotifications  # noqa: F401

        from audiorecorder.macos_notifications import _delegate_class
        from audiorecorder.meetings.window_closure import observer_callback

        _delegate_class()

        callback = observer_callback(ApplicationServices, lambda *args: None)
        error, observer = ApplicationServices.AXObserverCreate(os.getpid(), callback, None)
        if error:
            raise RuntimeError("native call-window observer unavailable")
        source = ApplicationServices.AXObserverGetRunLoopSource(observer)
        loop = CoreFoundation.CFRunLoopGetCurrent()
        CoreFoundation.CFRunLoopAddSource(loop, source, CoreFoundation.kCFRunLoopDefaultMode)
        CoreFoundation.CFRunLoopRemoveSource(loop, source, CoreFoundation.kCFRunLoopDefaultMode)

        from audiorecorder.audio.macos_screen_capture import _make_serial_queue

        _make_serial_queue("org.meetingrecorder.smoke-test")
    elif platform_name == "win32":
        import pyaudiowpatch  # noqa: F401
    elif platform_name == "linux":
        import sounddevice  # noqa: F401
    else:
        raise RuntimeError(f"unsupported smoke-test platform: {platform_name}")


def main():
    _configure_logging()
    if "--native-call-snapshot" in sys.argv:
        from audiorecorder.meetings.macos_call_probe import write_snapshot

        index = sys.argv.index("--native-call-snapshot")
        if len(sys.argv) != index + 2:
            raise SystemExit(2)
        write_snapshot(sys.argv[index + 1])
        return
    if "--credential-status" in sys.argv:
        from audiorecorder.readiness import STATUS_CODES, credential_status

        raise SystemExit(STATUS_CODES[credential_status()])
    if "--smoke-test" in sys.argv:
        _runtime_smoke_test()
        return
    if "--notification-smoke" in sys.argv:
        _notification_smoke()
        return
    from audiorecorder.macos_launch import LaunchContext
    from audiorecorder.macos_notifications import Notifications

    launch = LaunchContext()
    notifications = Notifications()
    app = QApplication(sys.argv)
    app.setApplicationName("Meeting Recorder")
    if sys.platform == "darwin":
        # Closing the window hides it into the menu bar so meeting detection remains alive.
        app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(str(asset_path("icon.svg"))))

    # Before the window, and before any keyboard hook: a second copy would dictate and
    # paste everything twice.
    instance = SingleInstance()
    if not instance.take_ownership():
        return

    cfg = load_config()
    os.makedirs(cfg["output_dir"], exist_ok=True)
    save_config(cfg)
    updates = AppUpdates.create(app)
    window = MainWindow(cfg, notifications)
    updates.set_busy_check(window.busy_reason)
    window.statusBar().addPermanentWidget(UpdateIndicator(updates, window))
    session_ending = []
    app.commitDataRequest.connect(lambda _manager: session_ending.append(True))
    app.aboutToQuit.connect(lambda: updates.quit(bool(session_ending)))
    quit_filter = QuitFilter(window)
    app.installEventFilter(quit_filter)
    app.aboutToQuit.connect(instance.close)
    instance.another_instance_started.connect(lambda: _raise(window))

    def show_launch():
        if not launch.login:
            _raise(window)

    updates.start()
    launch.on_finish = show_launch
    if launch.finished:
        QTimer.singleShot(0, show_launch)
    sys.exit(app.exec())


def _notification_smoke():
    """Exercise bundle identity, native settings and real selectors without prompting."""
    import json

    from audiorecorder.macos_notifications import Notifications

    notifications = Notifications()
    application = QApplication(sys.argv)
    result = {}

    def observed(status):
        result.update(
            authorization=status.authorization,
            alerts=status.alerts,
            error=status.error,
            delegate=notifications.delegate is not None,
        )
        application.quit()

    notifications.status_changed.connect(observed)
    QTimer.singleShot(0, notifications.refresh)
    QTimer.singleShot(5000, application.quit)
    application.exec()
    notifications.close()
    if not result or not result.get("delegate") or result.get("error"):
        raise RuntimeError(f"Native notification smoke failed: {result}")
    index = sys.argv.index("--notification-smoke")
    if len(sys.argv) > index + 1:
        Path(sys.argv[index + 1]).write_text(json.dumps(result))


class QuitFilter(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Quit and not getattr(self.window, "_exit_ready", False):
            self.window._quit_from_tray()
            return True
        return False


def _raise(window):
    # Above whatever had focus, then the flag comes straight off so the window is not
    # pinned in front of everything else for good.
    window.setWindowFlags(window.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
    window.show()
    window.setWindowFlags(window.windowFlags() & ~Qt.WindowType.WindowStaysOnTopHint)
    window.show()
    window.raise_()
    window.activateWindow()


if __name__ == "__main__":
    main()
