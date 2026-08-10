"""Set the application version.

    python scripts/set_version.py 0.2.0

Rewrites the single assignment in audiorecorder/version.py, which pyproject.toml, the
window title, the update check and the release workflow all read, then synchronizes the
browser companion manifest used by Chrome and Edge.
"""

import argparse
import json
import re
import sys
from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parent.parent / "audiorecorder" / "version.py"
EXTENSION_MANIFEST = Path(__file__).resolve().parent.parent / "browser-extension" / "manifest.json"
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def set_version(version):
    if not SEMVER.match(version):
        raise SystemExit(f"Not a semantic version: {version!r}. Expected MAJOR.MINOR.PATCH.")

    text = VERSION_FILE.read_text(encoding="utf-8")
    new_text, count = re.subn(
        r'^__version__ = "[^"]*"$', f'__version__ = "{version}"', text, flags=re.M)
    if count != 1:
        raise SystemExit(f"Expected exactly one __version__ assignment in {VERSION_FILE}, "
                         f"found {count}.")

    manifest = json.loads(EXTENSION_MANIFEST.read_text(encoding="utf-8"))
    manifest["version"] = version
    VERSION_FILE.write_text(new_text, encoding="utf-8", newline="\n")
    EXTENSION_MANIFEST.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"version set to {version}")


def main():
    parser = argparse.ArgumentParser(description="Set the application version.")
    parser.add_argument("version", help="new version, for example 0.2.0")
    set_version(parser.parse_args().version)


if __name__ == "__main__":
    sys.exit(main())
