"""Bind saved capture-test evidence to the build, platform and capture implementation."""

import hashlib
import platform
import sys
from functools import lru_cache
from pathlib import Path

from audiorecorder.version import __version__

SOURCES = (
    "backend.py",
    "backend_macos.py",
    "backend_portaudio.py",
    "backend_wasapi.py",
    "macos_screen_capture.py",
    "session.py",
    "encoder.py",
)


@lru_cache(maxsize=1)
def capture_runtime():
    digest = hashlib.sha256()
    digest.update(f"{sys.platform}:{platform.release()}:{sys.version}".encode())
    try:
        if getattr(sys, "frozen", False):
            # Hashing the packaged executable (about 100 MB) stalls the UI thread that asks
            # first. An update or rebuild replaces the file, which changes its size or time.
            stat = Path(sys.executable).stat()
            digest.update(f"{__version__}:{stat.st_size}:{stat.st_mtime_ns}".encode())
        else:
            for name in SOURCES:
                digest.update(Path(__file__).with_name(name).read_bytes())
    except OSError:
        return ""
    return digest.hexdigest()
