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
