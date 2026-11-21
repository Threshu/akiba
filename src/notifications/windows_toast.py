"""
Windows toast notifications working with both Windows-Toasts 0.4.x
(ToastImageAndText2 / SetHeadline API, pinned in requirements.txt) and
1.x (Toast(text_fields=...) API, what pip installs today).
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def make_toaster(app_name: str = "Akiba"):
    """Return a WindowsToaster, or None when windows_toasts is unavailable."""
    try:
        from windows_toasts import WindowsToaster
        return WindowsToaster(app_name)
    except Exception:
        logger.warning("windows_toasts unavailable, desktop notifications disabled", exc_info=True)
        return None


def show_toast(toaster, headline: str, body: str, image_path: Optional[str] = None) -> None:
    """Show a short toast with a headline, body and optional image. Never raises."""
    if toaster is None:
        return
    try:
        import windows_toasts as wt
        if hasattr(wt, "Toast"):  # 1.x
            toast = wt.Toast(text_fields=[str(headline), str(body)])
            toast.duration = wt.ToastDuration.Short
            if image_path:
                toast.AddImage(wt.ToastDisplayImage.fromPath(image_path))
        else:  # 0.4.x
            toast = wt.ToastImageAndText2()
            toast.SetHeadline(str(headline))
            toast.SetBody(str(body))
            if image_path:
                toast.AddImage(wt.ToastDisplayImage.fromPath(image_path))
            toast.SetDuration(wt.ToastDuration("short"))
        toaster.show_toast(toast)
    except Exception:
        logger.warning("could not show desktop notification", exc_info=True)
