from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QWidget

from audiorecorder.meetings.lifecycle import Lifecycle, Observation
from audiorecorder.ui import main_window
from audiorecorder.ui.main_window import MainWindow


class Tray:
    def __init__(self, icon, parent):
        self.menu = None

    def setContextMenu(self, menu):
        self.menu = menu

    def setToolTip(self, text):
        self.tooltip = text

    def show(self):
        pass


class Window(QWidget):
    _create_tray_icon = MainWindow._create_tray_icon
    _update_tray_actions = MainWindow._update_tray_actions
    _update_lifecycle_tray = MainWindow._update_lifecycle_tray
    _start_recording = MainWindow._start_recording
    _stop_recording = MainWindow._stop_recording
    _recording_start_blocked = MainWindow._recording_start_blocked

    def __init__(self):
        super().__init__()
        self._dictation_active = False
        self._encoder_queue = []
        self._active_encoders = []
        self._lifecycle = SimpleNamespace(controller=Lifecycle({}), process=lambda: None)
        self.opened = False

    def _show_from_tray(self):
        self.opened = True


@pytest.fixture
def window(monkeypatch):
    monkeypatch.setattr(main_window.sys, "platform", "darwin")
    monkeypatch.setattr(main_window, "QSystemTrayIcon", Tray)
    window = Window()
    window._create_tray_icon()
    yield window
    window.deleteLater()


def test_menu_actions_use_the_recording_lifecycle_without_opening_window(window):
    menu = window._tray_icon.menu
    assert [a.text() for a in menu.actions() if not a.isSeparator()] == [
        "Start Recording", "Stop Recording", "Open Meeting Recorder"
    ]
    menu.aboutToShow.emit()
    window._tray_start.trigger()
    controller = window._lifecycle.controller
    assert controller.state == "starting"
    recording = controller.recording
    window._tray_start.trigger()
    assert controller.recording == recording
    assert len([effect for effect in controller.drain() if effect.kind == "start"]) == 1
    assert controller.origin == "manual"
    assert not window.opened

    controller.started(controller.recording, True)
    window._update_lifecycle_tray("Recording")
    window._tray_stop.trigger()
    assert controller.state == "stopping"
    window._tray_stop.trigger()
    assert len([effect for effect in controller.drain() if effect.kind == "stop"]) == 1
    assert not window.opened
    menu.actions()[-1].trigger()
    assert window.opened


@pytest.mark.parametrize(
    "state,start,stop",
    [
        ("idle", True, False),
        ("start-pending", True, False),
        ("starting", False, False),
        ("recording", False, True),
        ("end-grace", False, True),
        ("stop-pending", False, True),
        ("stopping", False, False),
        ("capture-unresolved", False, False),
    ],
)
def test_menu_follows_manual_and_automatic_recording_states(window, state, start, stop):
    window._lifecycle.controller.state = state
    window._update_lifecycle_tray(state)
    assert window._tray_start.isEnabled() == start
    assert window._tray_stop.isEnabled() == stop
    assert all(action.isVisible() for action in window._tray_icon.menu.actions())


@pytest.mark.parametrize("blocker", ["dictation", "encoders", "closing"])
def test_opening_menu_refreshes_start_availability(window, blocker):
    window._update_tray_actions()
    assert window._tray_start.isEnabled()
    if blocker == "dictation":
        window._dictation_active = True
    elif blocker == "encoders":
        window._encoder_queue = [object(), object()]
        window._active_encoders = [object()]
    else:
        window._lifecycle.controller.closing = True
    window._tray_icon.menu.aboutToShow.emit()
    assert not window._tray_start.isEnabled()


def test_start_menu_takes_over_an_automatic_countdown_once(window):
    controller = window._lifecycle.controller
    controller.configure({"automatic_recording": True})
    now = [100.0]
    controller.clock = lambda: now[0]
    for timestamp in (100.0, 101.0):
        now[0] = timestamp
        controller.observe([
            Observation("call", "test", "active", timestamp, auto_start=True, observable=True)
        ])
    assert controller.state == "start-pending"
    controller.drain()

    window._tray_icon.menu.aboutToShow.emit()
    window._tray_start.trigger()
    window._tray_start.trigger()
    effects = controller.drain()
    assert controller.state == "starting"
    assert controller.origin == "manual"
    assert len([effect for effect in effects if effect.kind == "withdraw"]) == 1
    assert len([effect for effect in effects if effect.kind == "start"]) == 1


def test_start_rechecks_capacity_after_menu_opens(window):
    window._tray_icon.menu.aboutToShow.emit()
    assert window._tray_start.isEnabled()
    window._encoder_queue = [object(), object(), object()]
    window._tray_start.trigger()
    assert window._lifecycle.controller.state == "idle"
    assert not window._lifecycle.controller.drain()
