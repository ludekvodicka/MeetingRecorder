# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for all three platforms.

Windows gets a single executable, macOS an application bundle, and Linux a program
directory that scripts/build.py turns into an AppImage. Nothing secret is bundled: the
Soniox key lives in the keyring, and the settings live in the user's config directory.
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

sys.path.insert(0, SPECPATH)
from audiorecorder.version import __version__

ASSETS = Path(SPECPATH) / "audiorecorder" / "assets"

datas = [(str(ASSETS), "audiorecorder/assets")]
binaries = []
# WASAPI loopback is a Windows-only dependency, and PyInstaller must not look for it
# anywhere else.
hiddenimports = ["pyaudiowpatch"] if sys.platform == "win32" else []

# Collected wholesale because Qt loads its image, SVG and multimedia plugins by path at
# runtime, which static analysis does not see.
qt_datas, qt_binaries, qt_hiddenimports = collect_all("PyQt6")
datas += qt_datas
binaries += qt_binaries
hiddenimports += qt_hiddenimports

a = Analysis(
    [str(Path(SPECPATH) / "audiorecorder" / "__main__.py")],
    pathex=[SPECPATH],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

if sys.platform == "win32":
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name="MeetingRecorder",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=str(ASSETS / "icon.ico"),
    )
elif sys.platform == "darwin":
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name="MeetingRecorder",
        debug=False,
        strip=False,
        upx=True,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    collected = COLLECT(exe, a.binaries, a.datas, strip=False, upx=True, name="MeetingRecorder")
    app = BUNDLE(
        collected,
        name="Meeting Recorder.app",
        icon=str(ASSETS / "icon.icns"),
        bundle_identifier="io.github.ludekvodicka.audiorecorder",
        version=__version__,
        info_plist={
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            # Without this key macOS denies the microphone without even showing a prompt.
            "NSMicrophoneUsageDescription":
                "Meeting Recorder records your microphone for meetings and dictation.",
            "NSHighResolutionCapable": True,
        },
    )
elif sys.platform == "linux":
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name="MeetingRecorder",
        debug=False,
        strip=False,
        upx=True,
        console=False,
        disable_windowed_traceback=False,
    )
    collected = COLLECT(exe, a.binaries, a.datas, strip=False, upx=True, name="MeetingRecorder")
else:
    raise SystemExit(f"Unsupported platform: {sys.platform}")
