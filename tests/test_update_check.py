import pytest

from audiorecorder import update_check


class TestIsNewer:
    @pytest.mark.parametrize("candidate,current", [
        ("0.2.0", "0.1.0"),
        ("1.0.0", "0.9.9"),
        ("0.1.1", "0.1.0"),
        ("0.10.0", "0.9.0"),
    ])
    def test_newer(self, candidate, current):
        assert update_check.is_newer(candidate, current)

    @pytest.mark.parametrize("candidate,current", [
        ("0.1.0", "0.1.0"),
        ("0.1.0", "0.2.0"),
        ("0.9.0", "0.10.0"),
    ])
    def test_not_newer(self, candidate, current):
        assert not update_check.is_newer(candidate, current)

    def test_leading_v_is_accepted(self):
        assert update_check.is_newer("v0.2.0", "0.1.0")

    @pytest.mark.parametrize("candidate,current", [
        ("", "0.1.0"),
        (None, "0.1.0"),
        ("garbage", "0.1.0"),
        ("0.2.0", "not-a-version"),
        ("0.2", "0.1.0"),
    ])
    def test_unparseable_never_claims_an_update(self, candidate, current):
        """A malformed version must not nag the user, and must not raise either."""
        assert not update_check.is_newer(candidate, current)

    def test_prerelease_suffix_compares_on_the_numbers(self):
        assert update_check.is_newer("0.2.0-rc1", "0.1.0")


class FakeResponse:
    def __init__(self, payload=None, error=None):
        self._payload = payload or {}
        self._error = error

    def raise_for_status(self):
        if self._error:
            raise self._error

    def json(self):
        return self._payload


class TestFetchLatestVersion:
    def test_returns_the_tag_without_its_v(self, monkeypatch):
        monkeypatch.setattr(update_check.requests, "get",
                            lambda *a, **k: FakeResponse({"tag_name": "v0.3.0"}))
        assert update_check.fetch_latest_version() == "0.3.0"

    def test_network_failure_is_silent(self, monkeypatch):
        def boom(*args, **kwargs):
            raise OSError("no network")

        monkeypatch.setattr(update_check.requests, "get", boom)
        assert update_check.fetch_latest_version() is None

    def test_http_error_is_silent(self, monkeypatch):
        monkeypatch.setattr(update_check.requests, "get",
                            lambda *a, **k: FakeResponse(error=RuntimeError("404")))
        assert update_check.fetch_latest_version() is None

    def test_missing_tag_gives_none(self, monkeypatch):
        monkeypatch.setattr(update_check.requests, "get", lambda *a, **k: FakeResponse({}))
        assert update_check.fetch_latest_version() is None

    def test_a_request_is_made_with_a_timeout(self, monkeypatch):
        seen = {}

        def capture(url, **kwargs):
            seen["url"] = url
            seen["timeout"] = kwargs.get("timeout")
            return FakeResponse({"tag_name": "v1.0.0"})

        monkeypatch.setattr(update_check.requests, "get", capture)
        update_check.fetch_latest_version()
        assert seen["url"] == update_check.RELEASES_API
        assert seen["timeout"] == update_check.TIMEOUT_SECONDS
