"""Single source of truth for the application version.

`scripts/set_version.py` rewrites the assignment below, `pyproject.toml` reads it as the
dynamic project version, and `scripts/check_release_version.py` compares it against the
git tag during a release build. Nothing else may hardcode a version string.
"""

__version__ = "0.1.1"
