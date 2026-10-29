"""Read only native call-control semantics, in a bounded disposable subprocess.

No UI actions, permission requests, titles, participants, chat values or audio are read.
Only allowlisted control meanings leave this process. ApplicationServices imports stay
inside the reader so fixtures and non-macOS tests need no native frameworks.
"""

import json
import sys
import time
from collections import deque
from pathlib import Path

from audiorecorder.meetings.native_calls import PROFILES, control_kind

_CONTROL_ROLES = {"AXButton", "AXCheckBox", "AXPopUpButton", "AXMenuButton", "AXRadioButton"}
_TEXT_ROLES = {"AXTextArea", "AXTextField", "AXStaticText", "AXSecureTextField"}


class ProbeIncomplete(RuntimeError):
    pass


def confirm_no_windows(quartz, pid):
    """Corroborate empty AXWindows using all Spaces, including off-screen windows.

    Do not read window names or pixels. An omitted/unreadable inventory is not absence.
    Count every owned window regardless of layer/alpha, so auxiliary surfaces fail closed.
    """
    try:
        windows = quartz.CGWindowListCopyWindowInfo(
            quartz.kCGWindowListOptionAll, quartz.kCGNullWindowID
        )
        if windows is None or not len(windows):
            return False
        for window in windows:
            owner = window.get(quartz.kCGWindowOwnerPID)
            if owner is None or int(owner) == pid:
                return False
        return True
    except Exception:
        return False


class AccessibilityReader:
    def __init__(self, ax, clock=time.monotonic, budget=2.0, node_limit=2000):
        self.ax = ax
        self.clock = clock
        self.deadline = clock() + budget
        self.node_limit = node_limit
        # Global here means this disposable probe process, never another application.
        ax.AXUIElementSetMessagingTimeout(ax.AXUIElementCreateSystemWide(), 0.08)

    def attribute(self, element, name, optional=False):
        if self.clock() >= self.deadline:
            raise ProbeIncomplete("time-budget")
        error, value = self.ax.AXUIElementCopyAttributeValue(element, name, None)
        if error == 0:
            return value
        if optional and error in (-25205, -25212):  # unsupported attribute / no value
            return None
        raise ProbeIncomplete("unreadable-control")

    def children(self, element, name="AXChildren", optional=True):
        if self.clock() >= self.deadline:
            raise ProbeIncomplete("time-budget")
        error, count = self.ax.AXUIElementGetAttributeValueCount(element, name, None)
        if optional and error in (-25205, -25212):
            return []
        if error != 0 or count > self.node_limit:
            raise ProbeIncomplete("incomplete-tree")
        if not count:
            return []
        error, values = self.ax.AXUIElementCopyAttributeValues(element, name, 0, count, None)
        if error != 0 or values is None or len(values) != count:
            raise ProbeIncomplete("incomplete-tree")
        return list(values)

    def _windows(self, roots):
        # Interleave windows so a large chat/navigation tree cannot consume the entire
        # budget before a small detached or minimized call-control window is inspected.
        queues = [deque([root]) for root in roots]
        seen = [set() for _ in roots]
        output = [
            {"controls": set(), "complete": True, "identifier": f"ax-{hash(root):x}"}
            for root in roots
        ]
        while any(queues):
            for index, queue in enumerate(queues):
                if not queue:
                    continue
                element = queue.popleft()
                identity = hash(element)
                if identity in seen[index]:
                    continue
                seen[index].add(identity)
                try:
                    if len(seen[index]) > self.node_limit:
                        raise ProbeIncomplete("node-budget")
                    role = self.attribute(element, "AXRole")
                    if role in _CONTROL_ROLES and self.attribute(
                        element, "AXEnabled", optional=True
                    ):
                        for name in ("AXTitle", "AXDescription", "AXHelp"):
                            kind = control_kind(self.attribute(element, name, optional=True))
                            if kind:
                                output[index]["controls"].add(kind)
                    if role not in _TEXT_ROLES:
                        queue.extend(self.children(element))
                except ProbeIncomplete:
                    output[index]["complete"] = False
                    queue.clear()
        for window in output:
            window["controls"] = sorted(window["controls"])
        return output

    def window(self, root):
        return self._windows([root])[0]

    def application(self, pid):
        element = self.ax.AXUIElementCreateApplication(pid)
        windows = self.children(element, "AXWindows", optional=False)
        if len(windows) > 32:
            raise ProbeIncomplete("window-budget")
        output = self._windows(windows)
        return output, all(w["complete"] for w in output)


def snapshot():
    if sys.platform != "darwin":
        return {"permission": "unavailable", "apps": []}
    import ApplicationServices as AX
    from AppKit import NSRunningApplication
    from Foundation import NSBundle

    permission = "granted" if AX.AXIsProcessTrusted() else "not-granted"
    apps = []
    for bundle in PROFILES:
        try:
            running = list(NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle))
            if len(running) > 1:
                raise ProbeIncomplete("multiple-processes")
            if not running:
                apps.append(
                    {"bundle": bundle, "running": False, "complete": True, "reason": "not-running"}
                )
                continue
            app = running[0]
            date = app.launchDate()
            if date is None:
                raise ProbeIncomplete("process-identity")
            process = f"{app.processIdentifier()}:{date.timeIntervalSince1970():.6f}"
            native_bundle = NSBundle.bundleWithURL_(app.bundleURL())
            version = str(
                native_bundle.objectForInfoDictionaryKey_("CFBundleShortVersionString") or ""
            )
            item = {
                "bundle": bundle,
                "running": True,
                "complete": False,
                "process": process,
                "version": version[:80],
                "windows": [],
                "reason": "permission-required",
            }
            if permission == "granted":
                reader = AccessibilityReader(AX)
                item["windows"], item["complete"] = reader.application(app.processIdentifier())
                if item["complete"] and not item["windows"]:
                    import Quartz

                    item["empty_windows_confirmed"] = confirm_no_windows(
                        Quartz, app.processIdentifier()
                    )
                item["reason"] = "controls-read" if item["complete"] else "incomplete-tree"
            apps.append(item)
        except Exception:
            apps.append(
                {"bundle": bundle, "running": True, "complete": False, "reason": "probe-error"}
            )
    return {"permission": permission, "apps": apps}


def write_snapshot(destination):
    # Parent supplies a private temporary file; output is a small normalized schema.
    Path(destination).write_text(json.dumps(snapshot()), encoding="utf-8")
