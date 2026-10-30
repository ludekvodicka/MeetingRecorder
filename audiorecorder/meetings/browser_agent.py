"""Read Browser Agent meeting tools on a background thread, without a local listener."""

import ctypes
import json
import os
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from audiorecorder.meetings.detection import MeetingCandidate

PROTOCOL_VERSION = 4
POLL_SECONDS = 15
REQUEST_TIMEOUT = 3
MAX_RESPONSE_BYTES = 1024 * 1024
PIPE_DIRECTORY = "\\\\.\\pipe\\"
HOST_NAME = "com.ludekvodicka.browser_agent"
DOWNLOAD_URL = "https://github.com/ludekvodicka/BrowserAgentExtension"
# Written by `browser-agent-host install` beside the installed host: {"extension": "<folder>"}.
INSTALL_RECORD = "browser-agent.json"
MEETING_TOOLS = {
    "meetings.web.googleMeet": "Google Meet",
    "meetings.web.zoom": "Zoom Web",
    "meetings.web.teams": "Microsoft Teams Web",
}


def discover_endpoints(platform_name=None, home=None):
    platform_name = sys.platform if platform_name is None else platform_name
    if platform_name == "win32":
        return tuple(PIPE_DIRECTORY + name for name in sorted(os.listdir(PIPE_DIRECTORY))
                     if name.startswith("browser-agent-"))
    if platform_name == "darwin":
        directory = (Path.home() if home is None else Path(home)) / ".browser-agent/run"
        return tuple(str(path) for path in sorted(directory.glob("browser-agent-*.sock")))
    return ()


def _read_registered_manifest():
    import winreg

    key = r"Software\Google\Chrome\NativeMessagingHosts" + "\\" + HOST_NAME
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
        return winreg.QueryValue(handle, None)


def host_manifests(platform_name=None, home=None, read_registry=_read_registered_manifest):
    """Where the browsers look for the Browser Agent native host registration."""
    platform_name = sys.platform if platform_name is None else platform_name
    if platform_name == "win32":
        try:
            return (Path(read_registry()),)
        except OSError:
            return ()
    if platform_name == "darwin":
        support = (Path.home() if home is None else Path(home)) / "Library/Application Support"
        return tuple(support / browser / "NativeMessagingHosts" / f"{HOST_NAME}.json"
                     for browser in ("Google/Chrome", "BraveSoftware/Brave-Browser"))
    return ()


def extension_folder(manifests):
    """The extension folder Browser Agent's install recorded beside its host, if it still exists."""
    for manifest in manifests:
        try:
            host = Path(json.loads(manifest.read_text(encoding="utf-8"))["path"])
            recorded = json.loads((host.parent / INSTALL_RECORD).read_text(encoding="utf-8"))
            folder = Path(recorded["extension"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if folder.is_absolute() and (folder / "manifest.json").is_file():
            return folder
    return None


def _remaining(deadline, stopped):
    remaining = deadline - time.monotonic()
    if stopped.is_set() or remaining <= 0:
        raise TimeoutError("Browser Agent request cancelled or timed out")
    return remaining


def _read_response(read, deadline, stopped):
    data = bytearray()
    while True:
        _remaining(deadline, stopped)
        chunk = read()
        if chunk is None:
            stopped.wait(0.01)
            continue
        if not chunk:
            raise OSError("Browser Agent disconnected before a complete response")
        data.extend(chunk)
        newline = data.find(b"\n")
        if newline > MAX_RESPONSE_BYTES or (newline < 0 and len(data) > MAX_RESPONSE_BYTES):
            raise ValueError("Browser Agent response is too large")
        if newline >= 0:
            response = json.loads(data[:newline])
            if not isinstance(response, dict):
                raise ValueError("Browser Agent response must be an object")
            return response


def _pipe_request(endpoint, payload, deadline, stopped):
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.SetNamedPipeHandleState.argtypes = (
        wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, ctypes.c_void_p,
    )
    kernel.SetNamedPipeHandleState.restype = wintypes.BOOL
    for name in ("ReadFile", "WriteFile"):
        function = getattr(kernel, name)
        function.argtypes = (
            wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
        )
        function.restype = wintypes.BOOL
    # PIPE_NOWAIT makes both reads and writes bounded, including a silent connected peer.
    # Opening a busy pipe fails immediately; do not wait for an available server instance.
    with open(endpoint, "r+b", buffering=0) as pipe:
        handle = msvcrt.get_osfhandle(pipe.fileno())
        mode = wintypes.DWORD(1)
        if not kernel.SetNamedPipeHandleState(handle, ctypes.byref(mode), None, None):
            raise ctypes.WinError(ctypes.get_last_error())
        offset = 0
        while offset < len(payload):
            _remaining(deadline, stopped)
            written = wintypes.DWORD()
            part = payload[offset:]
            if not kernel.WriteFile(handle, part, len(part), ctypes.byref(written), None):
                raise ctypes.WinError(ctypes.get_last_error())
            offset += written.value
            if not written.value:
                stopped.wait(0.01)

        def read():
            buffer = ctypes.create_string_buffer(65536)
            count = wintypes.DWORD()
            if kernel.ReadFile(handle, buffer, len(buffer), ctypes.byref(count), None):
                return buffer.raw[:count.value]
            error = ctypes.get_last_error()
            if error == 232:  # ERROR_NO_DATA on a nonblocking pipe.
                return None
            raise ctypes.WinError(error)

        return _read_response(read, deadline, stopped)


def request(endpoint, message, stopped, timeout=REQUEST_TIMEOUT):
    deadline = time.monotonic() + timeout
    payload = (json.dumps({"protocolVersion": PROTOCOL_VERSION, **message}) + "\n").encode()
    _remaining(deadline, stopped)
    if endpoint.startswith(PIPE_DIRECTORY):
        return _pipe_request(endpoint, payload, deadline, stopped)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(_remaining(deadline, stopped))
        connection.connect(endpoint)
        connection.settimeout(_remaining(deadline, stopped))
        connection.sendall(payload)

        def read():
            connection.settimeout(min(0.05, _remaining(deadline, stopped)))
            try:
                return connection.recv(65536)
            except TimeoutError:
                return None

        return _read_response(read, deadline, stopped)


@dataclass(frozen=True)
class BrowserAgentProfile:
    instance_id: str
    label: str
    tools: tuple[str, ...]
    connected: bool
    error: str | None = None


@dataclass(frozen=True)
class BrowserAgentStatus:
    active: bool = False
    scanned: bool = False
    profiles: tuple[BrowserAgentProfile, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def summary(self):
        if not self.active:
            return "Browser Agent: meeting detection off"
        if not self.scanned:
            return "Browser Agent: scanning"
        if not self.profiles:
            return "Browser Agent: not connected" if self.errors else "Browser Agent: no profile"
        count = sum(len(profile.tools) for profile in self.profiles)
        summary = f"Browser Agent: {len(self.profiles)} profiles, {count} meeting tools"
        if self.errors or any(not profile.connected for profile in self.profiles):
            summary += " (not connected" + (
                " in some profiles)" if any(p.connected for p in self.profiles) else ")"
            )
        return summary


def _profile(response):
    if response.get("ok") is not True:
        raise ValueError(str(response.get("code", "request refused")))
    profile = response.get("profile")
    if not isinstance(profile, dict) or profile.get("protocolVersion") != PROTOCOL_VERSION:
        raise ValueError("protocol mismatch")
    if not isinstance(profile.get("instanceId"), str) or not profile["instanceId"]:
        raise ValueError("invalid profile identity")
    if not isinstance(profile.get("tools"), list):
        raise ValueError("invalid profile tools")
    return profile


class BrowserAgentDetector:
    def __init__(self, discover=discover_endpoints, transport=request, on_updated=None,
                 poll_seconds=POLL_SECONDS):
        self._discover = discover
        self._transport = transport
        self._on_updated = on_updated
        self._poll_seconds = poll_seconds
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._stopped.set()
        self._thread = None
        self._candidates = ()
        self._status = BrowserAgentStatus()

    def start(self):
        with self._lock:
            if not self._stopped.is_set():
                return
            stopped = threading.Event()
            self._stopped = stopped
            self._status = BrowserAgentStatus(active=True)
            self._thread = threading.Thread(
                target=self._run, args=(stopped,), name="browser-agent-meetings", daemon=True,
            )
            self._thread.start()

    def stop(self):
        with self._lock:
            self._stopped.set()
            self._candidates = ()
            self._status = BrowserAgentStatus()

    def scan(self):
        with self._lock:
            return list(self._candidates)

    def status(self):
        with self._lock:
            return self._status

    def _run(self, stopped):
        results = {}
        while not stopped.is_set():
            started = time.monotonic()
            try:
                endpoints = tuple(dict.fromkeys(self._discover()))
            except OSError as error:
                endpoints = ()
                results = {}
                self._publish({}, True, stopped, (f"Discovery failed: {error}",))
            else:
                # Keep pending profiles' last snapshot until their own answer arrives.
                # Otherwise a fast profile would momentarily remove another one's meeting.
                results = {endpoint: results[endpoint] for endpoint in endpoints
                           if endpoint in results}
                if not endpoints:
                    self._publish({}, True, stopped)
                else:
                    with ThreadPoolExecutor(max_workers=len(endpoints),
                                            thread_name_prefix="browser-agent-profile") as pool:
                        futures = {pool.submit(self._poll_profile, endpoint, stopped): endpoint
                                   for endpoint in endpoints}
                        for index, future in enumerate(as_completed(futures)):
                            endpoint = futures[future]
                            try:
                                profile, found = future.result()
                                results[endpoint] = (profile, found, None)
                            except (OSError, ValueError, TypeError) as error:
                                results[endpoint] = (None, (), f"{endpoint}: {error}")
                            ordered = {endpoint: results[endpoint] for endpoint in endpoints
                                       if endpoint in results}
                            self._publish(ordered, index + 1 == len(endpoints), stopped)
            stopped.wait(max(0, self._poll_seconds - (time.monotonic() - started)))

    def _publish(self, results, scanned, stopped, errors=()):
        profiles, candidates, failures = [], [], list(errors)
        for profile, found, error in results.values():
            if profile is not None:
                profiles.append(profile)
            candidates.extend(found)
            if error is not None:
                failures.append(error)
        with self._lock:
            if stopped.is_set() or stopped is not self._stopped:
                return
            self._candidates = tuple({c.key: c for c in candidates}.values())
            self._status = BrowserAgentStatus(True, scanned, tuple(profiles), tuple(failures))
        if self._on_updated is not None:
            self._on_updated()

    def _poll_profile(self, endpoint, stopped):
        profile = _profile(self._transport(endpoint, {"op": "describe"}, stopped))
        instance = profile["instanceId"]
        label = profile.get("label") or instance
        tools = tuple(dict.fromkeys(
            tool["id"] for tool in profile["tools"] if isinstance(tool, dict)
            and isinstance(tool.get("id"), str) and tool["id"] in MEETING_TOOLS
            and tool.get("enabled") is True
        ))
        names = tuple(MEETING_TOOLS[tool] for tool in tools)
        if profile.get("agentAccess") is not True:
            return BrowserAgentProfile(instance, label, names, False, "Agent access off"), []
        candidates = []
        try:
            for tool in tools:
                response = self._transport(endpoint, {
                    "op": "call", "tool": tool, "caller": "meeting-recorder",
                }, stopped)
                current = _profile(response)
                if current["instanceId"] != instance:
                    raise ValueError("profile identity changed")
                value = response.get("value")
                if not isinstance(value, dict) or not isinstance(value.get("meetings"), list):
                    raise ValueError("invalid meeting list")
                for meeting in value["meetings"]:
                    if (not isinstance(meeting, dict) or not isinstance(meeting.get("key"), str)
                            or not meeting["key"] or not isinstance(meeting.get("title"), str)):
                        raise ValueError("invalid meeting")
                    candidates.append(MeetingCandidate(
                        f"browser:{instance}:{meeting['key']}", MEETING_TOOLS[tool],
                        meeting["title"], source="browser-agent", prompt_immediately=True,
                    ))
        except (OSError, ValueError, TypeError) as error:
            return BrowserAgentProfile(instance, label, names, False, str(error)), []
        return BrowserAgentProfile(instance, label, names, True), candidates
