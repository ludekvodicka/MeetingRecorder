"""Tell the user when a newer release exists.

The application does not update itself. It asks the GitHub Releases API once at startup,
on a worker thread, and shows a line in the status bar if there is something newer.
Downloading and installing stays a deliberate act by the user.

Every failure is silent by design. A missing network, a rate limit or a renamed API is not
worth interrupting a recording for.
"""

import re

import requests

RELEASES_API = "https://api.github.com/repos/ludekvodicka/AudioRecorder/releases/latest"
RELEASES_PAGE = "https://github.com/ludekvodicka/AudioRecorder/releases/latest"
TIMEOUT_SECONDS = 5

_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


def _parse(version):
    match = _SEMVER.match((version or "").strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def is_newer(candidate, current):
    """True only when both versions parse and the candidate is genuinely ahead."""
    left, right = _parse(candidate), _parse(current)
    if left is None or right is None:
        return False
    return left > right


def fetch_latest_version():
    """The tag of the newest release, or None if it could not be determined."""
    try:
        response = requests.get(
            RELEASES_API,
            headers={"Accept": "application/vnd.github+json"},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        tag = response.json().get("tag_name", "")
    except Exception:
        return None
    return tag.lstrip("v") or None
