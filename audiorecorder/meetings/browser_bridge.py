"""Loopback bridge receiving sanitized meeting heartbeats from a browser extension."""

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from audiorecorder.meetings.detection import MeetingCandidate

log = logging.getLogger(__name__)

BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 8765
BRIDGE_PATH = "/v1/meeting"
BRIDGE_HEADER = "X-Meeting-Recorder"
BRIDGE_HEADER_VALUE = "browser-extension-v1"
MAX_BODY_BYTES = 8192
HEARTBEAT_TTL_SECONDS = 75
_ALLOWED_ORIGIN_PREFIXES = ("chrome-extension://", "moz-extension://")


class BrowserMeetingBridge:
    def __init__(self, host=BRIDGE_HOST, port=BRIDGE_PORT,
                 heartbeat_ttl=HEARTBEAT_TTL_SECONDS, clock=time.monotonic):
        self.host = host
        self.port = port
        self._heartbeat_ttl = heartbeat_ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._meetings = {}
        self._server = None
        self._thread = None
        self.last_error = None

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return True
        try:
            server = ThreadingHTTPServer((self.host, self.port), _BridgeHandler)
        except OSError as err:
            self.last_error = str(err)
            log.warning("browser meeting bridge could not listen: %s", err)
            return False
        server.daemon_threads = True
        server.bridge = self
        self._server = server
        # Port 0 is useful for tests and local diagnostics.
        self.port = server.server_address[1]
        self.last_error = None
        self._thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.2},
            name="browser-meeting-bridge",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self):
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=1)
        with self._lock:
            self._meetings.clear()

    def update(self, payload):
        key = _clean(payload.get("key"), 300)
        if not key:
            raise ValueError("meeting key is required")
        full_key = f"browser:{key}"
        with self._lock:
            if payload.get("active") is False:
                self._meetings.pop(full_key, None)
                return
            app_name = _clean(payload.get("app_name"), 80)
            title = _clean(payload.get("title"), 300)
            if not app_name or not title:
                raise ValueError("app_name and title are required")
            self._meetings[full_key] = (
                MeetingCandidate(full_key, app_name, title, source="browser-extension"),
                self._clock(),
            )

    def scan(self):
        now = self._clock()
        with self._lock:
            expired = [key for key, (_, seen_at) in self._meetings.items()
                       if now - seen_at > self._heartbeat_ttl]
            for key in expired:
                self._meetings.pop(key, None)
            return [candidate for candidate, _ in self._meetings.values()]


class _BridgeHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler names the protocol hooks
        if self.path != BRIDGE_PATH:
            self.send_error(404)
            return
        origin = self.headers.get("Origin", "")
        # Chromium may omit Origin for a host-permitted extension request. When it sends
        # one, it must be an extension origin. The custom header remains mandatory: an
        # ordinary page cannot send it because this server refuses its CORS preflight.
        if origin and not origin.startswith(_ALLOWED_ORIGIN_PREFIXES):
            self.send_error(403)
            return
        if self.headers.get(BRIDGE_HEADER) != BRIDGE_HEADER_VALUE:
            self.send_error(403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400)
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self.send_error(413)
            return
        try:
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("JSON object required")
            self.server.bridge.update(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as err:
            self.send_error(400, str(err))
            return
        self.send_response(204)
        self.end_headers()

    def do_OPTIONS(self):  # noqa: N802
        # Ordinary websites receive no CORS grant, so their custom-header POST never runs.
        self.send_error(403)

    def log_message(self, format, *args):
        # A heartbeat every 30 seconds should not become the application's main log.
        return


def _clean(value, limit):
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]
