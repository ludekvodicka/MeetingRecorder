import os
from pathlib import Path

from audiorecorder.platform_tools import executable_search_path, find_executable


def test_macos_desktop_searches_shell_homebrew_and_user_locations(tmp_path):
    path = executable_search_path(
        "darwin",
        home=tmp_path,
        environment={"PATH": os.pathsep.join(["/custom/bin", "/usr/bin"])},
    ).split(os.pathsep)

    assert path[:2] == ["/custom/bin", "/usr/bin"]
    assert "/opt/homebrew/bin" in path
    assert "/usr/local/bin" in path
    assert str(tmp_path / ".local" / "bin") in path
    assert len(path) == len(set(path))


def test_non_macos_search_path_is_not_modified():
    inherited = os.pathsep.join(["/custom/bin", "/usr/bin"])
    assert executable_search_path("linux", environment={"PATH": inherited}) == inherited


def test_find_executable_locates_a_user_installed_tool(tmp_path):
    # Windows finds executables by PATHEXT, not by the executable bit.
    binary = tmp_path / ".local" / "bin" / ("claude.exe" if os.name == "nt" else "claude")
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)

    found = find_executable(
        "claude",
        "darwin",
        home=tmp_path,
        environment={"PATH": "/usr/bin"},
    )

    assert os.path.normcase(found) == os.path.normcase(str(binary))


def test_find_executable_returns_none_for_missing_tool(tmp_path):
    assert find_executable(
        "does-not-exist",
        "darwin",
        home=Path(tmp_path),
        environment={"PATH": ""},
    ) is None
