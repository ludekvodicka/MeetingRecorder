"""macOS launch-at-login integration through Apple's Service Management API."""

import sys


class LoginItemUnavailable(RuntimeError):
    """The current platform or runtime cannot manage the main-app login item."""


def _service(platform_name=None):
    platform_name = sys.platform if platform_name is None else platform_name
    if platform_name != "darwin":
        raise LoginItemUnavailable("Launch at login is available only on macOS.")
    try:
        import ServiceManagement
    except ImportError as err:
        raise LoginItemUnavailable(
            "The macOS Service Management framework is unavailable.") from err
    return ServiceManagement.SMAppService.mainAppService()


def login_item_registered(platform_name=None):
    """Return whether the main app is registered, including pending user approval."""
    service = _service(platform_name)
    try:
        status = service.status()
    except Exception as err:
        raise RuntimeError(f"Could not read Launch at login status: {err}") from err
    # SMAppServiceStatus: 1 = enabled, 2 = registered but requires user approval.
    return status in (1, 2)


def set_login_item_enabled(enabled, platform_name=None):
    """Register or unregister the main app and raise a readable error on failure."""
    service = _service(platform_name)
    operation = (
        service.registerAndReturnError_
        if enabled else service.unregisterAndReturnError_
    )
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
