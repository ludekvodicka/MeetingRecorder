import sys
from pathlib import Path

from audiorecorder.meetings import browser_companion


def test_source_companion_contains_the_manifest():
    assert browser_companion.companion_available()


def test_frozen_runtime_uses_the_pyinstaller_root(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert browser_companion.companion_directory() == tmp_path / "browser-extension"


def test_missing_companion_is_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(
        browser_companion,
        "companion_directory",
        lambda: Path(tmp_path / "missing"),
    )
    assert not browser_companion.companion_available()
