from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QCoreApplication

from audiorecorder.transcription import cleanup


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QCoreApplication.instance() or QCoreApplication([])


def test_cleanup_uses_resolved_claude_path(tmp_path, monkeypatch):
    transcript = tmp_path / "meeting.md"
    transcript.write_text("Transcript", encoding="utf-8")
    command = []

    monkeypatch.setattr(cleanup, "find_executable", lambda name: "/opt/homebrew/bin/claude")

    def run(arguments, **kwargs):
        command.extend(arguments)
        (tmp_path / "meeting.summary.md").write_text("Summary", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cleanup.subprocess, "run", run)
    completed = []
    worker = cleanup.CleanupWorker(
        str(transcript), "Read {transcript_name}; write {summary_name}"
    )
    worker.completed.connect(completed.append)

    worker.run()

    assert command[0] == "/opt/homebrew/bin/claude"
    assert completed == [str(tmp_path / "meeting.summary.md")]


def test_cleanup_reports_a_missing_cli_before_starting_subprocess(tmp_path, monkeypatch):
    transcript = tmp_path / "meeting.md"
    transcript.write_text("Transcript", encoding="utf-8")
    monkeypatch.setattr(cleanup, "find_executable", lambda name: None)
    monkeypatch.setattr(
        cleanup.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("subprocess should not be started"),
    )
    errors = []
    worker = cleanup.CleanupWorker(
        str(transcript), "Read {transcript_name}; write {summary_name}"
    )
    worker.error.connect(errors.append)

    worker.run()

    assert len(errors) == 1
    assert "standard per-user locations" in errors[0]
