from types import SimpleNamespace

import pytest

from audiorecorder.ui.main_window import MainWindow


def window():
    return SimpleNamespace(
        _quitting=False, _exit_ready=False, _is_recording=False, _active_encoders=[],
        _encoder_queue=[], _busy_paths=set(), _dictation_active=False,
        _lifecycle=SimpleNamespace(controller=SimpleNamespace(state='idle')),
        _processing=SimpleNamespace(is_running=lambda: False),
    )


def test_idle_custom_window_allows_update_actions():
    assert MainWindow.busy_reason(window()) is None


@pytest.mark.parametrize('blocker', [
    'closing', 'recording', 'starting', 'pending-encoder', 'saving', 'queued-job',
    'finishing-thread',
])
def test_update_busy_guard_covers_custom_work(blocker):
    stub = window()
    if blocker == 'closing':
        stub._quitting = True
    elif blocker == 'recording':
        stub._is_recording = True
    elif blocker == 'starting':
        stub._lifecycle.controller.state = 'starting'
    elif blocker == 'pending-encoder':
        stub._encoder_queue = [object()]
    elif blocker == 'saving':
        stub._active_encoders = [object()]
    elif blocker == 'queued-job':
        stub._busy_paths = {'queued.m4a'}
    elif blocker == 'finishing-thread':
        stub._processing.is_running = lambda: True
    else:
        raise ValueError(blocker)
    assert MainWindow.busy_reason(stub)


def test_dictation_never_blocks_an_update():
    stub = window()
    stub._dictation_active = True
    assert MainWindow.busy_reason(stub) is None


def test_a_finished_close_lets_a_ready_update_install_on_quit():
    """aboutToQuit runs after closeEvent has stopped all work and set _exit_ready."""
    stub = window()
    stub._quitting = True
    stub._exit_ready = True
    assert MainWindow.busy_reason(stub) is None
