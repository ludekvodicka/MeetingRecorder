"""PID-bound code-signing checks for native macOS meeting providers."""

import ctypes
import errno
import sys
from dataclasses import dataclass

from audiorecorder.meetings.native_profiles import SIGNING_POLICIES

# Public XNU csops operations and code-signing flags. These values are stable ABI:
# https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/codesign.h
_CS_OPS_STATUS = 0
_CS_OPS_IDENTITY = 11
_CS_OPS_TEAMID = 14
_CS_OPS_VALIDATION_CATEGORY = 17
_CS_VALID = 0x00000001
_CS_ADHOC = 0x00000002
_CS_SIGNED = 0x20000000
_CS_GENERIC_BLOB_HEADER = 8


@dataclass(frozen=True)
class ProcessCodeIdentity:
    flags: int
    identifier: str
    team_id: str
    validation_category: int


def verify_running_process(pid, bundle, *, checker=None, platform_name=None):
    """Return the fixed signer identity only for a trusted matching running PID."""
    policy = SIGNING_POLICIES.get(bundle)
    platform_name = platform_name or sys.platform
    if platform_name != "darwin" or not policy or type(pid) is not int or pid <= 0:
        return ""
    checker = checker or _process_code_identity
    try:
        identity = checker(pid)
        trusted = (
            identity.flags & _CS_VALID
            and identity.flags & _CS_SIGNED
            and not identity.flags & _CS_ADHOC
            and identity.identifier == bundle
            and identity.team_id == policy["team_id"]
            and identity.validation_category in policy["validation_categories"]
        )
        return policy["identity"] if trusted else ""
    except Exception:
        return ""


def _process_code_identity(pid, *, csops=None):
    """Read the kernel's code-signing facts for the already-running PID.

    Unlike validating an application path, these facts stay bound to the process
    whose Accessibility tree will be read, closing replacement and path races.
    """
    if csops is None:
        libc = ctypes.CDLL(None, use_errno=True)
        csops = libc.csops
        csops.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_void_p, ctypes.c_size_t]
        csops.restype = ctypes.c_int

    return ProcessCodeIdentity(
        flags=_read_uint32(csops, pid, _CS_OPS_STATUS),
        identifier=_read_code_string(csops, pid, _CS_OPS_IDENTITY, 256),
        team_id=_read_code_string(csops, pid, _CS_OPS_TEAMID, 72, optional=True),
        validation_category=_read_uint32(csops, pid, _CS_OPS_VALIDATION_CATEGORY),
    )


def _call_csops(csops, pid, operation, buffer):
    ctypes.set_errno(0)
    if csops(pid, operation, ctypes.byref(buffer), ctypes.sizeof(buffer)) != 0:
        error = ctypes.get_errno() or errno.EIO
        raise OSError(error, f"csops operation {operation} failed")


def _read_uint32(csops, pid, operation):
    value = ctypes.c_uint32()
    _call_csops(csops, pid, operation, value)
    return value.value


def _read_code_string(csops, pid, operation, size, *, optional=False):
    buffer = ctypes.create_string_buffer(size)
    try:
        _call_csops(csops, pid, operation, buffer)
    except OSError as error:
        if optional and error.errno == errno.ENOENT:
            return ""
        raise
    raw = buffer.raw
    length = int.from_bytes(raw[4:8], "big")
    if length <= _CS_GENERIC_BLOB_HEADER or length > size:
        raise ValueError("invalid csops code-signing string length")
    value = raw[_CS_GENERIC_BLOB_HEADER:length]
    if not value.endswith(b"\0") or b"\0" in value[:-1]:
        raise ValueError("invalid csops code-signing string")
    return value[:-1].decode("ascii")
