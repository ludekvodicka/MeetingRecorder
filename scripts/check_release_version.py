"""Refuse a release whose tag does not match the version in the source.

Run by the release workflow before anything is built. A tag of v0.2.0 must find 0.2.0 in
audiorecorder/version.py, otherwise the published installers would carry a version nobody
can trace back to a commit. Outside a tag build it only checks that the version is a valid
semantic version, which is what the workflow_dispatch build-only path needs.
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from audiorecorder.version import __version__  # noqa: E402

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def check(ref_type, ref_name):
    if not SEMVER.match(__version__):
        return f"audiorecorder/version.py holds {__version__!r}, which is not MAJOR.MINOR.PATCH."

    if ref_type != "tag":
        print(f"version {__version__} (no tag to compare against)")
        return None

    if not ref_name.startswith("v"):
        return f"Tag {ref_name!r} does not start with 'v'."

    tagged = ref_name[1:]
    if tagged != __version__:
        return (f"Tag {ref_name} does not match audiorecorder/version.py ({__version__}). "
                f"Run: python scripts/set_version.py {tagged}")

    print(f"version {__version__} matches tag {ref_name}")
    return None


def main():
    problem = check(os.environ.get("GITHUB_REF_TYPE", ""), os.environ.get("GITHUB_REF_NAME", ""))
    if problem:
        print(problem, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
