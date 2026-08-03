from audiorecorder.ui.main_window import MainWindow


class FakeEvent:
    def __init__(self):
        self.accepted = False
        self.ignored = False

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
    return stub


def test_close_waits_for_an_active_encoder():
    window = closing_stub(encoders=[object()])
    event = FakeEvent()

    MainWindow.closeEvent(window, event)

    assert event.ignored
    assert not event.accepted
    assert "Finishing background work" in window._status_bar.messages[0][0]


def test_close_waits_for_transcription_or_cleanup():
    window = closing_stub(busy_paths={"meeting.m4a"})
    event = FakeEvent()

    MainWindow.closeEvent(window, event)

    assert event.ignored
    assert not event.accepted
