"""Build and package the application for the platform this runs on.

    python scripts/build.py icons     regenerate the icon files from icon.svg
    python scripts/build.py build     run PyInstaller
    python scripts/build.py smoke     verify the already-built executable can start its runtime
    python scripts/build.py package   build, then wrap the result for distribution

PyInstaller only produces a program directory or a single executable. Turning that into
something a user can download is different on every platform, and that difference lives
here rather than in the workflow file, so a release can be reproduced locally.

Artifacts land in release/. macOS builds are ad-hoc signed by default. Set
MACOS_CODESIGN_IDENTITY to a Developer ID Application identity for distribution, and set
MACOS_NOTARY_PROFILE to a notarytool keychain profile to notarize and staple the DMG.
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
RELEASE = ROOT / "release"
SPEC = ROOT / "MeetingRecorder.spec"
ASSETS = ROOT / "audiorecorder" / "assets"

sys.path.insert(0, str(ROOT))
from audiorecorder.version import __version__  # noqa: E402

PRODUCT = "Meeting Recorder"
ARTIFACT_STEM = "Meeting-Recorder"
ICON_SIZES = (16, 32, 48, 64, 128, 256, 512, 1024)


def arch_tag():
    machine = platform.machine().lower()
    if sys.platform == "win32":
        return "x64" if machine in ("amd64", "x86_64") else machine
    if sys.platform == "darwin":
        return "arm64" if machine in ("arm64", "aarch64") else "x64"
    return machine or "x86_64"


def run(command, **kwargs):
    print(f"$ {' '.join(str(part) for part in command)}", flush=True)
    subprocess.run(command, check=True, cwd=ROOT, **kwargs)


def make_icons():
    """Render icon.svg into the formats each platform's packager insists on."""
    from PIL import Image
    from PyQt6.QtCore import QByteArray
    from PyQt6.QtGui import QImage, QPainter
    from PyQt6.QtSvg import QSvgRenderer

    source = ASSETS / "icon.svg"
    renderer = QSvgRenderer(QByteArray(source.read_bytes()))
    frames = []
    for size in ICON_SIZES:
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        path = ASSETS / f"_icon_{size}.png"
        image.save(str(path), "PNG")
        frames.append(path)

    Image.open(frames[-1]).save(ASSETS / "icon.png")
    Image.open(frames[-1]).save(
        ASSETS / "icon.ico", sizes=[(s, s) for s in (16, 32, 48, 64, 128, 256)])
    Image.open(frames[-1]).save(ASSETS / "icon.icns")
    for path in frames:
        path.unlink()
    print(f"wrote icon.png, icon.ico and icon.icns into {ASSETS}")


def build():
    for directory in (DIST, ROOT / "build"):
        if directory.exists():
            shutil.rmtree(directory)
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", str(SPEC)])


def smoke():
    if sys.platform == "win32":
        executable = DIST / "MeetingRecorder.exe"
    elif sys.platform == "darwin":
        executable = DIST / f"{PRODUCT}.app" / "Contents" / "MacOS" / "MeetingRecorder"
    elif sys.platform == "linux":
        executable = DIST / "MeetingRecorder" / "MeetingRecorder"
    else:
        raise SystemExit(f"Unsupported platform: {sys.platform}")
    if not executable.exists():
        raise SystemExit(f"{executable} is missing, run the build first")
    run([str(executable), "--smoke-test"])


def package_windows():
    executable = DIST / "MeetingRecorder.exe"
    if not executable.exists():
        raise SystemExit(f"{executable} is missing, run the build first")
    target = RELEASE / f"{ARTIFACT_STEM}-{__version__}-{arch_tag()}.exe"
    shutil.copy2(executable, target)

    archive = RELEASE / f"{ARTIFACT_STEM}-{__version__}-{arch_tag()}-portable.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(executable, executable.name)
        zf.write(ROOT / "README.md", "README.md")
        zf.write(ROOT / "LICENSE", "LICENSE")
    return [target, archive]


def package_macos():
    app = DIST / f"{PRODUCT}.app"
    if not app.exists():
        raise SystemExit(f"{app} is missing, run the build first")
    notary_profile = os.environ.get("MACOS_NOTARY_PROFILE")
    if notary_profile and not os.environ.get("MACOS_CODESIGN_IDENTITY"):
        raise SystemExit(
            "MACOS_NOTARY_PROFILE requires MACOS_CODESIGN_IDENTITY; Apple will not "
            "notarize an ad-hoc signed application."
        )
    target = RELEASE / f"{ARTIFACT_STEM}-{__version__}-{arch_tag()}.dmg"
    target.unlink(missing_ok=True)
    run(["codesign", "--verify", "--deep", "--strict", str(app)])
    run(["hdiutil", "create", "-volname", PRODUCT, "-srcfolder", str(app),
         "-ov", "-format", "UDZO", str(target)])
    if notary_profile:
        run([
            "xcrun", "notarytool", "submit", str(target),
            "--keychain-profile", notary_profile, "--wait",
        ])
        run(["xcrun", "stapler", "staple", str(target)])
        run(["xcrun", "stapler", "validate", str(target)])
        run([
            "spctl", "--assess", "--type", "open", "--context",
            "context:primary-signature", "--verbose=2", str(target),
        ])
    return [target]


def package_linux():
    program = DIST / "MeetingRecorder"
    if not program.exists():
        raise SystemExit(f"{program} is missing, run the build first")

    tarball = RELEASE / f"{ARTIFACT_STEM}-{__version__}-linux-{arch_tag()}.tar.gz"
    tarball.unlink(missing_ok=True)
    run(["tar", "-czf", str(tarball), "-C", str(DIST), "MeetingRecorder"])

    appdir = DIST / "MeetingRecorder.AppDir"
    if appdir.exists():
        shutil.rmtree(appdir)
    shutil.copytree(program, appdir / "usr" / "bin")
    shutil.copy2(ROOT / "packaging" / "appimage" / "meeting-recorder.desktop",
                 appdir / "meeting-recorder.desktop")
    shutil.copy2(ASSETS / "icon.png", appdir / "meeting-recorder.png")
    apprun = appdir / "AppRun"
    apprun.write_text(
        '#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\n'
        'exec "$HERE/usr/bin/MeetingRecorder" "$@"\n', encoding="utf-8", newline="\n")
    apprun.chmod(0o755)

    tool = shutil.which("appimagetool") or shutil.which("appimagetool-x86_64.AppImage")
    if tool is None:
        raise SystemExit(
            "appimagetool was not found on PATH. Install it, or download "
            "appimagetool-x86_64.AppImage and put it on PATH.")
    target = RELEASE / f"{ARTIFACT_STEM}-{__version__}-{arch_tag()}.AppImage"
    target.unlink(missing_ok=True)
    run([tool, str(appdir), str(target)])
    return [tarball, target]


def package():
    build()
    smoke()
    RELEASE.mkdir(exist_ok=True)
    if sys.platform == "win32":
        artifacts = package_windows()
    elif sys.platform == "darwin":
        artifacts = package_macos()
    elif sys.platform == "linux":
        artifacts = package_linux()
    else:
        raise SystemExit(f"Unsupported platform: {sys.platform}")

    print(f"\n{PRODUCT} {__version__}:")
    for artifact in artifacts:
        print(f"  {artifact.relative_to(ROOT)}  ({artifact.stat().st_size / 1e6:.1f} MB)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["icons", "build", "smoke", "package"])
    command = parser.parse_args().command
    if command == "icons":
        make_icons()
    elif command == "build":
        build()
    elif command == "smoke":
        smoke()
    elif command == "package":
        package()
    else:
        raise SystemExit(f"Unknown command: {command}")


if __name__ == "__main__":
    main()
