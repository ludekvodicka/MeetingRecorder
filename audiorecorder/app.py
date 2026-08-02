import os
import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from audiorecorder.config import load_config, save_config
from audiorecorder.ui.main_window import MainWindow


def asset_path(name):
    """Bundled asset, both from source and from a PyInstaller build.

    PyInstaller unpacks the package tree under sys._MEIPASS, so the path relative to this
    module resolves in both cases and no frozen-vs-source branch is needed.
    """
    return Path(__file__).parent / "assets" / name


def main():
    cfg = load_config()

    os.makedirs(cfg["output_dir"], exist_ok=True)
    save_config(cfg)

    app = QApplication(sys.argv)
    app.setApplicationName("Audio Recorder")
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(str(asset_path("icon.svg"))))

    window = MainWindow(cfg)
    # Raise above whatever had focus, then drop the flag again so the window is not pinned.
    window.setWindowFlags(window.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
    window.show()
    window.setWindowFlags(window.windowFlags() & ~Qt.WindowType.WindowStaysOnTopHint)
    window.show()
    window.raise_()
    window.activateWindow()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
