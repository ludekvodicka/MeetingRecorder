import sys
from types import SimpleNamespace

from audiorecorder.ui.macos_spaces import show_on_all_spaces


class FakeWidget:
    def __init__(self, native_id=42):
        self.native_id = native_id
        self.win_id_requested = False

    def winId(self):
        self.win_id_requested = True
        return self.native_id


class FakeWindow:
    def __init__(self, behavior=10):
        self.behavior = behavior

    def collectionBehavior(self):
        return self.behavior

    def setCollectionBehavior_(self, behavior):
        self.behavior = behavior


class RejectingWindow(FakeWindow):
    def setCollectionBehavior_(self, behavior):
        raise RuntimeError("AppKit rejected the collection behavior")


def install_fake_appkit(monkeypatch, window, *, supports_all_applications=True):
    pointers = []

    class FakeView:
        def window(self):
            return window

    def objc_object(*, c_void_p):
        pointers.append(c_void_p.value)
        return FakeView()

    appkit_constants = {
        "NSWindowCollectionBehaviorCanJoinAllSpaces": 1,
        "NSWindowCollectionBehaviorMoveToActiveSpace": 2,
        "NSWindowCollectionBehaviorFullScreenAuxiliary": 256,
    }
    if supports_all_applications:
        appkit_constants.update({
            "NSWindowCollectionBehaviorPrimary": 65_536,
            "NSWindowCollectionBehaviorAuxiliary": 131_072,
            "NSWindowCollectionBehaviorCanJoinAllApplications": 262_144,
        })
    monkeypatch.setitem(
        sys.modules,
        "AppKit",
        SimpleNamespace(**appkit_constants),
    )
    monkeypatch.setitem(
        sys.modules,
        "objc",
        SimpleNamespace(objc_object=objc_object),
    )
    return pointers


def test_non_macos_does_not_request_a_native_window():
    widget = FakeWidget()

    assert not show_on_all_spaces(widget, platform_name="linux")
    assert not widget.win_id_requested


def test_macos_window_joins_all_and_full_screen_spaces(monkeypatch):
    unrelated_behavior = 8
    auxiliary_behavior = 131_072
    window = FakeWindow(2 | unrelated_behavior | auxiliary_behavior)
    pointers = install_fake_appkit(monkeypatch, window)

    assert show_on_all_spaces(
        FakeWidget(123), platform_name="darwin", qt_platform_name="cocoa")
    assert pointers == [123]
    assert window.behavior == unrelated_behavior | 1 | 256 | 262_144


def test_older_appkit_uses_available_space_behaviors(monkeypatch):
    window = FakeWindow()
    install_fake_appkit(
        monkeypatch, window, supports_all_applications=False)

    assert show_on_all_spaces(
        FakeWidget(), platform_name="darwin", qt_platform_name="cocoa")
    assert window.behavior == 8 | 1 | 256


def test_missing_native_window_is_non_fatal(monkeypatch):
    install_fake_appkit(monkeypatch, None)

    assert not show_on_all_spaces(
        FakeWidget(), platform_name="darwin", qt_platform_name="cocoa")


def test_appkit_rejection_is_non_fatal(monkeypatch, caplog):
    install_fake_appkit(monkeypatch, RejectingWindow())

    assert not show_on_all_spaces(
        FakeWidget(), platform_name="darwin", qt_platform_name="cocoa")
    assert "could not make the meeting prompt visible" in caplog.text


def test_non_cocoa_qt_backend_does_not_wrap_its_window_id():
    widget = FakeWidget()

    assert not show_on_all_spaces(
        widget, platform_name="darwin", qt_platform_name="offscreen")
    assert not widget.win_id_requested
