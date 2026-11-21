from __future__ import annotations

from typing import Callable, List

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView, QLabel,
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


class VerificationResultsDialog(QDialog):
    """Result of the comparison; clicking a row opens the game in the Board Viewer."""

    def __init__(self, report: VerificationReport, games: List, open_game: Callable[[int], None],
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Akiba - Result verification (ChessManager)"))
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.resize(860, 420)
        self._open_game = open_game
        layout = QVBoxLayout(self)

        summary = QLabel()
        summary.setWordWrap(True)
        if report.discrepancies:
            summary.setText("<b>" + tr("{n} results differ from ChessManager. "
                                       "Click a row to open the board.").format(n=len(report.discrepancies)) + "</b>")
            summary.setStyleSheet("color: #7a0000;")
        elif report.checked:
            summary.setText("<b>" + tr("All results match ChessManager ({n} games checked).").format(
                n=report.checked) + "</b>")
            summary.setStyleSheet("color: #006000;")
        else:
            summary.setText("<b>" + tr("No game could be matched with ChessManager.") + "</b>")
        layout.addWidget(summary)

        rows = [(d.game_index, d.round, d.board, d.white, d.black,
                 RESULT_TEXT.get(d.pgn_result, d.pgn_result), RESULT_TEXT.get(d.cm_result, d.cm_result),
                 tr(d.note) if d.note else (tr("missing in transmission") if d.pgn_result == "*"
                                            else tr("different result")))
                for d in report.discrepancies]
        rows += [(idx, rnd, "", white, black, "", "", tr("not found in ChessManager"))
                 for idx, rnd, white, black in report.not_found]

        if rows:
            headers = [tr("Round"), tr("Board"), tr("White"), tr("Black"),
                       tr("Transmission"), "ChessManager", tr("Problem")]
            self.table = QTableWidget(len(rows), len(headers))
            self.table.setHorizontalHeaderLabels(headers)
            self.table.verticalHeader().setVisible(False)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            for r, (idx, *cells) in enumerate(rows):
                is_diff = r < len(report.discrepancies)
                for c, value in enumerate(cells):
                    item = QTableWidgetItem(str(value))
                    item.setData(Qt.ItemDataRole.UserRole, idx)
                    if c not in (2, 3):
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    item.setBackground(QColor("#f8d0d0" if is_diff else "#eeeeee"))
                    self.table.setItem(r, c, item)
            header = self.table.horizontalHeader()
            header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
            self.table.cellClicked.connect(self._on_row_clicked)
            layout.addWidget(self.table, 1)

        if report.pending_in_cm:
            layout.addWidget(QLabel(tr("{n} finished games have no result in ChessManager yet.").format(
                n=report.pending_in_cm)))
        if report.errors:
            errors = QLabel(tr("Could not read from ChessManager:") + "\n" + "\n".join(report.errors))
            errors.setStyleSheet("color: #aa0000;")
            errors.setWordWrap(True)
            layout.addWidget(errors)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_row_clicked(self, row: int, _column: int) -> None:
        index = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if index is not None:
            self._open_game(int(index))
