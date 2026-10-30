import ctypes
import json
import os
import socket
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from audiorecorder.meetings import browser_agent as browser
from audiorecorder.meetings.detection import CombinedMeetingDetector, MeetingPromptPolicy

MEET = "meetings.web.googleMeet"
ZOOM = "meetings.web.zoom"


def profile(instance="work", *, access=True, enabled=(MEET,), version=4):
    return {"instanceId": instance, "label": instance, "protocolVersion": version,
            "agentAccess": access, "tools": [
                {"id": tool, "enabled": tool in enabled} for tool in browser.MEETING_TOOLS
            ]}


def response(instance="work", **kwargs):
    return {"ok": True, "profile": profile(instance, **kwargs), "value": {"meetings": [
        {"key": "meet.google.com/abc-defg-hij", "title": "Weekly meeting", "tabId": 4},
    ]}}


def wait_for(predicate):
    deadline = time.monotonic() + 2
    while not predicate():
        assert time.monotonic() < deadline, "Background operation did not finish"
        time.sleep(0.005)


@contextmanager
def running(detector):
    detector.start()
    try:
        wait_for(lambda: detector.status().scanned)
        yield detector
    finally:
        detector.stop()
        detector._thread.join(2)
        assert not detector._thread.is_alive()


def test_windows_discovery_uses_the_pipe_namespace(monkeypatch):
    seen = []

    def listdir(directory):
        seen.append(directory)
        return ["other", "browser-agent-b", "browser-agent-a"]

    monkeypatch.setattr(browser.os, "listdir", listdir)
    assert browser.discover_endpoints("win32") == (
        browser.PIPE_DIRECTORY + "browser-agent-a", browser.PIPE_DIRECTORY + "browser-agent-b",
    )
    assert seen == [browser.PIPE_DIRECTORY]


def test_macos_discovery_uses_the_users_run_directory(tmp_path):
    assert browser.discover_endpoints("darwin", tmp_path) == ()
    directory = tmp_path / ".browser-agent/run"
    directory.mkdir(parents=True)
    (directory / "browser-agent-a.sock").touch()
    (directory / "other.sock").touch()
    (directory / "browser-agent-b.txt").touch()
    assert browser.discover_endpoints("darwin", tmp_path) == (
        str(directory / "browser-agent-a.sock"),
    )
    assert browser.discover_endpoints("linux", tmp_path) == ()


def test_multiple_profiles_only_call_enabled_meeting_tools_with_stable_keys():
    calls = []

    def transport(endpoint, message, stopped):
        calls.append((endpoint, message))
        return response(endpoint, enabled=(MEET,) if endpoint == "work" else (ZOOM,))

    detector = browser.BrowserAgentDetector(lambda: ("work", "home"), transport, poll_seconds=.02)
    with running(detector):
        first = detector.scan()
        assert {c.key for c in first} == {
            "browser:work:meet.google.com/abc-defg-hij",
            "browser:home:meet.google.com/abc-defg-hij",
        }
        assert [c.app_name for c in first] == ["Google Meet", "Zoom Web"]
        assert all(c.source == "browser-agent" for c in first)
        assert "2 profiles, 2 meeting tools" in detector.status().summary
        wait_for(lambda: len(calls) >= 8)
        assert detector.scan() == first
        for endpoint, message in calls:
            if message["op"] == "call":
                assert message == {"op": "call", "tool": MEET if endpoint == "work" else ZOOM,
                                   "caller": "meeting-recorder"}
            elif message["op"] == "describe":
                assert message == {"op": "describe"}
            else:
                raise AssertionError(message)


@pytest.mark.parametrize("kwargs", [{"access": False}, {"enabled": ()}, {"version": 3}])
def test_disabled_access_tools_and_incompatible_versions_do_not_call(kwargs):
    calls = []

    def transport(endpoint, message, stopped):
        calls.append(message)
        return response(**kwargs)

    with running(browser.BrowserAgentDetector(lambda: ("work",), transport)) as detector:
        assert detector.scan() == []
        assert calls == [{"op": "describe"}]
        if kwargs.get("version") == 3 or kwargs.get("access") is False:
            assert "not connected" in detector.status().summary


@pytest.mark.parametrize("failure", [TimeoutError("timeout"), OSError("disconnected"),
                                     {"ok": False, "code": "permission_missing"},
                                     response(version=3), {"ok": True},
                                     {**response(), "value": {"meetings": [None]}}])
def test_failed_calls_clear_candidates_and_do_not_prevent_other_profiles(failure):
    failing = threading.Event()

    def transport(endpoint, message, stopped):
        if failing.is_set() and endpoint == "bad" and message["op"] == "call":
            if isinstance(failure, Exception):
                raise failure
            return failure
        return response(endpoint)

    detector = browser.BrowserAgentDetector(lambda: ("bad", "good"), transport, poll_seconds=.02)
    with running(detector):
        assert len(detector.scan()) == 2
        failing.set()
        wait_for(lambda: "not connected" in detector.status().summary)
        assert [candidate.key for candidate in detector.scan()] == [
            "browser:good:meet.google.com/abc-defg-hij",
        ]


def test_scan_is_nonblocking_stop_clears_and_late_results_cannot_repopulate():
    entered = threading.Event()
    released = threading.Event()

    def transport(endpoint, message, stopped):
        entered.set()
        assert released.wait(2)
        return response()

    detector = browser.BrowserAgentDetector(lambda: ("work",), transport)
    detector.start()
    assert entered.wait(2)
    try:
        started = time.monotonic()
        assert detector.scan() == []
        detector.stop()
        assert time.monotonic() - started < .1
        assert detector.status().summary == "Browser Agent: meeting detection off"
    finally:
        released.set()
        detector._thread.join(2)
    assert not detector._thread.is_alive()
    assert detector.scan() == []
    assert not detector.status().active


def test_a_slow_profile_cannot_delay_another_profiles_meeting():
    slow_entered = threading.Event()
    release = threading.Event()

    def transport(endpoint, message, stopped):
        if endpoint == "slow":
            slow_entered.set()
            assert release.wait(2)
        return response(endpoint)

    detector = browser.BrowserAgentDetector(lambda: ("slow", "fast"), transport)
    detector.start()
    try:
        assert slow_entered.wait(2)
        wait_for(lambda: len(detector.scan()) == 1)
        assert detector.scan()[0].key.startswith("browser:fast:")
        assert not detector.status().scanned
        release.set()
        wait_for(lambda: detector.status().scanned)
        assert len(detector.scan()) == 2
    finally:
        release.set()
        detector.stop()
        detector._thread.join(2)
        assert not detector._thread.is_alive()


def test_empty_success_is_connected_and_discovery_errors_are_not_empty_success():
    with running(browser.BrowserAgentDetector(
        lambda: ("work",), lambda *args: {**response(), "value": {"meetings": []}},
    )) as detector:
        assert detector.scan() == []
        assert detector.status().profiles[0].connected
    with running(browser.BrowserAgentDetector(lambda: ())) as detector:
        assert detector.status().summary == "Browser Agent: no profile"

    def unavailable():
        raise OSError("unavailable")

    with running(browser.BrowserAgentDetector(unavailable)) as detector:
        assert detector.status().summary == "Browser Agent: not connected"


def test_real_detector_chain_produces_only_one_advisory_prompt():
    detector = browser.BrowserAgentDetector(lambda: ("work",), lambda *args: response())
    with running(detector):
        combined = CombinedMeetingDetector([detector])
        policy = MeetingPromptPolicy()
        assert policy.next_prompt(combined.scan()).title == "Weekly meeting"
        assert policy.next_prompt(combined.scan()) is None
        assert combined.observations() == ()


@contextmanager
def pipe_peer(reply, *, delay=0):
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateNamedPipeW.argtypes = (wintypes.LPCWSTR,) + (wintypes.DWORD,) * 6 + (
        ctypes.c_void_p,
    )
    kernel.CreateNamedPipeW.restype = wintypes.HANDLE
    kernel.ConnectNamedPipe.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
    kernel.ConnectNamedPipe.restype = wintypes.BOOL
    for name in ("ReadFile", "WriteFile"):
        getattr(kernel, name).argtypes = (
            wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
        )
        getattr(kernel, name).restype = wintypes.BOOL
    kernel.FlushFileBuffers.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    endpoint = browser.PIPE_DIRECTORY + "browser-agent-test-" + uuid.uuid4().hex
    handle = kernel.CreateNamedPipeW(endpoint, 3, 0, 1, 65536, 65536, 0, None)
    assert handle != ctypes.c_void_p(-1).value
    requests, errors = [], []
    release = threading.Event()

    def serve():
        try:
            if not kernel.ConnectNamedPipe(handle, None) and ctypes.get_last_error() != 535:
                raise ctypes.WinError(ctypes.get_last_error())
            buffer = ctypes.create_string_buffer(4096)
            count = wintypes.DWORD()
            assert kernel.ReadFile(handle, buffer, len(buffer), ctypes.byref(count), None)
            requests.append(json.loads(buffer.raw[:count.value]))
            release.wait(delay)
            data = reply
            for part in (data[:5], data[5:]):
                kernel.WriteFile(handle, part, len(part), ctypes.byref(count), None)
                kernel.FlushFileBuffers(handle)
        except Exception as error:
            errors.append(error)
        finally:
            kernel.CloseHandle(handle)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield endpoint, requests
    finally:
        release.set()
        thread.join(2)
        assert not thread.is_alive()
        assert not errors


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named pipes")
def test_real_pipe_uses_json_lines_and_handles_fragmented_response():
    with pipe_peer(json.dumps(response()).encode() + b"\n") as (endpoint, requests):
        assert browser.request(endpoint, {"op": "describe"}, threading.Event()) == response()
    assert requests == [{"protocolVersion": 4, "op": "describe"}]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named pipes")
def test_silent_real_pipe_hits_deadline_and_closes():
    with pipe_peer(b"{}\n", delay=.3) as (endpoint, _requests):
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            browser.request(endpoint, {"op": "describe"}, threading.Event(), timeout=.05)
        assert time.monotonic() - started < .2


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="Unix sockets unavailable")
def test_real_unix_socket_uses_json_lines():
    # macOS caps socket paths at 104 bytes and pytest's tmp_path under $TMPDIR exceeds it.
    directory = tempfile.TemporaryDirectory(dir="/tmp")
    endpoint = os.path.join(directory.name, "peer.sock")
    requests = []
    with directory, socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(endpoint)
        server.listen(1)
        server.settimeout(2)

        def serve():
            connection, _address = server.accept()
            with connection:
                requests.append(json.loads(connection.recv(4096)))
                connection.sendall(json.dumps(response()).encode() + b"\n")

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            assert browser.request(endpoint, {"op": "describe"}, threading.Event()) == response()
        finally:
            thread.join(2)
            assert not thread.is_alive()
    assert requests == [{"protocolVersion": 4, "op": "describe"}]


def test_host_registration_is_read_from_the_registry_or_the_macos_manifests(tmp_path):
    assert browser.host_manifests("win32", read_registry=lambda: r"C:\x\host.json") == (
        Path(r"C:\x\host.json"),)

    def missing():
        raise FileNotFoundError

    assert browser.host_manifests("win32", read_registry=missing) == ()
    chrome, brave = browser.host_manifests("darwin", home=tmp_path)
    assert chrome == tmp_path / "Library/Application Support/Google/Chrome/NativeMessagingHosts" \
        / "com.ludekvodicka.browser_agent.json"
    assert "Brave-Browser" in str(brave)
    assert browser.host_manifests("linux") == ()


def test_extension_folder_comes_from_the_install_record_beside_the_host(tmp_path):
    host_dir = tmp_path / "install"
    host_dir.mkdir()
    manifest = tmp_path / "host.json"
    manifest.write_text(json.dumps({"path": str(host_dir / "browser-agent-host.exe")}), "utf-8")
    extension = tmp_path / "extension"
    extension.mkdir()
    assert browser.extension_folder([manifest]) is None
    (host_dir / "browser-agent.json").write_text(json.dumps({"extension": str(extension)}), "utf-8")
    assert browser.extension_folder([manifest]) is None
    (extension / "manifest.json").write_text("{}", "utf-8")
    assert browser.extension_folder([tmp_path / "missing.json", manifest]) == extension
    (host_dir / "browser-agent.json").write_text(json.dumps({"extension": "relative"}), "utf-8")
    assert browser.extension_folder([manifest]) is None
