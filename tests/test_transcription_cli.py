import json

import pytest

from audiorecorder.transcription import cli
from audiorecorder.transcription.client import SonioxClient


@pytest.fixture
def audio(tmp_path):
    path = tmp_path / "meeting.m4a"
    path.write_bytes(b"test audio")
    return path


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(cli, "load_config", lambda: {"language": "en"})
    monkeypatch.setattr(cli.secrets, "get_api_key_headless", lambda: "test-key")
    monkeypatch.setattr(SonioxClient, "upload_file", lambda self, audio_path: "file")
    monkeypatch.setattr(SonioxClient, "create_transcription", lambda self, file_id, **kw: "job")
    monkeypatch.setattr(SonioxClient, "poll_status", lambda self, job: None)
    monkeypatch.setattr(SonioxClient, "get_transcript", lambda self, job: [
        {"text": "Hello", "speaker": 1, "start_ms": 0, "language": "en"},
    ])


def test_dry_run_does_not_read_credentials(audio, monkeypatch, capsys):
    monkeypatch.setattr(
        cli.secrets, "get_api_key_headless", lambda: pytest.fail("credential access"))
    assert cli.main([str(audio), "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "dry-run"
    assert not audio.with_suffix(".md").exists()


def test_existing_transcript_is_preserved(audio, monkeypatch):
    output = audio.with_suffix(".md")
    output.write_text("existing")
    monkeypatch.setattr(
        cli.secrets, "get_api_key_headless", lambda: pytest.fail("credential access"))
    assert cli.main([str(audio)]) == 0
    assert output.read_text() == "existing"


def test_success(audio, provider, capsys):
    assert cli.main([str(audio), "--json", "--language", "auto"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "completed"
    assert "Hello" in audio.with_suffix(".md").read_text()
    assert not list(audio.parent.glob("*.transcribing"))


def test_empty_response_not_published(audio, provider, monkeypatch):
    monkeypatch.setattr(SonioxClient, "get_transcript", lambda self, job: [])
    assert cli.main([str(audio)]) == 1
    assert not audio.with_suffix(".md").exists()


def test_failure_redacts_provider_error(audio, provider, monkeypatch, capsys):
    def fail(self, audio_path):
        raise RuntimeError("test-key private provider content")
    monkeypatch.setattr(SonioxClient, "upload_file", fail)
    assert cli.main([str(audio), "--json"]) == 1
    assert "test-key" not in capsys.readouterr().err
    assert not audio.with_suffix(".md").exists()


def test_busy(audio, provider):
    audio.with_suffix(".md.transcribing").touch()
    assert cli.main([str(audio)]) == 1
    assert audio.with_suffix(".md.transcribing").exists()


def test_missing_key(audio, monkeypatch):
    monkeypatch.setattr(cli.secrets, "get_api_key_headless", lambda: "")
    assert cli.main([str(audio)]) == 1
    assert not audio.with_suffix(".md.transcribing").exists()


def test_missing_input(tmp_path):
    assert cli.main([str(tmp_path / "missing.m4a")]) == 2


def test_credential_failure_releases_lock_before_upload(audio, monkeypatch, capsys):
    def unavailable():
        raise cli.secrets.SecretsError("Credential lookup timed out")
    monkeypatch.setattr(cli.secrets, "get_api_key_headless", unavailable)
    monkeypatch.setattr(
        cli, "TranscriptionService", lambda *args, **kw: pytest.fail("upload attempted"))
    assert cli.main([str(audio), "--json"]) == 1
    assert json.loads(capsys.readouterr().err)["message"] == "Credential lookup timed out"
    assert not audio.with_suffix(".md.transcribing").exists()
    assert not list(audio.parent.glob(".transcript-*"))


def test_no_clobber_race(audio, provider, monkeypatch):
    def race(self, job):
        audio.with_suffix(".md").write_text("GUI result")
        return [{"text": "CLI result"}]
    monkeypatch.setattr(SonioxClient, "get_transcript", race)
    assert cli.main([str(audio)]) == 1
    assert audio.with_suffix(".md").read_text() == "GUI result"
