"""Bounded, noninteractive optional credential diagnostics; never returns key values."""

import os
import subprocess
import sys
import threading
from contextlib import suppress

from PyQt6.QtCore import QObject, pyqtSignal

STATUS_CODES = {"Ready": 0, "Missing": 2, "Keychain denied": 3, "Unknown": 4}


def credential_status():
    """Child-process entrypoint. Do not permit native credential UI."""
    from audiorecorder import secrets

    if os.environ.get(secrets.ENV_VAR, "").strip():
        return "Ready"
    if sys.platform == "darwin":
        import ctypes

        security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        disable = security.SecKeychainSetUserInteractionAllowed
        disable.argtypes = [ctypes.c_ubyte]
        disable.restype = ctypes.c_int32
        if disable(False) != 0:
            return "Keychain denied"
    try:
        return (
            "Ready" if secrets.keyring.get_password(secrets.SERVICE, secrets.ACCOUNT) else "Missing"
        )
    except Exception:
        return "Keychain denied" if sys.platform == "darwin" else "Unknown"


class CredentialProbe(QObject):
    finished = pyqtSignal(str)

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="credential-status").start()

    def _run(self):
        command = [sys.executable]
        if not getattr(sys, "frozen", False):
            command += ["-m", "audiorecorder"]
        command += ["--credential-status"]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
            # Windowed PyInstaller apps may have no stdout. Exit codes carry only status.
            value = {code: name for name, code in STATUS_CODES.items()}.get(
                result.returncode, "Unknown"
            )
        except subprocess.TimeoutExpired:
            value = "Timed out"
        except OSError:
            value = "Unknown"
        # Settings may close while the bounded child is running.
        with suppress(RuntimeError):
            self.finished.emit(value)


def microphone_permission(platform_name=None):
    platform_name = platform_name or sys.platform
    if platform_name != "darwin":
        return "Not applicable"
    try:
        import AVFoundation

        status = int(
            AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(
                AVFoundation.AVMediaTypeAudio
            )
        )
        return {0: "Not requested", 1: "Restricted", 2: "Denied", 3: "Granted"}.get(
            status, "Unknown"
        )
    except Exception:
        return "Unknown"


def capture_readiness(config, test, runtime, *, platform_name=None, microphone=None):
    """Automation requires a successful test for this runtime and saved device choice."""
    platform_name = platform_name or sys.platform
    microphone = microphone or microphone_permission(platform_name)
    if platform_name == "darwin" and microphone != "Granted":
        return False, f"Microphone permission: {microphone}; request/test audio manually first"
    if (
        not runtime
        or not isinstance(test, dict)
        or test.get("runtime") != runtime
        or not isinstance(test.get("config"), dict)
        or not isinstance(test.get("frames"), dict)
    ):
        return False, "Run Test Audio after installing this build"
    if test.get("status") != "Recording saved; encoded audio validated":
        return False, "The last capture did not finish successfully; test audio again"
    if any(
        test.get("config", {}).get(key) != config.get(key)
        for key in ("output_dir", "system_source_name", "mic_source_name")
    ):
        return False, "Saved devices/output changed; test the new configuration"
    frames = test.get("frames", {})
    if frames.get("mic", 0) <= 0 or (
        test.get("system") is not None and frames.get("system", 0) <= 0
    ):
        return False, "The test did not receive frames from every configured source"
    return (
        True,
        "Audio test matches this build and configuration; native capture remains authoritative",
    )
