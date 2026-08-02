import pytest

from audiorecorder.dictation.hotkeys import (
    HotkeyListener,
    PushToTalkStateMachine,
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
