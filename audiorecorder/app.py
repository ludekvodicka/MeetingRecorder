import logging
import os
import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from audiorecorder.config import load_config, save_config
from audiorecorder.single_instance import SingleInstance
from audiorecorder.ui.main_window import MainWindow


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
        import CoreAudio  # noqa: F401
        import CoreMedia  # noqa: F401
        import Quartz  # noqa: F401
        import ScreenCaptureKit  # noqa: F401

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
    if "--smoke-test" in sys.argv:
        _runtime_smoke_test()
        return
    cfg = load_config()

    os.makedirs(cfg["output_dir"], exist_ok=True)
    save_config(cfg)

    app = QApplication(sys.argv)
    app.setApplicationName("Meeting Recorder")
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(str(asset_path("icon.svg"))))

    # Before the window, and before any keyboard hook: a second copy would dictate and
    # paste everything twice.
    instance = SingleInstance()
    if not instance.take_ownership():
        return

    window = MainWindow(cfg)
    instance.another_instance_started.connect(lambda: _raise(window))
    _raise(window)

    sys.exit(app.exec())


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
