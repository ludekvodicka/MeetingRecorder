"""Bind saved capture-test evidence to the build, platform and capture implementation."""

import hashlib
import platform
import sys
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def capture_runtime():
    digest = hashlib.sha256()
    digest.update(f"{sys.platform}:{platform.release()}:{sys.version}".encode())
    paths = (
        [Path(sys.executable)]
        if getattr(sys, "frozen", False)
        else [
            Path(__file__).with_name(name)
            for name in (
                "backend.py",
                "backend_macos.py",
                "backend_portaudio.py",
                "backend_wasapi.py",
                "macos_screen_capture.py",
                "session.py",
                "encoder.py",
            )
        ]
    )
    try:
        for path in paths:
            with path.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    digest.update(block)
    except OSError:
        return ""
    return digest.hexdigest()
