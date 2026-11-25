from __future__ import annotations
from src.helpers import stable_game_id

import logging
import os.path
import time
from queue import Queue
from threading import Thread, Lock
from typing import List, TYPE_CHECKING, Dict, Optional, Any, Set

from PyQt6.QtCore import QRunnable, QThread, pyqtSignal
from chess.pgn import read_game
import chess

from src.helpers import get_appdata_path, Status, get_board_number, write_file_atomic, is_phantom_game
from src.claims.manager import ClaimsManager
from src.models.events import get_players, ClaimType, ClaimEntry
from src.models.download import check_download, download_pgn, fetch_pgn
from src.models.livechesscloud import (
    extract_tournament_id, fetch_tournament,
    TournamentData, LiveChessCloudSource, get_rounds_with_games,
    fetch_index, fetch_game, generate_pgn_for_round
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from src.controllers import SourceDialogController
    from src.views.dialog_view import SourceHBox
    from threading import Event, Lock
    from PyQt6.QtWidgets import QAction


# ---------------------------------------------------------
# GAME ANALYSIS QUEUE ITEM
# ---------------------------------------------------------

class GameAnalysisItem:
    """Represents a game waiting for analysis in the queue."""
    
    __slots__ = ["game", "declared_result", "final_fen", "game_id", "queue_key"]

    def __init__(self, game: Any, declared_result: str, final_fen: str):
        self.game = game
        self.declared_result = declared_result
        self.final_fen = final_fen
        self.game_id = stable_game_id(game)
        # A corrected result (or final position) is a new item to analyse
        self.queue_key = analysis_key(self.game_id, declared_result, final_fen)


def analysis_key(game_id: str, result: str, final_fen: str) -> str:
    return f"{game_id}|{result}|{final_fen}"


# ---------------------------------------------------------
# GAME QUEUE - Tracks games queued for analysis
# ---------------------------------------------------------

class GameQueue:
    """
    Thread-safe queue that tracks which games have been queued for analysis.
    Prevents duplicate queuing of the same game.
    """
    
    def __init__(self):
        self._queue = Queue()
        self._queued_games: Set[str] = set()  # Track ALL games (queued + analyzed)
        self._lock = Lock()
        self.stop_flag = False
    
    def put(self, item) -> bool:
        """
        Add a game to the queue if not already queued or analyzed.
        
        Returns:
            bool: True if added, False if already queued/analyzed
        """
        if item is None:
            # Poison pill that stops the engine worker - never deduplicated
            self._queue.put(None)
            return True
        with self._lock:
            if item.queue_key in self._queued_games:
                return False
            self._queued_games.add(item.queue_key)
        self._queue.put(item)
        return True

    def requeue(self, item) -> None:
        """Queue a game again even if it was analysed before (periodic re-check)."""
        with self._lock:
            self._queued_games.add(item.queue_key)
        self._queue.put(item)
    
    def get(self, timeout: float = 1.0):
        """Get an item from the queue with timeout."""
        return self._queue.get(timeout=timeout)
    
    def task_done(self):
        """Mark a task as done."""
        self._queue.task_done()
    
    def clear(self):
        """Clear only the queue (not analyzed games)."""
        # Empty the queue but keep tracked game IDs
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except Exception:
                pass
    
    def add_already_analyzed(self, game_id: str):
        """Mark a game as already analyzed (skip future queuing)."""
        with self._lock:
            self._queued_games.add(game_id)
    
    def is_queued(self, game_id: str) -> bool:
        """Check if a game is already in queue or analyzed."""
        with self._lock:
            return game_id in self._queued_games
    
    @property
    def size(self) -> int:
        """Get current queue size."""
        return self._queue.qsize()


# ---------------------------------------------------------
# ENGINE WORKER - Background Stockfish Analysis for Mismatched Result
# ---------------------------------------------------------

class EngineWorker(QThread):
    """
    Worker thread for sequential Stockfish engine analysis to prevent UI freeze.
    
    Processes games from a queue one at a time, analyzing each for 1-2 seconds
    to ensure accurate evaluation. Emits ClaimEntry if mismatch detected.
    """
    
    # Signal emits (game, entry) where entry is ClaimEntry or None
    analysis_complete = pyqtSignal(object, object)
    error_occurred = pyqtSignal(str)
    analysis_logged = pyqtSignal(int, str, str, float, str)  # game_index, board_number, declared_result, engine_score, last_moves
    queue_empty = pyqtSignal()  # Emitted when queue is empty
    
    def __init__(self, engine_path: str, game_queue: Any):
        super().__init__()
        self.engine_path = engine_path
        self.game_queue = game_queue  # Queue of GameAnalysisItem
        self.daemon = True
    
    def run(self):
        """Run analysis in background thread - processes queue sequentially."""
        try:
            from src.services.stockfish_wrapper import StockfishWrapper
            
            
            # Keep running as long as there are items in queue or worker is active
            while True:
                # Wait for an item from the queue (with timeout to check stop condition)
                try:
                    analysis_item = self.game_queue.get(timeout=2.0)
                except Exception:
                    # Queue empty or timeout - check if we should continue
                    if hasattr(self.game_queue, 'stop_flag') and self.game_queue.stop_flag:
                        break
                    continue
                
                if analysis_item is None:  # Poison pill to stop worker
                    break
                
                try:
                    with StockfishWrapper(self.engine_path) as engine:
                        # Analyze for 1.5 seconds to ensure accurate evaluation
                        # Returns (score, pv_str) tuple where pv_str is already formatted
                        result = engine.get_position_evaluation(analysis_item.final_fen, time_limit=1.5)
                        score = result[0]
                        pv_str = result[1] if len(result) > 1 else ""
                        
                        # Mark game as analyzed to prevent re-queuing on restart
                        game_id = stable_game_id(analysis_item.game)
                        self.game_queue.add_already_analyzed(game_id)
                        
                        # Log the analysis result (include board number for better identification)
                        game_index = self._get_game_index(analysis_item.game)
                        board_number = self._get_board_number(analysis_item.game)
                        self.analysis_logged.emit(game_index, board_number, analysis_item.declared_result, score, pv_str)
                        
                        # Check if result matches evaluation
                        # Threshold: >3.0 or <-3.0 for clear mismatch
                        # Gray zone (1.0 to 3.0 or -1.0 to -3.0) is NOT monitored
                        parsed_result = self._parse_result(analysis_item.declared_result)
                        
                        if (parsed_result == 0
                                and self._draw_by_repetition(analysis_item.game)):
                            # Perpetual check / repeated moves: a material advantage
                            # does not make such a draw suspicious
                            parsed_result = None

                        if parsed_result is not None and self._is_mismatch(score, parsed_result):
                            # Get last move for display (same as other checkers)
                            moves_list = list(analysis_item.game.mainline_moves())
                            
                            if moves_list:
                                ply_count = len(moves_list)
                                full_move_count = ply_count // 2
                                
                                # Replay board to get position before last move
                                temp_board = chess.Board()
                                for move in moves_list[:-1]:
                                    try:
                                        temp_board.push(move)
                                    except (ValueError, chess.InvalidMoveError):
                                        break
                                
                                if moves_list[:-1] or len(moves_list) == 1:
                                    try:
                                        last_san = temp_board.san(moves_list[-1])
                                        
                                        # Format move number with ellipsis for black's move
                                        if ply_count % 2 == 0:
                                            move_num_str = f"{full_move_count}...{last_san}"
                                        else:
                                            move_num_str = f"{full_move_count}.{last_san}"
                                    except Exception:
                                        move_num_str = ""
                                else:
                                    move_num_str = ""
                            else:
                                move_num_str = ""
                            
                            # Determine expected result based on engine score (threshold 1.0)
                            expected_result = self._get_expected_result(score)
                            
                            # Format move display with original declared result (no HTML tags)
                            declared_result = analysis_item.declared_result
                            if move_num_str:
                                move_display = f"{move_num_str} ({declared_result})"
                            else:
                                move_display = f"({declared_result})"
                            
                            board_number = get_board_number(analysis_item.game)
                            
                            entry = ClaimEntry(
                                type=ClaimType.MISMATCHED_RESULT,
                                board_number=board_number,
                                players=get_players(analysis_item.game),
                                move=move_display,
                                game_index=self._get_game_index(analysis_item.game),
                                move_counter=len(moves_list) if moves_list else 0,
                                start_move_counter=0,
                                comment=f"Result mismatch: Engine evaluation {score:+.2f}",
                                game_id=analysis_item.game_id,
                            )
                            self.analysis_complete.emit(analysis_item.game, entry)
                        else:
                            # No mismatch
                            self.analysis_complete.emit(analysis_item.game, None)
                            
                except Exception as e:
                    self.error_occurred.emit(str(e))
                
                # Mark task as done if using Queue
                try:
                    self.game_queue.task_done()
                except Exception:
                    pass
                    
        except Exception as e:
            self.error_occurred.emit(str(e))
    
    def _get_game_index(self, game: Any) -> int:
        """Get game index from headers or default to 0."""
        try:
            return int(game.headers.get("Round", "0"))
        except (ValueError, TypeError):
            return 0
    
    def _get_board_number(self, game: Any) -> str:
        """Get board number from game headers (same as ClaimsManager.get_board_number)."""
        return get_board_number(game)
    
    def _parse_result(self, result: str) -> Optional[int]:
        """Parse PGN result string to numeric value."""
        result_map = {
            "1-0": 1,      # White wins
            "0-1": -1,     # Black wins
            "1/2-1/2": 0,  # Draw
            "*": None      # Unknown
        }
        return result_map.get(result)
    
    @staticmethod
    def _draw_by_repetition(game, recent_plies: int = 12) -> bool:
        """
        True if a position near the end of the game occurred twice: a perpetual
        check or repeated moves, after which a draw is normal whatever the
        material balance (e.g. ...Qh4+ Kg1 Qe1+ Kh2 Qh4+).
        """
        board = game.board()
        keys = [board._transposition_key()]
        for move in game.mainline_moves():
            try:
                board.push(move)
            except (ValueError, AssertionError):
                return False
            keys.append(board._transposition_key())
        recent = keys[-(recent_plies + 1):]
        return len(recent) != len(set(recent))

    def _is_mismatch(self, engine_score: float, declared_result: int) -> bool:
        """Check if engine evaluation mismatches declared result.
        
        Thresholds:
        - Score > +3.0 or < -3.0 → clear mismatch (flagged)
        - Score between +1.0 and +3.0 OR -1.0 and -3.0 → gray zone, NOT monitored
        - Score between -1.0 and +1.0 → draw expected
        """
        strong_threshold = 3.0
        weak_threshold = 1.0
        
        # White won (declared_result == 1)
        if declared_result == 1:
            # Flag only if engine strongly favors black (< -3.0)
            # Gray zone (-3.0 to -1.0) is NOT monitored
            if engine_score < -strong_threshold:
                return True
        
        # Black won (declared_result == -1)
        elif declared_result == -1:
            # Flag only if engine strongly favors white (> +3.0)
            # Gray zone (+1.0 to +3.0) is NOT monitored
            if engine_score > strong_threshold:
                return True
        
        # Draw (declared_result == 0)
        elif declared_result == 0:
            # Flag only if engine strongly favors one side (> +3.0 or < -3.0)
            # Gray zone (-3.0 to -1.0 and +1.0 to +3.0) is NOT monitored
            if engine_score > strong_threshold or engine_score < -strong_threshold:
                return True
        
        return False
    
    def _get_expected_result(self, engine_score: float) -> str:
        """Get expected result based on engine score.
        
        Returns:
            "1-0" if score > +1.0 (white advantage)
            "0-1" if score < -1.0 (black advantage)
            "1/2-1/2" if score between -1.0 and +1.0 (draw)
        """
        if engine_score > 1.0:
            return "1-0"
        elif engine_score < -1.0:
            return "0-1"
        else:
            return "1/2-1/2"
    
    def _make_game_id(self, game: Any) -> str:
        """Generate stable game identifier from PGN headers."""
        return stable_game_id(game)


# ---------------------------------------------------------
# CHECK DOWNLOAD
# ---------------------------------------------------------

class CheckDownload(QRunnable):
    """Checks if a web source is valid."""

    __slots__ = ["controller", "source", "download_id"]

    def __init__(self, controller: SourceDialogController, source: SourceHBox, download_id: int):
        super().__init__()
        self.controller = controller
        self.source = source
        self.download_id = download_id

    def run(self):
        url = self.source.get_value()
        if check_download(url):
            self.source.set_status(Status.OK)
            if url not in self.controller.downloads:
                self.controller.add_valid_url(url, self.download_id)
        else:
            self.source.set_status(Status.ERROR)


# ---------------------------------------------------------
# DOWNLOAD GAMES
# ---------------------------------------------------------

class DownloadGames(QThread):
    """Downloads PGN files from the web."""

    status_signal = pyqtSignal(Status)
    # (url, reason) - a source has been failing continuously for ERROR_GRACE seconds
    source_failed_signal = pyqtSignal(str, str)
    INTERVAL = 2
    # Live servers rewrite their PGN files, so single failed downloads are
    # normal; only a source that keeps failing this long is reported.
    ERROR_GRACE = 60
    __slots__ = ["downloads", "stop_event", "app_path", "_failing_since", "_reported"]

    def __init__(self, downloads: Dict[str, str], stop_event: Event = None):
        super().__init__()
        self.downloads = downloads
        self.stop_event = stop_event
        self.app_path = get_appdata_path()
        self._failing_since: Dict[str, float] = {}
        self._reported: Set[str] = set()

    def run(self) -> None:
        if not self.stop_event:
            return self.download_pgns()

        while not self.stop_event.is_set():
            self.download_pgns()
            self.stop_event.wait(self.INTERVAL)

    def download_pgns(self):
        for url in self.downloads:
            data, reason = fetch_pgn(url)
            if not data:
                self.status_signal.emit(Status.ERROR)
                self._on_failure(url, reason)
                # Keep the last good copy instead of overwriting it with nothing
                continue

            self.status_signal.emit(Status.OK)
            self._on_success(url)

            filename = self.downloads[url]
            try:
                write_file_atomic(filename, data)
            except (OSError, TypeError) as exc:
                logger.warning("cannot write %s: %s", filename, exc)
                self.status_signal.emit(Status.ERROR)
                continue

    def _on_failure(self, url: str, reason: str) -> None:
        now = time.monotonic()
        if url not in self._failing_since:
            self._failing_since[url] = now
            logger.warning("PGN download failed: %s (%s)", url, reason)
        elif (url not in self._reported
              and now - self._failing_since[url] >= self.ERROR_GRACE):
            self._reported.add(url)
            logger.error("PGN source failing for %d s: %s (%s)", self.ERROR_GRACE, url, reason)
            self.source_failed_signal.emit(url, reason)

    def _on_success(self, url: str) -> None:
        since = self._failing_since.pop(url, None)
        if since is not None:
            logger.info("PGN download recovered after %.0f s: %s", time.monotonic() - since, url)
        self._reported.discard(url)


# ---------------------------------------------------------
# DOWNLOAD LIVECHESS GAMES
# ---------------------------------------------------------

class DownloadLiveChessGames(QThread):
    """Downloads and updates PGN from LiveChessCloud live tournaments."""
    
    status_signal = pyqtSignal(Status)
    INTERVAL = 4  # Same as DownloadGames for consistency
    
    __slots__ = ["sources", "stop_event", "app_path"]
    
    def __init__(self, sources: List[LiveChessCloudSource], stop_event: Event = None):
        super().__init__()
        self.sources = sources
        self.stop_event = stop_event
        self.app_path = get_appdata_path()
    
    def run(self) -> None:
        if not self.stop_event:
            return self.download_pgns()
        
        while not self.stop_event.is_set():
            self.download_pgns()
            self.stop_event.wait(self.INTERVAL)
    
    def download_pgns(self):
        """Fetch live games and update PGN files."""
        from src.models.livechesscloud import (
            fetch_tournament, fetch_index, fetch_game,
            generate_pgn_for_round
        )
        
        for source in self.sources:
            status = Status.OK
            
            if not source.is_valid:
                continue
            
            tournament = fetch_tournament(source.tournament_id)
            if not tournament:
                self.status_signal.emit(Status.ERROR)
                status = Status.ERROR
                continue
            
            # Process only the specified round (default: 1)
            round_num = source.round_num if source.round_num is not None else 1
            round_data = fetch_index(source.tournament_id, round_num)
            
            if not round_data:
                self.status_signal.emit(Status.ERROR)
                status = Status.ERROR
                continue
            
            # Fetch all games in this round
            games = {}
            for game_num in range(1, len(round_data.pairings) + 1):
                game = fetch_game(source.tournament_id, round_num, game_num)
                if game and game.moves:
                    games[game_num] = game
            
            # Generate PGN for this round
            if not games:
                self.status_signal.emit(Status.ERROR)
                status = Status.ERROR
                continue
            
            pgn_content = generate_pgn_for_round(
                tournament, round_data, games, round_num
            )
            # Write PGN to file
            filename = source.get_filename()
            filepath = os.path.join(self.app_path, filename)
            
            try:
                write_file_atomic(filepath, pgn_content.encode("utf-8"))
            except Exception:
                self.status_signal.emit(Status.ERROR)
                status = Status.ERROR
    
    def get_filepaths(self) -> List[str]:
        """Get list of PGN file paths for this source type."""
        return [os.path.join(self.app_path, source.get_filename()) 
                for source in self.sources if source.is_valid]


# ---------------------------------------------------------
# SCAN PGN
# ---------------------------------------------------------

class Scan(QThread):
    """
    Continuously scans the combined PGN file for new games.
    Emits ClaimEntry objects to update the GUI.
    Uses GameQueue to ensure each finished game is analyzed only once.
    """

    __slots__ = ["filename", "claims_manager", "lock", "live_pgn_option", "stop_event", "game_queue"]

    add_entry_signal = pyqtSignal(object)   # emits ClaimEntry
    status_signal = pyqtSignal(Status)
    new_move_signal = pyqtSignal()
    game_queued_signal = pyqtSignal(object, str, str)  # game, declared_result, final_fen
    
    INTERVAL = 2

    def __init__(self, claims_manager: ClaimsManager, filename: str, lock: Lock,
                 live_pgn_option: QAction, stop_event: Event, game_queue: GameQueue = None):
        super().__init__()
        self.filename = filename
        self.claims_manager = claims_manager
        self.lock = lock
        self.live_pgn_option = live_pgn_option   # can be None
        self.stop_event = stop_event
        self.game_queue = game_queue or GameQueue()

    def run(self):
        last_size = 0

        while not self.stop_event.is_set():
            try:
                size_of_pgn = os.path.getsize(self.filename)
            except FileNotFoundError:
                size_of_pgn = 0

            if size_of_pgn != 0:
                self.status_signal.emit(Status.ACTIVE)
                self.new_move_signal.emit()
                self.check_pgn()

            self.status_signal.emit(Status.WAIT)
            last_size = size_of_pgn

            self.stop_event.wait(self.INTERVAL)

    def check_pgn(self):
        """
        Reads the PGN file and queues new finished games for analysis.
        Each game is queued only once using GameQueue tracking.
        """

        self.lock.acquire()
        try:
            with open(self.filename, "r", encoding="utf-8") as pgn:
                game_index = 0

                while not self.stop_event.is_set():
                    game = read_game(pgn)
                    if not game:
                        break
                    if is_phantom_game(game):
                        continue  # fragment of a truncated PGN (skipped by the GUI too)

                    # ---------------------------------------------
                    # FIX: Live PGN optional
                    # ---------------------------------------------
                    if (
                        self.live_pgn_option
                        and self.live_pgn_option.isChecked()
                        and game.headers.get("Result") != "*"
                    ):
                        continue

                    if self.stop_event.is_set():
                        break
                    
                    # Use ClaimsManager for checking instead of old Claims class
                    entries, dont_check, _ = self.claims_manager.run_all_claims([game], None, game_index=game_index)

                    for entry in entries:
                        self.add_entry_signal.emit(entry)

                    # Check if game is finished and not yet queued/analyzed for mismatched result
                    result = game.headers.get("Result", "*")
                    if result != "*" and result not in ("*",):
                        game_id = stable_game_id(game)
                        
                        # Final position; result + position decide whether it is new
                        board = chess.Board()
                        for move in game.mainline_moves():
                            try:
                                board.push(move)
                            except (ValueError, chess.InvalidMoveError):
                                break
                        final_fen = board.fen()

                        # Check if already queued or analyzed (using GameQueue tracking)
                        if not self.game_queue.is_queued(analysis_key(game_id, result, final_fen)):
                            # Create analysis item and add to queue
                            analysis_item = GameAnalysisItem(game, result, final_fen)
                            
                            if not self.game_queue.put(analysis_item):
                                pass  # Game already in queue, skip

                    game_index += 1

        finally:
            self.lock.release()

    @staticmethod
    def is_file_updated(last_size: int, current_size: int):
        return current_size != 0 and last_size != current_size


# ---------------------------------------------------------
# STOP WORKERS
# ---------------------------------------------------------

class Stop(QThread):
    """Stops all running threads and resets the model."""

    enable_signal = pyqtSignal()
    disable_signal = pyqtSignal()

    __slots__ = ["stop_event", "make_pgn_worker", "scan_worker", "download_worker", "livechess_worker", "engine_worker", "game_queue"]

    def __init__(self, stop_event: Event, make_pgn_worker: Thread,
                 scan_worker: QThread, download_worker: QThread = None,
                 livechess_worker: QThread = None, engine_worker: QThread = None):
        super().__init__()
        self.stop_event = stop_event
        self.download_worker = download_worker
        self.livechess_worker = livechess_worker
        self.make_pgn_worker = make_pgn_worker
        self.scan_worker = scan_worker
        self.engine_worker = engine_worker
        self.game_queue = None

    def run(self):
        self.disable_signal.emit()
        self.stop_event.set()

        if self.download_worker:
            self.download_worker.wait()
        
        if self.livechess_worker:
            self.livechess_worker.wait()

        # Stop engine worker by sending poison pill and waiting
        if self.engine_worker and self.game_queue:
            try:
                self.game_queue.put(None)  # Poison pill
            except Exception:
                pass
            self.engine_worker.wait(5000)

        self.scan_worker.wait()
        self.make_pgn_worker.join()

        self.enable_signal.emit()


# ---------------------------------------------------------
# MAKE PGN
# ---------------------------------------------------------

class MakePgn(Thread):
    """
    Creates a combined PGN file from all available sources.
    This worker does NOT emit Qt signals (it is a plain Thread).
    """

    INTERVAL = 2
    __slots__ = ["filepaths", "stop_event", "is_running", "lock", "daemon"]

    def __init__(self, filepaths: List[str], stop_event: Event = None, lock: Lock = None):
        super().__init__()
        self.filepaths = filepaths
        self.lock = lock
        self.stop_event = stop_event
        self.daemon = True

        app_path = get_appdata_path()
        self.filename = os.path.join(app_path, "games.pgn")

    def run(self) -> None:
        if not self.stop_event:
            return self.make_pgn()

        while not self.stop_event.is_set():
            self.make_pgn()
            self.stop_event.wait(self.INTERVAL)

    def make_pgn(self):
        """
        Merges all PGN files into a single combined PGN.
        Ensures proper separation between games.
        """

        data = b""
        for filepath in self.filepaths:
            try:
                with open(filepath, "rb") as in_file:
                    content = in_file.read().strip()
                    if not content:
                        continue
                    data += b"\n\n" + content + b"\n\n"
            except FileNotFoundError:
                continue

        self.lock_file()
        try:
            # Swap in atomically: readers without the lock (GUI reload, board
            # viewer) never see a half-written games.pgn
            write_file_atomic(self.filename, data)
        finally:
            self.release_file()

    def lock_file(self):
        if self.lock:
            self.lock.acquire()

    def release_file(self):
        if self.lock:
            self.lock.release()

