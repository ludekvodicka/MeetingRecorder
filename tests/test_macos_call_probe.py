from types import SimpleNamespace

import pytest

from audiorecorder.meetings.macos_call_probe import (
    AccessibilityReader,
    ProbeIncomplete,
    confirm_no_windows,
)


class Node:
    def __init__(self, role, title=None, children=(), enabled=True):
        self.values = {"AXRole": role, "AXEnabled": enabled, "AXChildren": list(children)}
        if title:
            self.values["AXTitle"] = title


class API:
    def __init__(self, root):
        self.root = root
        self.read = []

    def AXUIElementCreateSystemWide(self):
        return self.root

    def AXUIElementSetMessagingTimeout(self, element, timeout):
        assert 0 < timeout < 1

    def AXUIElementCreateApplication(self, pid):
        return self.root

    def AXUIElementCopyAttributeValue(self, element, name, unused):
        self.read.append((element, name))
        return (0, element.values[name]) if name in element.values else (-25205, None)

    def AXUIElementGetAttributeValueCount(self, element, name, unused):
        return (0, len(element.values[name])) if name in element.values else (-25205, None)

    def AXUIElementCopyAttributeValues(self, element, name, start, count, unused):
        return 0, element.values[name][start : start + count]


def test_reader_exports_only_control_meanings_not_titles_or_text_values():
    private = Node("AXTextArea", "Secret meeting transcript", [Node("AXButton", "Leave")])
    root = Node(
        "AXWindow",
        "Private meeting subject",
        [
            private,
            Node("AXButton", "Mute"),
            Node("AXButton", "Camera"),
            Node("AXButton", "Leave"),
            Node("AXButton", "Call Jane Doe"),
        ],
    )
    api = API(root)
    result = AccessibilityReader(api).window(root)
    assert result["complete"]
    assert result["controls"] == ["camera", "hangup", "microphone"]
    assert not any(
        name == "AXValue" or (node in (private, root) and name == "AXTitle")
        for node, name in api.read
    )
    assert "Secret" not in str(result) and "Jane" not in str(result)


def test_disabled_controls_are_not_active_evidence():
    root = Node("AXWindow", children=[Node("AXButton", "Leave", enabled=False)])
    assert not AccessibilityReader(API(root)).window(root)["controls"]


def test_cyclic_children_terminate_without_losing_budget():
    root = Node("AXWindow")
    root.values["AXChildren"] = [root]
    assert AccessibilityReader(API(root)).window(root)["complete"]


def test_truncated_or_timed_out_tree_never_reports_complete():
    root = Node("AXWindow", children=[Node("AXButton", "Leave")])
    assert not AccessibilityReader(API(root), node_limit=1).window(root)["complete"]
    clock = SimpleNamespace(value=0.0)
    reader = AccessibilityReader(API(root), clock=lambda: clock.value, budget=1)
    clock.value = 2
    assert not reader.window(root)["complete"]


def test_missing_app_window_list_is_an_error_not_healthy_absence():
    root = Node("AXApplication")
    with pytest.raises(ProbeIncomplete):
        AccessibilityReader(API(root)).application(1)


def test_large_chat_tree_does_not_starve_separate_call_window():
    chat = Node(
        "AXWindow", children=[Node("AXGroup", children=[Node("AXGroup")]) for _ in range(8)]
    )
    call = Node(
        "AXWindow",
        children=[Node("AXButton", "Leave"), Node("AXButton", "Mute"), Node("AXButton", "Camera")],
    )
    root = Node("AXApplication")
    root.values["AXWindows"] = [chat, call]
    windows, complete = AccessibilityReader(API(root), node_limit=4).application(1)
    assert not complete
    assert not windows[0]["complete"]
    assert windows[1]["complete"]
    assert windows[1]["controls"] == ["camera", "hangup", "microphone"]


@pytest.mark.parametrize(
    "inventory,expected",
    [(None, False), ([], False), ([{}], False), ([{"pid": 42}], False), ([{"pid": 7}], True)],
)
def test_empty_ax_windows_need_a_healthy_conflict_free_all_spaces_inventory(inventory, expected):
    def read(options, window):
        assert options == 99 and window == 0
        return inventory

    quartz = SimpleNamespace(
        CGWindowListCopyWindowInfo=read,
        kCGWindowListOptionAll=99,
        kCGNullWindowID=0,
        kCGWindowOwnerPID="pid",
    )
    assert confirm_no_windows(quartz, 42) is expected
