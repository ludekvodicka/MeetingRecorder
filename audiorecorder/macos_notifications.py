"""Native notification transport. Policy and capture remain on the Qt thread."""

import logging
import sys
from dataclasses import dataclass

from PyQt6.QtCore import QObject, pyqtSignal


@dataclass(frozen=True)
class NotificationStatus:
    authorization: str = "Unknown"
    alerts: bool = False
    sound: bool = False
    error: str = ""

    @property
    def actionable(self):
        return self.authorization == "Authorized" and self.alerts and not self.error


CATEGORIES = {
    "start": (("start", "Start Now"), ("cancel", "Cancel")),
    "stop": (("stop", "Stop Now"), ("keep", "Keep Recording")),
    "recording": (("open", "Open Recorder"), ("stop", "Stop")),
    "saved": (("open-recording", "Open Recording"), ("reveal", "Show in Finder")),
    "advisory": (("start", "Start Recording"), ("cancel", "Not Now")),
    "error": (("open", "Open Recorder"),),
}
_DELEGATE_CLASS = None


def _delegate_class():
    global _DELEGATE_CLASS
    if _DELEGATE_CLASS is not None:
        return _DELEGATE_CLASS
    import objc
    import UserNotifications as UN
    from Foundation import NSObject

    class MRNotificationDelegate(
        NSObject, protocols=[objc.protocolNamed("UNUserNotificationCenterDelegate")]
    ):
        def userNotificationCenter_willPresentNotification_withCompletionHandler_(
            self, center, notification, completion
        ):
            completion(
                UN.UNNotificationPresentationOptionBanner
                | UN.UNNotificationPresentationOptionList
                | UN.UNNotificationPresentationOptionSound
            )

        def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(
            self, center, response, completion
        ):
            try:
                request = response.notification().request()
                category = str(request.content().categoryIdentifier())
                action = str(response.actionIdentifier())
                if action == UN.UNNotificationDefaultActionIdentifier:
                    action = "open"
                elif action == UN.UNNotificationDismissActionIdentifier:
                    action = "dismiss"
                elif action.startswith("mr."):
                    action = action[3:]
                else:
                    return
                self.owner.response.emit(str(request.identifier()), category, action)
            except Exception:
                logging.getLogger(__name__).exception("Invalid native notification response")
            finally:
                completion()

    _DELEGATE_CLASS = MRNotificationDelegate
    return _DELEGATE_CLASS


class Notifications(QObject):
    response = pyqtSignal(str, str, str)
    submitted = pyqtSignal(str, bool)
    status_changed = pyqtSignal(object)

    def __init__(self, center=None):
        super().__init__()
        self.center = center
        self.delegate = None
        self.status = NotificationStatus()
        self.closed = False
        self._known = set()
        self.status_changed.connect(self._set_status)
        if center is None:
            if sys.platform != "darwin" or not getattr(sys, "frozen", False):
                self.status = NotificationStatus(
                    error="Native notifications require the installed app"
                )
                return
            try:
                import UserNotifications as UN

                self.center = UN.UNUserNotificationCenter.currentNotificationCenter()
                self.delegate = _delegate_class().alloc().init()
                self.delegate.owner = self
                self.center.setDelegate_(self.delegate)
                categories = []
                for kind, actions in CATEGORIES.items():
                    native_actions = [
                        UN.UNNotificationAction.actionWithIdentifier_title_options_(
                            f"mr.{key}",
                            title,
                            UN.UNNotificationActionOptionForeground
                            if key in ("open", "open-recording", "reveal")
                            else 0,
                        )
                        for key, title in actions
                    ]
                    categories.append(
                        UN.UNNotificationCategory.categoryWithIdentifier_actions_intentIdentifiers_options_(
                            f"mr.{kind}",
                            native_actions,
                            [],
                            UN.UNNotificationCategoryOptionCustomDismissAction,
                        )
                    )
                self.center.setNotificationCategories_(set(categories))
                # Only this application's obsolete notifications are owned here.
                self.center.removeAllPendingNotificationRequests()
                self.center.removeAllDeliveredNotifications()
            except Exception as error:
                self.center = None
                self.status = NotificationStatus(error=str(error))
        self.refresh()

    def _set_status(self, status):
        self.status = status

    def refresh(self):
        if self.center is None or self.closed:
            return

        def received(settings):
            try:
                names = {
                    0: "Not requested",
                    1: "Denied",
                    2: "Authorized",
                    3: "Provisional",
                    4: "Ephemeral",
                }
                status = NotificationStatus(
                    names.get(int(settings.authorizationStatus()), "Unknown"),
                    int(settings.alertSetting()) == 2,
                    int(settings.soundSetting()) == 2,
                )
            except Exception as error:
                status = NotificationStatus(error=str(error))
            # Assignment and UI notifications are processed by the QObject's owner thread.
            self.status_changed.emit(status)

        try:
            self.center.getNotificationSettingsWithCompletionHandler_(received)
        except Exception as error:
            self.status_changed.emit(NotificationStatus(error=str(error)))

    def request(self):
        if self.center is None or self.closed:
            return
        import UserNotifications as UN

        self.center.requestAuthorizationWithOptions_completionHandler_(
            UN.UNAuthorizationOptionAlert | UN.UNAuthorizationOptionSound,
            lambda granted, error: self.refresh(),
        )

    def send(self, identifier, kind, title, body=""):
        if self.closed or self.center is None or not self.status.actionable:
            self.submitted.emit(identifier, False)
            return False
        import UserNotifications as UN

        content = UN.UNMutableNotificationContent.alloc().init()
        content.setTitle_(title)
        content.setBody_(body)
        content.setCategoryIdentifier_(f"mr.{kind}")
        content.setSound_(UN.UNNotificationSound.defaultSound())
        request = UN.UNNotificationRequest.requestWithIdentifier_content_trigger_(
            identifier, content, None
        )
        self._known.add(identifier)

        def completed(error):
            self.submitted.emit(identifier, error is None)
            if self.closed or identifier not in self._known:
                self.withdraw(identifier)

        try:
            self.center.addNotificationRequest_withCompletionHandler_(request, completed)
        except Exception:
            self.submitted.emit(identifier, False)
            return False
        return True

    def withdraw(self, identifier):
        self._known.discard(identifier)
        if self.center is not None:
            self.center.removePendingNotificationRequestsWithIdentifiers_([identifier])
            self.center.removeDeliveredNotificationsWithIdentifiers_([identifier])

    def close(self):
        self.closed = True
        for identifier in tuple(self._known):
            self.withdraw(identifier)
        # Retain the delegate until application teardown so native completions stay valid.
