"""Soniox API key storage.

The key is held by the operating system keyring (Windows Credential Manager, macOS
Keychain, Secret Service on Linux) and never written to a file inside the project or
next to the executable. The SONIOX_API_KEY environment variable overrides the stored
value, which is what development and headless machines use.
"""

import os
import subprocess
import sys

import keyring
from keyring.errors import KeyringError

# "AudioRecorder", not the current product name: this decides where an existing
# installation already keeps the stored key. Renaming it would strand what is there.
SERVICE = "AudioRecorder"
ACCOUNT = "soniox-api-key"
ENV_VAR = "SONIOX_API_KEY"

_NO_BACKEND_HINT = (
    f"No usable keyring backend on this system. Set the {ENV_VAR} environment variable "
    f"instead, or install a Secret Service provider such as gnome-keyring."
)


class SecretsError(RuntimeError):
    """Raised when a key cannot be stored or removed."""


def get_api_key_headless(timeout=10):
    """Read credentials without allowing a Keychain prompt or unbounded wait.

    Isolate native keyring calls so a blocked backend can be terminated. Secrets
    travel only over the private child pipe, never command arguments or logs.
    """
    from_env = os.environ.get(ENV_VAR, "").strip()
    if from_env:
        return from_env
    script = """
import ctypes
import sys
if sys.platform == 'darwin':
    security = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
    disable = security.SecKeychainSetUserInteractionAllowed
    disable.argtypes = [ctypes.c_ubyte]
    disable.restype = ctypes.c_int32
    if disable(False) != 0:
        sys.exit(2)
from audiorecorder.secrets import get_api_key
sys.stdout.write(get_api_key())
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        raise SecretsError(
            "Credential lookup timed out; unlock Keychain or set SONIOX_API_KEY") from None
    if result.returncode:
        raise SecretsError(
            "Credential unavailable without interaction; unlock Keychain or set SONIOX_API_KEY")
    return result.stdout.strip()


def get_api_key():
    """The key from the environment, else the keyring, else an empty string."""
    from_env = os.environ.get(ENV_VAR, "").strip()
    if from_env:
        return from_env
    try:
        return (keyring.get_password(SERVICE, ACCOUNT) or "").strip()
    except KeyringError:
        return ""


def has_stored_key():
    try:
        return bool(keyring.get_password(SERVICE, ACCOUNT))
    except KeyringError:
        return False


def set_api_key(value):
    value = (value or "").strip()
    if not value:
        raise SecretsError("The API key is empty.")
    try:
        keyring.set_password(SERVICE, ACCOUNT, value)
    except KeyringError as err:
        raise SecretsError(f"{_NO_BACKEND_HINT}\n\n{err}") from err


def delete_api_key():
    try:
        keyring.delete_password(SERVICE, ACCOUNT)
    except KeyringError as err:
        raise SecretsError(f"{_NO_BACKEND_HINT}\n\n{err}") from err
