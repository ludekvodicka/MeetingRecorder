from types import SimpleNamespace

import pytest

from audiorecorder import macos_login


class Service:
    def __init__(self, status=0, register=(True, None), unregister=(True, None)):
        self._status = status
        self._register = register
        self._unregister = unregister

    def status(self):
        return self._status

    def registerAndReturnError_(self, error):
        return self._register

    def unregisterAndReturnError_(self, error):
        return self._unregister


def test_login_item_is_macos_only():
    with pytest.raises(macos_login.LoginItemUnavailable, match="only on macOS"):
        macos_login.login_item_registered("linux")


def test_enabled_status_is_read_from_service(monkeypatch):
    monkeypatch.setattr(macos_login, "_service", lambda platform_name=None: Service(status=1))
    assert macos_login.login_item_registered()


def test_pending_approval_is_still_registered(monkeypatch):
    monkeypatch.setattr(macos_login, "_service", lambda platform_name=None: Service(status=2))
    assert macos_login.login_item_registered()


def test_registration_and_unregistration_use_the_main_app_service(monkeypatch):
    calls = []
    service = Service()
    service.registerAndReturnError_ = lambda error: calls.append("register") or (True, None)
    service.unregisterAndReturnError_ = lambda error: calls.append("unregister") or (True, None)
    monkeypatch.setattr(macos_login, "_service", lambda platform_name=None: service)

    macos_login.set_login_item_enabled(True)
    macos_login.set_login_item_enabled(False)

    assert calls == ["register", "unregister"]


def test_registration_error_is_readable(monkeypatch):
    error = SimpleNamespace(localizedDescription=lambda: "approval required")
    monkeypatch.setattr(
        macos_login,
        "_service",
        lambda platform_name=None: Service(register=(False, error)),
    )
    with pytest.raises(RuntimeError, match="approval required"):
        macos_login.set_login_item_enabled(True)
