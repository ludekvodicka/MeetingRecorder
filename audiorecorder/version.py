"""Single source of truth for the application version.

`scripts/set_version.py` rewrites the assignment below and synchronizes packaged companion
metadata, `pyproject.toml` reads it as the dynamic project version, and the release check
compares every copy against the git tag.
"""

__version__ = "0.3.3"
