from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QApplication

from audiorecorder import readiness


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize(
    "code,expected",
    [(0, "Ready"), (2, "Missing"), (3, "Keychain denied"), (4, "Unknown"), (99, "Unknown")],
)
def test_windowed_child_reports_status_without_stdout(app, monkeypatch, code, expected):
    monkeypatch.setattr(
        readiness.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=code, stdout=""),
    )
    probe = readiness.CredentialProbe()
    statuses = []
    probe.finished.connect(statuses.append)
    probe._run()
    assert statuses == [expected]


def test_automatic_audio_readiness_requires_matching_success_and_frames():
    from audiorecorder.readiness import capture_readiness

    config = {"output_dir": "/audio", "system_source_name": None, "mic_source_name": None}
    test = {
        "runtime": "build",
        "status": "Recording saved; encoded audio validated",
        "config": config,
        "frames": {"mic": 100, "system": 100},
        "system": "Native",
    }
    assert capture_readiness(config, test, "build", platform_name="darwin", microphone="Granted")[0]
    for changed, runtime, permission in (
        (None, "build", "Granted"),
        (test, "new-build", "Granted"),
        (test, "build", "Denied"),
        ({**test, "frames": {"mic": 100, "system": 0}}, "build", "Granted"),
        ({**test, "status": "Partial recording saved"}, "build", "Granted"),
    ):
        assert not capture_readiness(
            config, changed, runtime, platform_name="darwin", microphone=permission
        )[0]
    assert not capture_readiness(
        {**config, "mic_source_name": "new mic"},
        test,
        "build",
        platform_name="darwin",
        microphone="Granted",
    )[0]
