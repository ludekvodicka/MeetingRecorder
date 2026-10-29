"""Capture-test evidence is bound to the build, without reading the whole executable."""

import os
import sys

import pytest

from audiorecorder.audio import runtime


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    executable = tmp_path / "MeetingRecorder.exe"
    executable.write_bytes(b"build one")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    runtime.capture_runtime.cache_clear()
    yield executable
    runtime.capture_runtime.cache_clear()


def test_a_packaged_build_is_identified_without_reading_it(frozen, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the executable must not be read")

    monkeypatch.setattr(runtime.Path, "read_bytes", refuse)
    monkeypatch.setattr(runtime.Path, "open", refuse)
    assert runtime.capture_runtime()


def test_a_replaced_executable_changes_the_identity(frozen):
    first = runtime.capture_runtime()
    frozen.write_bytes(b"build two, longer")
    os.utime(frozen, ns=(1, 1))
    runtime.capture_runtime.cache_clear()
    assert runtime.capture_runtime() != first


def test_a_missing_executable_has_no_identity(frozen):
    frozen.unlink()
    assert runtime.capture_runtime() == ""
