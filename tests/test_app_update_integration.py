from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from audiorecorder import app as module
from audiorecorder import macos_launch, macos_notifications


class Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in self.callbacks:
            callback(*args)


@pytest.mark.parametrize('login', [False, True])
@pytest.mark.parametrize('session_ending', [False, True])
def test_startup_combines_notifications_updater_login_and_safe_quit(
    login, session_ending, tmp_path, monkeypatch,
):
    application = Mock()
    application.commitDataRequest = Signal()
    application.aboutToQuit = Signal()
    application.exec.return_value = 0
    window, updates, instance, notifications = Mock(), Mock(), Mock(), Mock()
    launch = SimpleNamespace(login=login, finished=True)
    config = {'output_dir': str(tmp_path)}
    saved = Mock()
    raised = Mock()
    constructor = Mock(return_value=window)
    monkeypatch.setattr(module.sys, 'argv', ['meeting-recorder'])
    monkeypatch.setattr(module, '_configure_logging', lambda: None)
    monkeypatch.setattr(module, 'QApplication', lambda argv: application)
    monkeypatch.setattr(module, 'SingleInstance', lambda name, legacy: instance)
    monkeypatch.setattr(module, 'MainWindow', constructor)
    monkeypatch.setattr(module, 'QuitFilter', lambda value: 'quit-filter')
    monkeypatch.setattr(module, 'UpdateIndicator', lambda *args: 'update-indicator')
    monkeypatch.setattr(module.AppUpdates, 'create', lambda parent: updates)
    monkeypatch.setattr(module, 'load_config', lambda: config)
    monkeypatch.setattr(module, 'save_config', saved)
    monkeypatch.setattr(module, '_raise', raised)
    monkeypatch.setattr(module.QTimer, 'singleShot', lambda delay, callback: callback())
    monkeypatch.setattr(macos_launch, 'LaunchContext', lambda: launch)
    monkeypatch.setattr(macos_notifications, 'Notifications', lambda: notifications)

    with pytest.raises(SystemExit) as stopped:
        module.main()
    assert stopped.value.code == 0
    constructor.assert_called_once_with(config, notifications)
    saved.assert_called_once_with(config)
    updates.set_busy_check.assert_called_once_with(window.busy_reason)
    updates.start.assert_called_once_with()
    window.statusBar().addPermanentWidget.assert_called_once_with('update-indicator')
    application.installEventFilter.assert_called_once_with('quit-filter')
    assert raised.call_count == (0 if login else 1)
    if session_ending:
        application.commitDataRequest.emit(object())
    application.aboutToQuit.emit()
    updates.quit.assert_called_once_with(session_ending)
    assert window.end_session.call_count == (1 if session_ending else 0)
    instance.close.assert_called_once_with()
