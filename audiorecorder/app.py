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


def main():
    _configure_logging()
    cfg = load_config()

    os.makedirs(cfg["output_dir"], exist_ok=True)
    save_config(cfg)

    app = QApplication(sys.argv)
    app.setApplicationName("Audio Recorder")
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
