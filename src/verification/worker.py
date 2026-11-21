from __future__ import annotations

import logging
from typing import List

from PyQt6.QtCore import QThread, pyqtSignal

from src.verification.chessmanager import VerificationReport, verify

logger = logging.getLogger(__name__)


class VerifyResultsWorker(QThread):
    """Runs the ChessManager comparison off the GUI thread (it waits between requests)."""

    progress_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(object)  # VerificationReport

    def __init__(self, games: List, urls: List[str]) -> None:
        super().__init__()
        self.games = list(games)  # snapshot: the controller replaces its list on PGN updates
        self.urls = list(urls)

    def run(self) -> None:
        try:
            report = verify(self.games, self.urls, progress=self.progress_signal.emit)
        except Exception as exc:  # never kill the thread silently
            logger.exception("result verification failed")
            report = VerificationReport(errors=[str(exc)])
        self.finished_signal.emit(report)
