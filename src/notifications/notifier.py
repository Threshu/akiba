from __future__ import annotations

import platform

if platform.system() == "Darwin":
    from src.notifications.mac import Notification
elif platform.system() == "Windows":
    from src.notifications.windows_toast import make_toaster, show_toast


class NotificationManager:
    """
    Cross-platform notification manager.
    Wraps macOS NSUserNotification and Windows Toast notifications.
    """

    def __init__(self) -> None:
        self.system = platform.system()

        if self.system == "Darwin":
            self.notification = Notification()
        elif self.system == "Windows":
            self.toaster = make_toaster("Akiba")

    def show_notification(self, title: str, message: str, image_path: str | None = None) -> None:
        """
        Display a desktop notification.

        Args:
            title: Notification title
            message: Notification message body
            image_path: Optional path to icon image (Windows only)
        """
        if self.system == "Darwin":
            self.notification.notify(title, "", message)
        elif self.system == "Windows":
            show_toast(self.toaster, title, message, image_path)
