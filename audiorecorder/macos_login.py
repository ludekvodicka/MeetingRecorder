"""macOS launch-at-login integration through Apple's Service Management API."""

import sys
from dataclasses import dataclass


class LoginItemUnavailable(RuntimeError):
    """The current platform or runtime cannot manage the main-app login item."""


def _service(platform_name=None):
    platform_name = sys.platform if platform_name is None else platform_name
    if platform_name != "darwin":
        raise LoginItemUnavailable("Launch at login is available only on macOS.")
    if not getattr(sys, "frozen", False):
        raise LoginItemUnavailable("Register the installed application, not the source checkout.")
    from pathlib import Path

    from Foundation import NSBundle

    bundle = Path(str(NSBundle.mainBundle().bundlePath())).resolve()
    if not any(
        bundle.is_relative_to(root)
        for root in (Path("/Applications"), Path.home() / "Applications")
    ):
        raise LoginItemUnavailable("Install the application in Applications before registering it.")
    try:
        import ServiceManagement
    except ImportError as err:
        raise LoginItemUnavailable(
            "The macOS Service Management framework is unavailable."
        ) from err
    return ServiceManagement.SMAppService.mainAppService()


@dataclass(frozen=True)
class LoginStatus:
    state: str
    error: str = ""

    @property
    def enabled(self):
        return self.state == "enabled"


def login_item_status(platform_name=None):
    try:
        status = int(_service(platform_name).status())
        return LoginStatus(
            {0: "not registered", 1: "enabled", 2: "requires approval", 3: "not found"}.get(
                status, "unknown"
            )
        )
    except Exception as error:
        return LoginStatus("unavailable", str(error))


def login_item_registered(platform_name=None):
    """Compatibility query; pending approval is not an enabled login item."""
    status = login_item_status(platform_name)
    if status.error:
        raise LoginItemUnavailable(status.error)
    return status.enabled


def set_login_item_enabled(enabled, platform_name=None):
    """Register or unregister the main app and raise a readable error on failure."""
    service = _service(platform_name)
    operation = service.registerAndReturnError_ if enabled else service.unregisterAndReturnError_
    try:
        result = operation(None)
    except Exception as err:
        raise RuntimeError(f"Could not update Launch at login: {err}") from err
    if isinstance(result, tuple):
        succeeded, error = result
    else:
        succeeded, error = result, None
    if not succeeded:
        description = getattr(error, "localizedDescription", None)
        message = description() if description else str(error or "unknown error")
        raise RuntimeError(f"Could not update Launch at login: {message}")
    return True
