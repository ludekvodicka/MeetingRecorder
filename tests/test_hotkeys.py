import sys

import pytest

from audiorecorder.dictation.hotkeys import (
    HotkeyListener,
    PushToTalkStateMachine,
    held_modifiers,
    normalize_key,
)


class FakeKey:
    def __init__(self, name):
        self.name = name


class FakeCharKey:
    """A printable character. pynput models these without a name attribute."""


@pytest.fixture
def machine():
    events = []
    m = PushToTalkStateMachine(
        on_activate=lambda label: events.append(("start", label)),
        on_release=lambda: events.append(("stop", None)),
    )
    m.events = events
    return m


def press(machine, *keys):
    for key in keys:
        machine.key_down(key)


class TestNormalizeKey:
    @pytest.mark.parametrize("name,expected", [
        ("ctrl", "ctrl"), ("ctrl_l", "ctrl"), ("ctrl_r", "ctrl"),
        ("shift", "shift"), ("shift_l", "shift"), ("shift_r", "shift"),
        ("space", "space"),
        ("esc", "other"), ("f1", "other"), ("cmd", "other"),
    ])
    def test_named_keys(self, name, expected):
        assert normalize_key(FakeKey(name)) == expected

    def test_character_keys_are_other(self):
        assert normalize_key(FakeCharKey()) == "other"


class TestPushToTalk:
    def test_ctrl_space_dictates(self, machine):
        press(machine, "ctrl", "space")
        assert machine.events == [("start", "dictate")]
        assert machine.active

    def test_ctrl_shift_space_translates(self, machine):
        press(machine, "ctrl", "shift", "space")
        assert machine.events == [("start", "translate")]

    def test_shift_order_does_not_matter(self, machine):
        press(machine, "shift", "ctrl", "space")
        assert machine.events == [("start", "translate")]

    def test_releasing_space_ends_it(self, machine):
        press(machine, "ctrl", "space")
        machine.key_up("space")
        assert machine.events[-1] == ("stop", None)
        assert not machine.active

    def test_releasing_ctrl_ends_it(self, machine):
        press(machine, "ctrl", "space")
        machine.key_up("ctrl")
        assert machine.events[-1] == ("stop", None)

    def test_releasing_shift_does_not_end_it(self, machine):
        """Letting go of shift while still holding Ctrl+Space must not cut the dictation."""
        press(machine, "ctrl", "shift", "space")
        machine.key_up("shift")
        assert machine.events == [("start", "translate")]
        assert machine.active

    def test_space_without_ctrl_does_nothing(self, machine):
        press(machine, "space")
        machine.key_up("space")
        assert machine.events == []

    def test_key_repeat_does_not_restart(self, machine):
        press(machine, "ctrl", "space", "space", "space")
        assert machine.events == [("start", "dictate")]

    def test_unrelated_keys_are_ignored(self, machine):
        press(machine, "ctrl", "other", "space")
        assert machine.events == [("start", "dictate")]

    def test_release_without_a_press_is_silent(self, machine):
        machine.key_up("space")
        machine.key_up("ctrl")
        assert machine.events == []

    def test_two_dictations_in_a_row(self, machine):
        press(machine, "ctrl", "space")
        machine.key_up("space")
        press(machine, "space")
        machine.key_up("space")
        assert machine.events == [
            ("start", "dictate"), ("stop", None), ("start", "dictate"), ("stop", None),
        ]

    def test_reset_clears_held_modifiers(self, machine):
        press(machine, "ctrl")
        machine.reset()
        press(machine, "space")
        assert machine.events == []


class TestSyntheticPasteDoesNotDesyncModifiers:
    """The paste after a dictation sends a synthetic Ctrl+V that our own global listener
    sees. Without asking the operating system, that synthetic release clears a Ctrl the
    user is still holding, and the next Ctrl+Space is ignored until they press Ctrl again.
    """

    def machine_with_probe(self, held):
        events = []
        m = PushToTalkStateMachine(
            on_activate=lambda label: events.append(("start", label)),
            on_release=lambda: events.append(("stop", None)),
            modifier_probe=lambda: set(held),
        )
        m.events = events
        return m

    def test_second_dictation_works_after_a_synthetic_paste(self):
        held = {"ctrl"}
        m = self.machine_with_probe(held)

        press(m, "ctrl", "space")
        m.key_up("space")
        # paste_text taps Ctrl+V, and the listener sees both halves of it
        m.key_down("ctrl")
        m.key_up("ctrl")
        # the user, still holding Ctrl, presses space again
        m.key_down("space")

        assert m.events == [
            ("start", "dictate"), ("stop", None), ("start", "dictate"),
        ]

    def test_synthetic_ctrl_release_does_not_end_a_live_dictation(self):
        m = self.machine_with_probe({"ctrl"})
        press(m, "ctrl", "space")
        m.key_up("ctrl")  # synthetic, the user has not let go
        assert m.events == [("start", "dictate")]
        assert m.active

    def test_a_real_ctrl_release_still_ends_it(self):
        held = {"ctrl"}
        m = self.machine_with_probe(held)
        press(m, "ctrl", "space")
        held.clear()  # the user really let go
        m.key_up("ctrl")
        assert m.events[-1] == ("stop", None)
        assert not m.active

    def test_the_probe_decides_translate_not_the_event_stream(self):
        m = self.machine_with_probe({"ctrl", "shift"})
        m.key_down("space")
        assert m.events == [("start", "translate")]

    def test_falls_back_to_tracking_when_the_platform_cannot_say(self):
        events = []
        m = PushToTalkStateMachine(
            on_activate=lambda label: events.append(("start", label)),
            on_release=lambda: events.append(("stop", None)),
            modifier_probe=lambda: None,
        )
        m.key_down("ctrl")
        m.key_down("space")
        assert events == [("start", "dictate")]


class TestHeldModifiers:
    def test_returns_a_set_on_windows_and_none_elsewhere(self, monkeypatch):
        monkeypatch.setattr("audiorecorder.dictation.hotkeys.sys.platform", "linux")
        assert held_modifiers() is None

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
    def test_windows_reports_a_set_without_raising(self):
        held = held_modifiers()
        assert isinstance(held, set)
        assert held <= {"ctrl", "shift"}


class TestSupport:
    def test_windows_is_supported(self, monkeypatch):
        monkeypatch.setattr("audiorecorder.dictation.hotkeys.sys.platform", "win32")
        supported, reason = HotkeyListener.support()
        assert supported
        assert reason == ""

    def test_macos_is_supported(self, monkeypatch):
        monkeypatch.setattr("audiorecorder.dictation.hotkeys.sys.platform", "darwin")
        assert HotkeyListener.support()[0]

    def test_linux_on_x11_is_supported(self, monkeypatch):
        monkeypatch.setattr("audiorecorder.dictation.hotkeys.sys.platform", "linux")
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
        assert HotkeyListener.support()[0]

    def test_wayland_display_is_refused_with_a_reason(self, monkeypatch):
        monkeypatch.setattr("audiorecorder.dictation.hotkeys.sys.platform", "linux")
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        supported, reason = HotkeyListener.support()
        assert not supported
        assert "Wayland" in reason

    def test_wayland_session_type_is_refused(self, monkeypatch):
        monkeypatch.setattr("audiorecorder.dictation.hotkeys.sys.platform", "linux")
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.setenv("XDG_SESSION_TYPE", "Wayland")
        assert not HotkeyListener.support()[0]
