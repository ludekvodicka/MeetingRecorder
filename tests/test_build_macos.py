import importlib.util
from pathlib import Path

import pytest

_BUILD_PATH = Path(__file__).parent.parent / "scripts" / "build.py"
_SPEC = importlib.util.spec_from_file_location("meeting_recorder_build", _BUILD_PATH)
build = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(build)


@pytest.fixture
def macos_package(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    release = tmp_path / "release"
    (dist / "Meeting Recorder.app").mkdir(parents=True)
    release.mkdir()
    commands = []
    monkeypatch.setattr(build, "DIST", dist)
    monkeypatch.setattr(build, "RELEASE", release)
    monkeypatch.setattr(build, "run", lambda command, **kwargs: commands.append(command))
    monkeypatch.setattr(build, "arch_tag", lambda: "arm64")
    return commands, release


def test_macos_package_verifies_app_before_creating_dmg(macos_package, monkeypatch):
    commands, release = macos_package
    monkeypatch.delenv("MACOS_NOTARY_PROFILE", raising=False)

    artifacts = build.package_macos()

    assert commands[0][:4] == ["codesign", "--verify", "--deep", "--strict"]
    assert commands[1][0:2] == ["hdiutil", "create"]
    assert artifacts == [release / f"Meeting-Recorder-{build.__version__}-arm64.dmg"]


def test_macos_package_can_notarize_and_staple(macos_package, monkeypatch):
    commands, _ = macos_package
    monkeypatch.setenv("MACOS_CODESIGN_IDENTITY", "Developer ID Application: Example")
    monkeypatch.setenv("MACOS_NOTARY_PROFILE", "meeting-recorder-notary")

    build.package_macos()

    assert [command[:3] for command in commands[2:]] == [
        ["xcrun", "notarytool", "submit"],
        ["xcrun", "stapler", "staple"],
        ["xcrun", "stapler", "validate"],
        ["spctl", "--assess", "--type"],
    ]


def test_notarization_requires_a_real_signing_identity(macos_package, monkeypatch):
    monkeypatch.delenv("MACOS_CODESIGN_IDENTITY", raising=False)
    monkeypatch.setenv("MACOS_NOTARY_PROFILE", "meeting-recorder-notary")

    with pytest.raises(SystemExit, match="MACOS_CODESIGN_IDENTITY"):
        build.package_macos()

    assert macos_package[0] == []


def test_macos_entitlements_include_audio_input():
    contents = (build.ROOT / "packaging" / "macos.entitlements").read_text(encoding="utf-8")

    assert "com.apple.security.device.audio-input" in contents


@pytest.mark.parametrize(
    ("platform_name", "relative_executable"),
    [
        ("win32", "MeetingRecorder.exe"),
        ("darwin", "Meeting Recorder.app/Contents/MacOS/MeetingRecorder"),
        ("linux", "MeetingRecorder/MeetingRecorder"),
    ],
)
def test_smoke_test_runs_each_platforms_bundled_executable(
    tmp_path, monkeypatch, platform_name, relative_executable
):
    executable = tmp_path / relative_executable
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.touch()
    commands = []
    monkeypatch.setattr(build, "DIST", tmp_path)
    monkeypatch.setattr(build.sys, "platform", platform_name)
    monkeypatch.setattr(build, "run", lambda command, **kwargs: commands.append(command))

    build.smoke()

    assert commands == [[str(executable), "--smoke-test"]]
