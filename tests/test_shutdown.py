from audiorecorder.ui.main_window import MainWindow


class FakeEvent:
    def __init__(self, spontaneous=False):
        self.accepted = False
        self.ignored = False
        self._spontaneous = spontaneous

    def spontaneous(self):
        return self._spontaneous

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.ignored = True


class FakeStatusBar:
    def __init__(self):
        self.messages = []

    def showMessage(self, message, timeout=0):
        self.messages.append((message, timeout))


def closing_stub(*, encoders=(), busy_paths=()):
    class Stub:
        pass

    stub = Stub()
    stub._is_recording = False
    stub._dictation_active = False
    stub._active_encoders = list(encoders)
    stub._busy_paths = set(busy_paths)
    stub._status_bar = FakeStatusBar()
    stub._tray_icon = None
    stub._quitting = False
    return stub


class FakeTray:
    def __init__(self):
        self.messages = []

    def isVisible(self):
        return True

    def showMessage(self, *message):
        self.messages.append(message)


def test_red_close_hides_into_the_menu_bar_without_stopping_services():
    window = closing_stub()
    window._tray_icon = FakeTray()
    window.hidden = False
    window.hide = lambda: setattr(window, "hidden", True)
    event = FakeEvent(spontaneous=True)

    MainWindow.closeEvent(window, event)

    assert window.hidden
    assert event.ignored
    assert window._tray_icon is not None


def test_close_waits_for_an_active_encoder():
    window = closing_stub(encoders=[object()])
    window._quitting = True
    event = FakeEvent()

    MainWindow.closeEvent(window, event)

    assert event.ignored
    assert not event.accepted
    assert window._quitting
    assert "Finishing background work" in window._status_bar.messages[0][0]


def test_close_waits_for_transcription_or_cleanup():
    window = closing_stub(busy_paths={"meeting.m4a"})
    window._quitting = True
    event = FakeEvent()

    MainWindow.closeEvent(window, event)

    assert event.ignored
    assert not event.accepted
    assert window._quitting


def test_close_waits_for_thread_completion_after_result_clears_busy_path():
    from types import SimpleNamespace

    window = closing_stub()
    window._quitting = True
    window._processing = SimpleNamespace(is_running=lambda: True)
    window._background_threads_running = lambda: MainWindow._background_threads_running(window)
    event = FakeEvent()
    MainWindow.closeEvent(window, event)
    assert event.ignored
    assert not event.accepted
