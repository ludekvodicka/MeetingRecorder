"""Soniox API key storage.

The key is held by the operating system keyring (Windows Credential Manager, macOS
Keychain, Secret Service on Linux) and never written to a file inside the project or
next to the executable. The SONIOX_API_KEY environment variable overrides the stored
value, which is what development and headless machines use.
"""

import os

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
