"""
Live board monitor: follow one or more boards with everything an arbiter
needs at a glance - position, engine evaluation, clocks, last move,
repetition count of the current position and the 50/75-move counter.
"""
from __future__ import annotations

import logging
import queue
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import chess
import chess.svg
from PyQt6.QtCore import QByteArray, Qt, QThread, pyqtSignal
from PyQt6.QtSvgWidgets import QSvgWidget
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QCompleter, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QProgressBar, QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget,
)

from src.i18n import tr

logger = logging.getLogger(__name__)

PIECE_VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}


def _fmt_clock(seconds: Optional[float]) -> str:
    if seconds is None:
        return "--:--"
    seconds = int(seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


@dataclass
class BoardState:
    fen: str
    board: chess.Board
    last_move: str
    plies: int
    to_move: str              # "white" / "black"
    white_clock: Optional[float]
    black_clock: Optional[float]
    repetition: int           # how many times the current position occurred
    halfmove_clock: int       # half-moves since last capture / pawn move
    material: int             # white minus black, in pawns
    status: str               # result or special state
    last_move_uci: Optional[chess.Move]


def analyse_game(game) -> BoardState:
    """Everything shown on a card, from a python-chess game."""
    board = game.board()
    counts: Dict[object, int] = {}
    counts[board._transposition_key()] = 1
    clocks = {chess.WHITE: None, chess.BLACK: None}
    last_san, last_move = "", None
    for node in game.mainline():
        mover = board.turn
        number = board.fullmove_number
        try:
            san = board.san(node.move)
            board.push(node.move)
        except (ValueError, AssertionError):
            break
        last_san = f"{number}.{san}" if mover == chess.WHITE else f"{number}...{san}"
        last_move = node.move
        key = board._transposition_key()
        counts[key] = counts.get(key, 0) + 1
        clock = node.clock()
        if clock is not None:
            clocks[mover] = clock

    material = sum(PIECE_VALUES.get(p.piece_type, 0) * (1 if p.color == chess.WHITE else -1)
                   for p in board.piece_map().values())
    result = game.headers.get("Result", "*")
    if result != "*":
        status = result
    elif board.is_checkmate():
        status = tr("Checkmate")
    elif board.is_stalemate():
        status = tr("Stalemate")
    elif board.is_insufficient_material():
        status = tr("Insufficient material")
    elif board.is_check():
        status = tr("Check")
    else:
        status = ""
    return BoardState(
        fen=board.fen(), board=board, last_move=last_san, plies=len(board.move_stack),
        to_move="white" if board.turn == chess.WHITE else "black",
        white_clock=clocks[chess.WHITE], black_clock=clocks[chess.BLACK],
        repetition=counts.get(board._transposition_key(), 1),
        halfmove_clock=board.halfmove_clock, material=material, status=status,
        last_move_uci=last_move,
    )


class EvalWorker(QThread):
    """Stockfish evaluation of monitored positions, newest position per board only."""

    evaluated = pyqtSignal(str, str, str, float)  # game id, fen, text (+1.25 / M5), pawns (white POV)

    def __init__(self, engine_path: str, seconds: float = 0.4) -> None:
        super().__init__()
        self.engine_path = engine_path
        self.seconds = seconds
        self._jobs: "queue.Queue" = queue.Queue()
        self._latest: Dict[str, str] = {}
        self._running = True

    def submit(self, gid: str, fen: str) -> None:
        if self._latest.get(gid) == fen:
            return
        self._latest[gid] = fen
        self._jobs.put((gid, fen))

    def stop(self) -> None:
        self._running = False
        self._jobs.put(None)
        self.wait(3000)

    def run(self) -> None:
        import subprocess
        import sys
        import chess.engine
        try:
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            engine = chess.engine.SimpleEngine.popen_uci(self.engine_path, creationflags=flags)
            engine.configure({"Threads": 1, "Hash": 16})
        except Exception:
            logger.warning("live monitor: engine not available", exc_info=True)
            return
        try:
            while self._running:
                job = self._jobs.get()
                if job is None:
                    break
                gid, fen = job
                if self._latest.get(gid) != fen:
                    continue  # a newer position is queued
                try:
                    board = chess.Board(fen)
                    if board.is_game_over():
                        continue
                    info = engine.analyse(board, chess.engine.Limit(time=self.seconds))
                    score = info["score"].white()
                    if score.is_mate():
                        mate = score.mate()
                        text, pawns = f"M{abs(mate)}" if mate > 0 else f"-M{abs(mate)}", (99.0 if mate > 0 else -99.0)
                    else:
                        pawns = score.score() / 100.0
                        text = f"{pawns:+.2f}"
                    self.evaluated.emit(gid, fen, text, pawns)
                except Exception:
                    logger.debug("live monitor eval failed", exc_info=True)
        finally:
            try:
                engine.quit()
            except Exception:
                pass


class BoardCard(QFrame):
    """One monitored board."""

    def __init__(self, gid: str, on_remove: Callable[[str], None], on_open: Callable[[str], None]) -> None:
        super().__init__()
        self.gid = gid
        self.fen = ""
        self.setObjectName("boardCard")
        self.setMinimumWidth(460)
        self.setStyleSheet("QFrame#boardCard { background: white; border: 1px solid #d0d0d0; border-radius: 8px; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)

        top = QHBoxLayout()
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 14px; font-weight: 700;")
        self.title.setWordWrap(True)
        open_button = QToolButton()
        open_button.setText("⤢")
        open_button.setToolTip(tr("Open in Board Viewer"))
        open_button.clicked.connect(lambda: on_open(self.gid))
        close = QToolButton()
        close.setText("✕")
        close.setToolTip(tr("Stop monitoring this board"))
        close.clicked.connect(lambda: on_remove(self.gid))
        top.addWidget(self.title, 1)
        top.addWidget(open_button)
        top.addWidget(close)
        layout.addLayout(top)

        body = QHBoxLayout()
        self.svg = QSvgWidget()
        self.svg.setFixedSize(190, 190)
        body.addWidget(self.svg)

        info = QGridLayout()
        info.setVerticalSpacing(4)
        self.labels = {}
        rows = [("eval", tr("Evaluation")), ("clocks", tr("Clocks")), ("last", tr("Last move")),
                ("material", tr("Material")), ("repetition", tr("Repetition")), ("status", tr("Status"))]
        for r, (key, caption) in enumerate(rows):
            cap = QLabel(caption + ":")
            cap.setStyleSheet("color: #666666;")
            value = QLabel("—")
            value.setStyleSheet("font-weight: 600;")
            value.setWordWrap(True)
            info.addWidget(cap, r, 0)
            info.addWidget(value, r, 1)
            self.labels[key] = value
        cap = QLabel(tr("No capture / pawn move") + ":")
        cap.setStyleSheet("color: #666666;")
        info.addWidget(cap, len(rows), 0, 1, 2)
        self.fifty_bar = QProgressBar()
        self.fifty_bar.setTextVisible(True)
        self.fifty_bar.setFixedHeight(18)
        info.addWidget(self.fifty_bar, len(rows) + 1, 0, 1, 2)
        info.setRowStretch(len(rows) + 2, 1)
        body.addLayout(info, 1)
        layout.addLayout(body)

    def update_game(self, game, state: BoardState, source: str, board_number: str, fifty_limit: int) -> None:
        white = game.headers.get("White", "?")
        black = game.headers.get("Black", "?")
        prefix = f"[{source}] " if source else ""
        self.title.setText(f"{prefix}#{board_number}  {white} – {black}")
        self.fen = state.fen

        check_square = None
        if state.board.is_check():
            check_square = state.board.king(state.board.turn)
        svg = chess.svg.board(state.board, size=190, lastmove=state.last_move_uci, check=check_square,
                              coordinates=False)
        self.svg.load(QByteArray(svg.encode("utf-8")))

        white_turn = state.to_move == "white"
        self.labels["clocks"].setText(
            (f"<b>▶ {tr('White')} {_fmt_clock(state.white_clock)}</b>" if white_turn
             else f"{tr('White')} {_fmt_clock(state.white_clock)}") + "<br>" +
            (f"<b>▶ {tr('Black')} {_fmt_clock(state.black_clock)}</b>" if not white_turn
             else f"{tr('Black')} {_fmt_clock(state.black_clock)}"))
        self.labels["last"].setText(state.last_move or "—")
        self.labels["material"].setText(f"{state.material:+d}" if state.material else "=")
        rep = self.labels["repetition"]
        rep.setText(f"{state.repetition}×")
        rep.setStyleSheet("font-weight: 700; color: %s;" % (
            "#b42318" if state.repetition >= 3 else "#c27c0e" if state.repetition == 2 else "#333333"))
        status = self.labels["status"]
        status.setText(state.status or tr("in progress"))
        status.setStyleSheet("font-weight: 700; color: %s;" % ("#1e7b3c" if not state.status else "#b42318"))

        moves = state.halfmove_clock // 2
        self.fifty_bar.setMaximum(75)
        self.fifty_bar.setValue(min(moves, 75))
        self.fifty_bar.setFormat(f"{moves} / {fifty_limit}")
        color = "#4caf50" if moves < fifty_limit - 5 else "#f0a020" if moves < fifty_limit else "#d92d20"
        self.fifty_bar.setStyleSheet(
            "QProgressBar { border: 1px solid #c8c8c8; border-radius: 4px; text-align: center; background: #f4f4f4; }"
            f"QProgressBar::chunk {{ background: {color}; border-radius: 3px; }}")

    def set_eval(self, text: str, pawns: float) -> None:
        label = self.labels["eval"]
        label.setText(text)
        color = "#1e7b3c" if pawns > 1.0 else "#b42318" if pawns < -1.0 else "#333333"
        label.setStyleSheet(f"font-weight: 700; color: {color};")


class LiveMonitorWindow(QWidget):
    """Window with cards of the monitored boards."""

    def __init__(self, controller) -> None:
        super().__init__(None, Qt.WindowType.Window)
        self.controller = controller
        self.setWindowTitle(tr("Akiba - Live board monitor"))
        self.resize(1120, 760)
        self.setStyleSheet("QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #8a8a8a;"
                           " border-radius: 3px; background: white; }"
                           "QCheckBox::indicator:checked { background: #4caf50; border-color: #388e3c; }")
        self.cards: Dict[str, BoardCard] = {}
        self.eval_worker: Optional[EvalWorker] = None

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.picker = QComboBox()
        self.picker.setEditable(True)
        self.picker.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.picker.lineEdit().setPlaceholderText(tr("Search board or player..."))
        self.picker.setMinimumWidth(420)
        completer = self.picker.completer()
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        add = QPushButton(tr("Add board"))
        add.clicked.connect(self._add_selected)
        self.picker.lineEdit().returnPressed.connect(self._add_selected)
        self.on_top = QCheckBox(tr("Always on top"))
        self.on_top.toggled.connect(self._set_on_top)
        top.addWidget(self.picker, 1)
        top.addWidget(add)
        top.addSpacing(12)
        top.addWidget(self.on_top)
        layout.addLayout(top)

        self.empty = QLabel(tr("Pick a board above to follow it live: position, evaluation, clocks, "
                               "repetitions and the 50-move counter."))
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet("color: #666666; padding: 20px;")
        layout.addWidget(self.empty)

        self.grid_widget = QWidget()
        self.grid = QGridLayout(self.grid_widget)
        self.grid.setSpacing(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.grid_widget)
        layout.addWidget(scroll, 1)

    # --------------------------------------------------------------- engine
    def _ensure_engine(self) -> None:
        if self.eval_worker is None:
            from src.helpers import get_engine_path
            self.eval_worker = EvalWorker(get_engine_path())
            self.eval_worker.evaluated.connect(self._on_evaluated)
            self.eval_worker.start()

    def _on_evaluated(self, gid: str, fen: str, text: str, pawns: float) -> None:
        card = self.cards.get(gid)
        if card is not None and card.fen == fen:
            card.set_eval(text, pawns)

    def closeEvent(self, event) -> None:
        if self.eval_worker is not None:
            self.eval_worker.stop()
            self.eval_worker = None
        super().closeEvent(event)

    # ---------------------------------------------------------------- cards
    def _set_on_top(self, on: bool) -> None:
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        self.show()

    def _game_label(self, game) -> str:
        c = self.controller
        gid = c.make_game_id(game)
        source = c.source_name_of(gid)
        from src.helpers import get_board_number
        prefix = f"[{source}] " if source else ""
        return f"{prefix}#{get_board_number(game)}  {game.headers.get('White', '?')} – {game.headers.get('Black', '?')}"

    def _fill_picker(self) -> None:
        current = self.picker.currentText()
        self.picker.blockSignals(True)
        self.picker.clear()
        for game in self.controller.games:
            self.picker.addItem(self._game_label(game), self.controller.make_game_id(game))
        self.picker.setEditText(current)
        self.picker.blockSignals(False)

    def _add_selected(self) -> None:
        text = self.picker.currentText().strip()
        index = self.picker.findText(text)
        if index < 0:
            # free text: first board whose label contains it
            matches = [i for i in range(self.picker.count()) if text.lower() in self.picker.itemText(i).lower()]
            if not matches:
                return
            index = matches[0]
        self.add_game(self.picker.itemData(index))
        self.picker.setEditText("")

    def add_game(self, gid: str) -> None:
        if not gid or gid in self.cards:
            return
        card = BoardCard(gid, self.remove_game, self.controller.open_game_in_viewer)
        self.cards[gid] = card
        self._relayout()
        self.refresh()

    def remove_game(self, gid: str) -> None:
        card = self.cards.pop(gid, None)
        if card is not None:
            card.setParent(None)
            card.deleteLater()
        self._relayout()

    def _relayout(self) -> None:
        for i, card in enumerate(self.cards.values()):
            self.grid.addWidget(card, i // 2, i % 2)
        self.empty.setVisible(not self.cards)

    def refresh(self) -> None:
        """Update all cards from the controller's current games."""
        self._fill_picker()
        if not self.cards:
            return
        self._ensure_engine()
        from src.helpers import get_board_number
        c = self.controller
        by_id = {c.make_game_id(g): g for g in c.games}
        fifty = c.claims_manager.fifty_moves_checker.threshold_moves
        for gid, card in self.cards.items():
            game = by_id.get(gid)
            if game is None:
                continue
            state = analyse_game(game)
            card.update_game(game, state, c.source_name_of(gid), get_board_number(game), fifty)
            if game.headers.get("Result", "*") == "*" or state.status:
                self.eval_worker.submit(gid, state.fen)
