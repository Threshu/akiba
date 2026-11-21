from __future__ import annotations

from typing import Callable, List

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QPlainTextEdit, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from src.i18n import tr
from src.verification.chessmanager import VerificationReport, tournament_id

RESULT_TEXT = {"1-0": "1-0", "0-1": "0-1", "1/2-1/2": "½-½", "*": "—", "0-0": "0-0"}


class ChessManagerSourcesDialog(QDialog):
    """Links of the ChessManager tournaments used as the source of truth for results."""

    def __init__(self, urls: List[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Akiba - ChessManager tournaments"))
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.resize(640, 300)
        layout = QVBoxLayout(self)
        hint = QLabel(tr("One tournament link per line, e.g. "
                         "https://www.chessmanager.com/tournaments/5437727486771200"))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.edit = QPlainTextEdit("\n".join(urls))
        layout.addWidget(self.edit, 1)
        self.info = QLabel()
        layout.addWidget(self.info)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.edit.textChanged.connect(self._update_info)
        self._update_info()

    def urls(self) -> List[str]:
        return [line.strip() for line in self.edit.toPlainText().splitlines() if line.strip()]

    def _update_info(self) -> None:
        bad = [u for u in self.urls() if not tournament_id(u)]
        if bad:
            self.info.setText(tr("Not a ChessManager tournament link: {url}").format(url=bad[0]))
            self.info.setStyleSheet("color: #aa0000;")
        else:
            self.info.setText(tr("Tournaments: {n}").format(n=len(self.urls())))
            self.info.setStyleSheet("color: #505050;")


def _banner(symbol: str, title: str, details: str, background: str, color: str) -> QFrame:
    """Coloured summary box with a large round symbol."""
    frame = QFrame()
    frame.setStyleSheet(f"QFrame#banner {{ background: {background}; border: 1px solid {color};"
                        f" border-radius: 8px; }}")
    frame.setObjectName("banner")
    row = QHBoxLayout(frame)
    row.setContentsMargins(16, 14, 16, 14)
    row.setSpacing(16)
    icon = QLabel(symbol)
    icon.setFixedSize(52, 52)
    icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
    icon.setStyleSheet(f"background: {color}; color: white; border-radius: 26px;"
                       f" font-size: 28px; font-weight: bold;")
    row.addWidget(icon)
    texts = QVBoxLayout()
    headline = QLabel(title)
    headline.setStyleSheet(f"color: {color}; font-size: 17px; font-weight: bold; background: transparent;")
    headline.setWordWrap(True)
    sub = QLabel(details)
    sub.setStyleSheet("color: #444444; background: transparent;")
    sub.setWordWrap(True)
    texts.addWidget(headline)
    texts.addWidget(sub)
    row.addLayout(texts, 1)
    return frame


class VerificationResultsDialog(QDialog):
    """Result of the comparison; clicking a row opens the game in the Board Viewer."""

    def __init__(self, report: VerificationReport, games: List, open_game: Callable[[int], None],
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Akiba - Result verification (ChessManager)"))
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.resize(1080, 460)
        self._open_game = open_game
        layout = QVBoxLayout(self)

        # Banner: green when everything matches, red with discrepancies, grey otherwise
        details = tr("Games checked: {n}").format(n=report.checked)
        details += " · " + tr("Tournaments: {n}").format(n=report.tournaments)
        if report.rounds:
            details += " · " + tr("Rounds: {rounds}").format(rounds=", ".join(report.rounds))
        if report.finished_at:
            details += " · " + tr("Checked at {time}").format(time=report.finished_at)
        if report.pending_in_cm:
            details += "<br>" + tr("Finished games without a result in ChessManager: {n} (listed below)").format(
                n=len(report.pending_in_cm))
        if report.discrepancies:
            layout.addWidget(_banner("!", tr("{n} results differ from ChessManager").format(
                n=len(report.discrepancies)), tr("Click a row to open the game on the board.") + "<br>" + details,
                "#fdecec", "#b42318"))
        elif report.checked and not report.errors:
            layout.addWidget(_banner("✓", tr("All results match ChessManager"), details,
                                     "#e9f7ef", "#1e7b3c"))
        elif report.errors:
            layout.addWidget(_banner("?", tr("Could not read from ChessManager"), details,
                                     "#fff4e5", "#9a5b00"))
        else:
            layout.addWidget(_banner("?", tr("No game could be matched with ChessManager."), details,
                                     "#f2f2f2", "#505050"))

        # (game index, colour, cells...) - red: differs, orange: no result in CM yet, grey: not found
        rows = [(d.game_index, "#f8d0d0", d.tournament, d.round, d.board, d.white, d.black,
                 RESULT_TEXT.get(d.pgn_result, d.pgn_result), RESULT_TEXT.get(d.cm_result, d.cm_result),
                 tr(d.note) if d.note else (tr("missing in transmission") if d.pgn_result == "*"
                                            else tr("different result")))
                for d in report.discrepancies]
        rows += [(d.game_index, "#ffe8bf", d.tournament, d.round, d.board, d.white, d.black,
                  RESULT_TEXT.get(d.pgn_result, d.pgn_result), "—", tr(d.note))
                 for d in report.pending_in_cm]
        rows += [(idx, "#eeeeee", "", rnd, "", white, black, "", "", tr("not found in ChessManager"))
                 for idx, rnd, white, black in report.not_found]

        if rows:
            headers = [tr("Tournament"), tr("Round"), tr("Board"), tr("White"), tr("Black"),
                       tr("Transmission"), "ChessManager", tr("Problem")]
            self.table = QTableWidget(len(rows), len(headers))
            self.table.setHorizontalHeaderLabels(headers)
            self.table.verticalHeader().setVisible(False)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            for r, (idx, colour, *cells) in enumerate(rows):
                for c, value in enumerate(cells):
                    item = QTableWidgetItem(str(value))
                    item.setData(Qt.ItemDataRole.UserRole, idx)
                    if c not in (0, 3, 4):
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    item.setBackground(QColor(colour))
                    if c == 0:
                        item.setToolTip(str(value))
                    self.table.setItem(r, c, item)
            header = self.table.horizontalHeader()
            header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(0, 220)
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
            self.table.cellClicked.connect(self._on_row_clicked)
            layout.addWidget(self.table, 1)

        if report.errors:
            errors = QLabel(tr("Could not read from ChessManager:") + "\n" + "\n".join(report.errors))
            errors.setStyleSheet("color: #aa0000;")
            errors.setWordWrap(True)
            layout.addWidget(errors)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if not rows:
            self.setMinimumWidth(600)
            self.adjustSize()

    def _on_row_clicked(self, row: int, _column: int) -> None:
        index = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if index is not None:
            self._open_game(int(index))
