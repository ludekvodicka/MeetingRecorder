import sys

import pytest

from audiorecorder import app


def test_runtime_smoke_checks_assets_and_current_platform():
    app._runtime_smoke_test()


def test_runtime_smoke_rejects_unknown_platform():
    with pytest.raises(RuntimeError, match="unsupported smoke-test platform"):
        app._runtime_smoke_test("plan9")


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS frameworks are host-only")
def test_macos_runtime_smoke_creates_a_native_callback_queue():
    app._runtime_smoke_test("darwin")


def test_a_failed_smoke_test_exits_with_a_code_instead_of_a_traceback(monkeypatch):
    """A windowed build would show an uncaught traceback in a dialog and hang the job."""
    monkeypatch.setattr(app.sys, "argv", ["MeetingRecorder", "--smoke-test"])
    monkeypatch.setattr(app, "_runtime_smoke_test", lambda: (_ for _ in ()).throw(
        RuntimeError("bundled application assets are missing")))
    with pytest.raises(SystemExit) as exit_info:
        app.main()
    assert exit_info.value.code == 1
