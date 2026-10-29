import ssl
from unittest.mock import Mock

import certifi
import pytest

from audiorecorder.dictation.streaming import SonioxStreamingSession


@pytest.mark.parametrize("override", ["SSL_CERT_FILE", "WEBSOCKET_CLIENT_CA_BUNDLE"])
def test_streaming_uses_only_bundled_roots_with_verification(monkeypatch, tmp_path, override):
    monkeypatch.setenv(override, str(tmp_path / "stale-system-store.pem"))
    default_roots = Mock(side_effect=AssertionError("Must not add system roots"))
    monkeypatch.setattr(ssl.SSLContext, "load_default_certs", default_roots)
    thread = Mock()
    monkeypatch.setattr("audiorecorder.dictation.streaming.threading.Thread", thread)
    session = SonioxStreamingSession("unused")
    session._opened.set()

    session.connect()

    context = thread.call_args.kwargs["kwargs"]["sslopt"]["context"]
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
    expected = ssl.create_default_context(cafile=certifi.where())
    roots = set(context.get_ca_certs(binary_form=True))
    assert roots == set(expected.get_ca_certs(binary_form=True))
    default_roots.assert_not_called()
    thread.return_value.start.assert_called_once()


def test_missing_bundle_fails_without_starting_a_connection(monkeypatch, tmp_path):
    monkeypatch.setattr(certifi, "where", lambda: str(tmp_path / "missing.pem"))
    thread = Mock()
    monkeypatch.setattr("audiorecorder.dictation.streaming.threading.Thread", thread)
    session = SonioxStreamingSession("unused")

    with pytest.raises(FileNotFoundError):
        session.connect()

    thread.assert_not_called()
