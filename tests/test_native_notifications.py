import sys
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QApplication

from audiorecorder.macos_notifications import Notifications, NotificationStatus, _delegate_class


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class Center:
    def getNotificationSettingsWithCompletionHandler_(self, callback):
        callback(
            SimpleNamespace(
                authorizationStatus=lambda: 2, alertSetting=lambda: 2, soundSetting=lambda: 1
            )
        )

    def removePendingNotificationRequestsWithIdentifiers_(self, identifiers):
        self.pending = identifiers

    def removeDeliveredNotificationsWithIdentifiers_(self, identifiers):
        self.delivered = identifiers


def test_status_is_observed_without_requesting_permission(app):
    adapter = Notifications(Center())
    assert adapter.status == NotificationStatus("Authorized", True, False)
    adapter.withdraw("old")
    assert adapter.center.pending == ["old"]
    assert adapter.center.delivered == ["old"]


def test_unsupported_source_runtime_degrades_without_framework_access(app, monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    adapter = Notifications()
    results = []
    adapter.submitted.connect(lambda identifier, ok: results.append((identifier, ok)))
    assert not adapter.send("op", "start", "Test")
    assert results == [("op", False)]


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS selector boundary")
def test_real_native_delegate_completes_malformed_and_valid_callbacks_once(app):
    adapter = Notifications(Center())
    delegate = _delegate_class().alloc().init()
    delegate.owner = adapter
    completions = []
    received = []
    adapter.response.connect(lambda *args: received.append(args))
    request = SimpleNamespace(
        identifier=lambda: "op",
        content=lambda: SimpleNamespace(categoryIdentifier=lambda: "mr.start"),
    )
    response = SimpleNamespace(
        actionIdentifier=lambda: "mr.cancel",
        notification=lambda: SimpleNamespace(request=lambda: request),
    )
    for value in (response, None):
        delegate.userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(
            None, value, lambda: completions.append(True)
        )
    assert len(completions) == 2
    assert received == [("op", "mr.start", "cancel")]
    presentation = []
    delegate.userNotificationCenter_willPresentNotification_withCompletionHandler_(
        None, None, presentation.append
    )
    assert len(presentation) == 1 and presentation[0] != 0


def test_dismissing_saved_notification_does_not_open_audio():
    from audiorecorder.meetings.lifecycle import Lifecycle
    from audiorecorder.ui.lifecycle_adapter import LifecycleAdapter

    opened = []
    stub = SimpleNamespace(
        actions={"saved": ("saved", "/private/recording.m4a")},
        window=SimpleNamespace(_open_saved=lambda *args, **kw: opened.append(args)),
        controller=Lifecycle({}),
        process=lambda: None,
    )
    LifecycleAdapter._response(stub, "saved", "mr.saved", "dismiss")
    assert not opened
