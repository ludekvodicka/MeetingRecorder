import ctypes
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import audiorecorder
from audiorecorder.dictation.hotkeys import (
    HotkeyListener,
    HotkeyUnsupportedError,
    PushToTalkStateMachine,
)


@pytest.fixture
def machine():
    events = []
    machine = PushToTalkStateMachine(
        on_activate=lambda label: events.append(("start", label)),
        on_release=lambda: events.append(("stop", None)),
    )
    machine.events = events
    return machine


def test_make_and_break_reports_drive_the_state_machine(monkeypatch, machine):
    listeners = []

    class FakeRawKeyboardListener:
        on_key: object
        start: Mock
        stop: Mock

        def __init__(self, on_key, on_error=None):
            self.on_key = on_key
            self.start = Mock()
            self.stop = Mock()
            listeners.append(self)

    monkeypatch.setattr("audiorecorder.dictation.hotkeys.sys.platform", "win32")
    monkeypatch.setitem(sys.modules, "audiorecorder.dictation.raw_keyboard",
                        SimpleNamespace(RawKeyboardListener=FakeRawKeyboardListener))
    listener = HotkeyListener(machine)
    listener.start()
    try:
        assert len(listeners) == 1
        raw = listeners[0]
        raw.start.assert_called_once_with()
        for vkey, flags in ((0x11, 0), (0x20, 0), (0x20, 0), (0x20, 1)):
            raw.on_key(vkey, flags)
        assert machine.events == [("start", "dictate"), ("stop", None)]
        assert not machine.active
    finally:
        listener.stop()
    raw.stop.assert_called_once_with()
    machine.key_down("space")
    assert not machine.active


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_non_windows_uses_pynput_make_and_break(monkeypatch, machine, platform):
    keyboard = SimpleNamespace(Listener=Mock(return_value=SimpleNamespace(
        start=Mock(), stop=Mock(),
    )))
    monkeypatch.setitem(sys.modules, "pynput", SimpleNamespace(keyboard=keyboard))
    monkeypatch.setattr("audiorecorder.dictation.hotkeys.sys.platform", platform)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    listener = HotkeyListener(machine)
    listener.start()
    try:
        keyboard.Listener.assert_called_once()
        callbacks = keyboard.Listener.call_args.kwargs
        assert set(callbacks) == {"on_press", "on_release"}
        keyboard.Listener.return_value.start.assert_called_once_with()
        for name in ("ctrl_l", "shift_r", "space"):
            callbacks["on_press"](SimpleNamespace(name=name))
        callbacks["on_release"](SimpleNamespace(name="space"))
        assert machine.events == [("start", "translate"), ("stop", None)]
    finally:
        listener.stop()
    keyboard.Listener.return_value.stop.assert_called_once_with()
    machine.key_down("space")
    assert not machine.active


def test_no_low_level_hook_in_the_package():
    package = Path(audiorecorder.__file__).parent
    offenders = [str(path.relative_to(package)) for path in package.rglob("*.py")
                 if "SetWindowsHookEx" in path.read_text("utf-8")]
    assert offenders == []


@pytest.fixture
def windows_raw():
    if sys.platform != "win32":
        pytest.skip("Windows only")
    from audiorecorder.dictation import raw_keyboard

    return raw_keyboard


def test_windows_registers_raw_input_and_never_builds_the_pynput_listener(
    monkeypatch, machine, windows_raw,
):
    def refuse(*args, **kwargs):
        raise AssertionError("pynput keyboard listener on Windows")

    monkeypatch.setattr("pynput.keyboard.Listener", refuse)
    listener = HotkeyListener(machine)
    for _ in range(2):
        listener.start()
        try:
            thread = listener._listener._thread
            assert thread.is_alive()
            assert thread.name == "raw-keyboard"
            registration = windows_raw._keyboard_registration()
            assert registration is not None
            assert registration.hwndTarget
            assert registration.dwFlags & windows_raw.RIDEV_INPUTSINK
        finally:
            listener.stop()
        assert not thread.is_alive()
        assert not any(thread.name == "raw-keyboard" for thread in threading.enumerate())
        assert windows_raw._keyboard_registration() is None


def test_registration_refusal_reports_the_cause_and_destroys_the_window(
    monkeypatch, machine, windows_raw,
):
    raw = windows_raw
    destroy = Mock(return_value=True)

    def refuse(*args):
        ctypes.set_last_error(5)
        return False

    monkeypatch.setattr(raw, "_open_window", lambda: 42)
    monkeypatch.setattr(raw.user32, "RegisterRawInputDevices", refuse)
    monkeypatch.setattr(raw.user32, "DestroyWindow", destroy)
    listener = HotkeyListener(machine)
    with pytest.raises(HotkeyUnsupportedError, match="could not be registered") as error:
        listener.start()
    assert isinstance(error.value.__cause__, OSError)
    assert error.value.__cause__.winerror == 5
    assert listener._listener is None
    destroy.assert_called_once_with(42)
    assert not any(thread.name == "raw-keyboard" for thread in threading.enumerate())


@pytest.mark.parametrize("target,flags,remove", [
    (43, 0x100, False), (42, 0, True), (None, 0, False),
])
def test_invalid_registration_is_refused_without_removing_another_owner(
    monkeypatch, machine, windows_raw, target, flags, remove,
):
    raw = windows_raw
    register = Mock()
    destroy = Mock(return_value=True)
    monkeypatch.setattr(raw, "_open_window", lambda: 42)
    monkeypatch.setattr(raw, "_register", register)
    monkeypatch.setattr(raw, "_keyboard_registration",
                        lambda: raw.RawInputDevice(1, 6, flags, target) if target else None)
    monkeypatch.setattr(raw.user32, "DestroyWindow", destroy)
    listener = HotkeyListener(machine)
    with pytest.raises(HotkeyUnsupportedError, match="does not target the listener window"):
        listener.start()
    assert register.call_args_list[0].args == (42, raw.RIDEV_INPUTSINK)
    assert register.call_count == (2 if remove else 1)
    if remove:
        assert register.call_args.args == (None, raw.RIDEV_REMOVE)
    destroy.assert_called_once_with(42)
    assert listener._listener is None
    assert not any(thread.name == "raw-keyboard" for thread in threading.enumerate())


def test_failed_startup_verification_removes_registration_and_destroys_window(
    monkeypatch, machine, windows_raw,
):
    raw = windows_raw
    failure = OSError("registration enumeration failed")
    register = Mock()
    destroy = Mock(return_value=True)
    monkeypatch.setattr(raw, "_open_window", lambda: 42)
    monkeypatch.setattr(raw, "_register", register)
    monkeypatch.setattr(raw, "_keyboard_registration", Mock(side_effect=[
        failure, raw.RawInputDevice(1, 6, raw.RIDEV_INPUTSINK, 42),
    ]))
    monkeypatch.setattr(raw.user32, "DestroyWindow", destroy)
    with pytest.raises(HotkeyUnsupportedError, match="registration enumeration failed") as error:
        HotkeyListener(machine).start()
    assert error.value.__cause__ is failure
    assert [call.args for call in register.call_args_list] == [
        (42, raw.RIDEV_INPUTSINK), (None, raw.RIDEV_REMOVE),
    ]
    destroy.assert_called_once_with(42)
    assert not any(thread.name == "raw-keyboard" for thread in threading.enumerate())


def test_stop_during_startup_destroys_window_without_registering(monkeypatch, windows_raw):
    raw = windows_raw
    listener = raw.RawKeyboardListener(Mock())
    opened = threading.Event()
    errors = []
    register = Mock()
    destroy = Mock(return_value=True)

    def open_window():
        opened.set()
        if not listener._stopping.wait(2):
            raise RuntimeError("Startup was not cancelled")
        return 42

    def start():
        try:
            listener.start()
        except Exception as error:
            errors.append(error)

    monkeypatch.setattr(raw, "_open_window", open_window)
    monkeypatch.setattr(raw, "_register", register)
    monkeypatch.setattr(raw.user32, "DestroyWindow", destroy)
    starter = threading.Thread(target=start)
    starter.start()
    try:
        assert opened.wait(1)
    finally:
        listener.stop()
        starter.join(3)
    assert not starter.is_alive()
    assert errors == []
    register.assert_not_called()
    destroy.assert_called_once_with(42)
    assert not any(thread.name == "raw-keyboard" for thread in threading.enumerate())


@pytest.mark.parametrize("failure,exception", [
    ("read", OSError), ("type", ValueError), ("callback", RuntimeError),
])
def test_raw_input_is_dispatched_even_when_processing_fails(
    monkeypatch, windows_raw, failure, exception,
):
    raw = windows_raw
    dispatch = Mock()
    callback = Mock(side_effect=RuntimeError("callback failed"))

    def message(pointer, *args):
        value = ctypes.cast(pointer, ctypes.POINTER(raw.wintypes.MSG)).contents
        value.message = raw.WM_INPUT
        value.lParam = 123
        return 1

    def report(handle, command, pointer, size, header_size):
        if failure == "read":
            ctypes.set_last_error(5)
            return raw.UINT_ERROR
        value = ctypes.cast(pointer, ctypes.POINTER(raw.RawKeyboardInput)).contents
        if failure == "type":
            value.header.dwType = 0
        elif failure == "callback":
            value.header.dwType = raw.RIM_TYPEKEYBOARD
        else:
            raise AssertionError(f"Unknown failure: {failure}")
        value.keyboard.VKey = 0x11
        value.keyboard.Flags = 2
        return ctypes.sizeof(value)

    monkeypatch.setattr(raw.user32, "GetMessageW", message)
    monkeypatch.setattr(raw.user32, "GetRawInputData", report)
    monkeypatch.setattr(raw.user32, "DispatchMessageW", dispatch)
    with pytest.raises(exception):
        raw.RawKeyboardListener(callback)._listen()
    dispatch.assert_called_once()
    if failure == "callback":
        callback.assert_called_once_with(0x11, 2)
    elif failure in ("read", "type"):
        callback.assert_not_called()
    else:
        raise AssertionError(f"Unknown failure: {failure}")


@pytest.mark.parametrize("failure", ["message", "read"])
@pytest.mark.parametrize("release_raises", [False, True])
def test_runtime_failure_releases_active_dictation_and_cleans_up(
    monkeypatch, machine, windows_raw, failure, release_raises,
):
    raw = windows_raw
    fail = threading.Event()
    register = Mock()
    destroy = Mock(return_value=True)
    dispatch = Mock()
    release = machine.on_release

    def on_release():
        release()
        if release_raises:
            raise RuntimeError("Release callback failed")

    def message(pointer, *args):
        if not fail.wait(2):
            raise RuntimeError("Failure was not triggered")
        if failure == "message":
            ctypes.set_last_error(5)
            return -1
        elif failure == "read":
            value = ctypes.cast(pointer, ctypes.POINTER(raw.wintypes.MSG)).contents
            value.message = raw.WM_INPUT
            value.lParam = 123
            return 1
        else:
            raise AssertionError(f"Unknown failure: {failure}")

    def report(*args):
        ctypes.set_last_error(5)
        return raw.UINT_ERROR

    machine.on_release = on_release
    monkeypatch.setattr(raw, "_open_window", lambda: 42)
    monkeypatch.setattr(raw, "_register", register)
    monkeypatch.setattr(raw, "_keyboard_registration",
                        lambda: raw.RawInputDevice(1, 6, raw.RIDEV_INPUTSINK, 42))
    monkeypatch.setattr(raw.user32, "DestroyWindow", destroy)
    monkeypatch.setattr(raw.user32, "GetMessageW", message)
    monkeypatch.setattr(raw.user32, "GetRawInputData", report)
    monkeypatch.setattr(raw.user32, "DispatchMessageW", dispatch)
    listener = HotkeyListener(machine)
    listener.start()
    worker = listener._listener
    try:
        machine.key_down("ctrl")
        machine.key_down("space")
        assert machine.active
        fail.set()
        worker._thread.join(2)
        assert not worker._thread.is_alive()
        assert machine.events == [("start", "dictate"), ("stop", None)]
        assert not machine.active
        assert machine._modifiers == set()
        assert isinstance(worker._error, OSError)
        assert worker._error.winerror == 5
        assert [call.args for call in register.call_args_list] == [
            (42, raw.RIDEV_INPUTSINK), (None, raw.RIDEV_REMOVE),
        ]
        destroy.assert_called_once_with(42)
        assert dispatch.call_count == (1 if failure == "read" else 0)
    finally:
        fail.set()
        listener.stop()
