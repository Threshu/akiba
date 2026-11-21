"""
Glue between Akiba and a push provider: filtering, de-duplication and
sending on a worker thread so a slow tournament network never blocks the GUI.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

from src.push.base import (
    CLAIM_TYPE_TO_CODE, ERR_PGN, PushError, PushMessage, PushProvider,
    PushSettings, build_message, split_players,
)
from src.push.pushover import PushoverProvider

logger = logging.getLogger(__name__)

# After a PGN error was reported, downloads must stay OK this long before a
# new error is reported again (several sources may alternate OK / ERROR).
PGN_ERROR_RESET_SECONDS = 60


def make_provider(settings: PushSettings) -> PushProvider:
    # Only Pushover for now; other providers plug in here.
    return PushoverProvider(settings.user_key, settings.app_token,
                            settings.emergency_retry, settings.emergency_expire)


class _Signals(QObject):
    finished = pyqtSignal(str)   # "" on success, error text otherwise


class _SendTask(QRunnable):
    def __init__(self, provider: PushProvider, message: PushMessage,
                 on_done: Optional[Callable[[str], None]] = None) -> None:
        super().__init__()
        self.provider = provider
        self.message = message
        self.signals = _Signals()
        if on_done is not None:
            self.signals.finished.connect(on_done)

    def run(self) -> None:
        error = ""
        try:
            self.provider.send(self.message)
        except PushError as exc:
            error = str(exc)
        except Exception as exc:  # never let a worker crash the app
            error = f"Unexpected error: {exc}"
        if error:
            logger.warning("push failed (%s): %s", self.message.title, error)
        self.signals.finished.emit(error)


class PushNotifier:
    def __init__(self, settings: Optional[PushSettings] = None) -> None:
        self.settings = settings or PushSettings()
        self._sent_keys: set = set()
        self._pgn_error_notified = False
        self._last_pgn_error = 0.0
        self._pool = QThreadPool.globalInstance()

    @property
    def active(self) -> bool:
        return self.settings.enabled and self.settings.configured

    def _dispatch(self, message: Optional[PushMessage],
                  on_done: Optional[Callable[[str], None]] = None) -> None:
        if message is None:
            return
        self._pool.start(_SendTask(make_provider(self.settings), message, on_done))

    def notify_claim(self, entry) -> None:
        """Send a claim / reminder entry (ClaimEntry) if its type is enabled."""
        if not self.active or getattr(entry, "cleared", False):
            return
        code = CLAIM_TYPE_TO_CODE.get(entry.type)
        if code is None:
            return
        key = (code, entry.game_id or entry.players, entry.move_counter)
        if key in self._sent_keys:
            return
        self._sent_keys.add(key)
        white, black = split_players(entry.players)
        self._dispatch(build_message(self.settings, code, str(entry.board_number),
                                     white, black, str(entry.move)))

    def on_download_status(self, is_error: bool, is_ok: bool) -> None:
        """One notification per series of PGN download errors."""
        now = time.monotonic()
        if is_error:
            self._last_pgn_error = now
            if not self._pgn_error_notified:
                self._pgn_error_notified = True
                if self.active:
                    self._dispatch(build_message(self.settings, ERR_PGN))
        elif is_ok and self._pgn_error_notified:
            if now - self._last_pgn_error >= PGN_ERROR_RESET_SECONDS:
                self._pgn_error_notified = False

    def send_test(self, settings: PushSettings, code: str,
                  on_done: Callable[[str], None]) -> None:
        """Send a sample message using (possibly unsaved) settings."""
        if not settings.configured:
            on_done("Enter the Pushover user key and API token first.")
            return
        test = PushSettings.from_dict(settings.to_dict())
        test.types[code].enabled = True
        message = build_message(test, code, "6", "Nowak Jan", "Kowalski Piotr", "34. Nf3")
        self._pool.start(_SendTask(make_provider(test), message, on_done))

    def reset(self) -> None:
        """Forget what was sent (e.g. after the claims table was cleared)."""
        self._sent_keys.clear()
