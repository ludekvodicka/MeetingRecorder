"""Locate the unpacked browser companion in source and packaged app runtimes."""

import sys
from pathlib import Path


def companion_directory():
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return root / "browser-extension"


def companion_available():
    directory = companion_directory()
    return (directory / "manifest.json").is_file() and (directory / "content.js").is_file()
