"""
Glue between Akiba and a push provider: filtering, de-duplication and
sending on a worker thread so a slow tournament network never blocks the GUI.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

from src.push.base import (
    CLAIM_TYPE_TO_CODE, ERR_PGN, SAMPLE_VALUES, PushError, PushMessage, PushProvider,
    PushSettings, build_message, split_players,
)
from src.push.pushover import PushoverProvider
from src.i18n import tr

logger = logging.getLogger(__name__)



def make_provider(settings: PushSettings) -> PushProvider:
    # Only Pushover for now; other providers plug in here.
    return PushoverProvider(settings.user_key, settings.app_token)


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
        self._pool = QThreadPool.globalInstance()

    @property
    def active(self) -> bool:
        return self.settings.enabled and self.settings.configured

    def _dispatch(self, message: Optional[PushMessage],
                  on_done: Optional[Callable[[str], None]] = None) -> None:
        if message is None:
            return
        self._pool.start(_SendTask(make_provider(self.settings), message, on_done))

    def notify_claim(self, entry, follow=None) -> None:
        """
        Send a claim / reminder entry (ClaimEntry) if its type is enabled.
        For a followed player and an event chosen in the follow settings it is
        sent even when the type is off, with a star in the title.
        """
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
        parts = (entry.game_id or "").split("|")
        full_white, full_black = (parts[4], parts[5]) if len(parts) >= 6 else (white, black)
        followed = bool(follow and code in follow.events and follow.followed_in(full_white, full_black))
        message = build_message(self.settings, code, str(entry.board_number),
                                white, black, str(entry.move), force=followed)
        if message is not None and followed:
            message = PushMessage("★ " + message.title, message.body, message.priority)
        self._dispatch(message)

    def notify_game_end(self, board: str, white: str, black: str, result: str) -> None:
        """A game of a followed player has finished."""
        if not self.active:
            return
        message = build_message(self.settings, "GAME_END", board, white, black, result=result, force=True)
        if message is not None:
            self._dispatch(PushMessage("★ " + message.title, message.body, message.priority))

    def notify_pgn_error(self, url: str, reason: str) -> None:
        """A PGN source keeps failing (reported once per failure series by DownloadGames)."""
        if not self.active:
            return
        message = build_message(self.settings, ERR_PGN)
        if message is None:
            return
        details = "\n".join(part for part in (url, reason) if part)
        self._dispatch(PushMessage(message.title, f"{message.body}\n{details}".strip(),
                                   message.priority))

    def send_test(self, settings: PushSettings, code: str,
                  on_done: Callable[[str], None]) -> None:
        """Send a sample message using (possibly unsaved) settings."""
        if not settings.configured:
            on_done(tr("Enter the Pushover user key and API token first."))
            return
        test = PushSettings.from_dict(settings.to_dict())
        test.types[code].enabled = True
        message = build_message(test, code, **({} if code == ERR_PGN else SAMPLE_VALUES))
        self._pool.start(_SendTask(make_provider(test), message, on_done))

    def reset(self) -> None:
        """Forget what was sent (e.g. after the claims table was cleared)."""
        self._sent_keys.clear()
