"""Locate optional command-line tools from both shells and desktop app bundles."""

import os
import shutil
import sys
from pathlib import Path


def executable_search_path(platform_name=None, home=None, environment=None):
    """Return a PATH that includes conventional user-install locations.

    Apps launched from Finder do not inherit the interactive shell PATH on macOS. Keep the
    inherited entries first, then add the locations used by Homebrew and common per-user
    installers without invoking a shell or reading shell startup files.
    """
    if platform_name is None:
        platform_name = sys.platform
    home = Path.home() if home is None else Path(home)
    if environment is None:
        environment = os.environ

    entries = [entry for entry in environment.get("PATH", "").split(os.pathsep) if entry]
    if platform_name == "darwin":
        entries.extend([
            "/opt/homebrew/bin",
            "/usr/local/bin",
            str(home / ".local" / "bin"),
            str(home / ".claude" / "local"),
            str(home / ".npm-global" / "bin"),
        ])

    # Preserve priority while avoiding redundant filesystem probes.
    return os.pathsep.join(dict.fromkeys(entries))


def find_executable(name, platform_name=None, home=None, environment=None):
    return shutil.which(
        name,
        path=executable_search_path(platform_name, home, environment),
    )
