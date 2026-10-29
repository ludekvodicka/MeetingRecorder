import subprocess
from types import SimpleNamespace

import pytest

from audiorecorder import secrets


def test_environment_avoids_keychain(monkeypatch):
    monkeypatch.setenv("SONIOX_API_KEY", " example ")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("Keychain accessed"))
    assert secrets.get_api_key_headless() == "example"


def test_timeout_is_bounded_and_redacted(monkeypatch):
    monkeypatch.delenv("SONIOX_API_KEY", raising=False)
    def blocked(*args, **kwargs):
        assert kwargs["timeout"] == 10
        raise subprocess.TimeoutExpired(args[0], 10, output="private-key")
    monkeypatch.setattr(subprocess, "run", blocked)
    with pytest.raises(secrets.SecretsError, match="timed out") as error:
        secrets.get_api_key_headless()
    assert "private-key" not in str(error.value)


def test_backend_error_is_redacted(monkeypatch):
    monkeypatch.delenv("SONIOX_API_KEY", raising=False)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=1, stdout="", stderr="private provider detail"))
    with pytest.raises(secrets.SecretsError, match="without interaction"):
        secrets.get_api_key_headless()
