import json
import logging
import os
import sys
from datetime import datetime
from threading import Event, Thread, Lock
from typing import List, Dict

from PyQt6.QtCore import Qt, QThreadPool
from PyQt6.QtWidgets import QApplication

from src.helpers import get_appdata_path, Status, get_engine_path, is_phantom_game
from src.claims.manager import ClaimsManager
from src.models.workers import CheckDownload, DownloadGames, MakePgn, Scan, Stop, DownloadLiveChessGames
from src.models.events import ClaimEntry, ClaimType, ClaimsStore
from src.views.dialog_view import AddSourceDialog, SourceHBox
from src.views.main_view import ChessClaimView, sources_warning
from src.views.board_viewer import BoardViewerWindow
from src.models.events import get_players
from src.reminders.manager import ReminderManager
from src.models.livechesscloud import LiveChessCloudSource
from src.push import PushNotifier, PushSettings


logger = logging.getLogger(__name__)


class ChessClaimController(QApplication):

    __slots__ = [
        'view', 'claims_manager', 'sources_dialog',
        'make_pgn_worker', 'stop_worker', 'download_worker', 'scan_worker',
        'stop_event', 'board_viewer', 'reminder_manager',
        'scoresheet_reminder_enabled', 'scoresheet_threshold',
        'scoresheet_reminder_fired',
        'timecontrol_reminder_enabled', 'timecontrol_threshold',
        'timecontrol_reminder_fired',
        'possible_threefold_enabled', 'possible_fiftymove_enabled',
        'threefold_fired', 'fiftymove_fired',
        'games',
        'claims',
        'low_time_reminder_enabled',
        'low_time_threshold_seconds',
        'low_time_reminder_fired',
        'flag_fall_reminder_enabled',
        'flag_fall_reminder_fired',
        'illegal_move_reminder_enabled',
        'filepaths',
        'downloads',
        'notifications_enabled',
        'livechess_sources',
        'livechess_worker',
    ]

    def __init__(self) -> None:
        super().__init__(sys.argv)

        # UI language must be known before any window is built
        from src.i18n import init_language, install_qt_translations
        self.language = init_language()
        install_qt_translations(self)

        # NEW: Central claims store - SINGLE SOURCE OF TRUTH (must be before view!)
        self.claims_store = ClaimsStore()
        
        self.view = ChessClaimView(self)
        self.claims_manager = ClaimsManager()
        self.claims_manager.controller = self
        self.sources_dialog = None
        self.current_viewed_gid = None
        self.reminder_manager = ReminderManager()

        self.make_pgn_worker = None
        self.download_worker = None
        self.scan_worker = None
        self.stop_worker = None
        self.livechess_worker = None

        self.stop_event = Event()
        self.board_viewer = None
        
        # LiveChessCloud sources
        self.livechess_sources: List[LiveChessCloudSource] = []

        # Scoresheet reminder
        self.scoresheet_reminder_enabled = False
        self.scoresheet_threshold = 55
        self.scoresheet_reminder_fired: Dict[int, bool] = {}

        # Timecontrol reminder
        self.timecontrol_reminder_enabled = False
        self.timecontrol_threshold = 40
        self.timecontrol_reminder_fired: Dict[int, bool] = {}

        # Low time reminder
        self.low_time_reminder_enabled = False
        self.low_time_threshold_seconds = 120
        self.low_time_reminder_fired: Dict[int, bool] = {}

        # Flag fall reminder (uses set for tracking shown reminders)
        self.flag_fall_reminder_enabled = False
        self.flag_fall_reminder_fired: set = set()

        # Illegal move reminder (uses set for tracking shown reminders to prevent duplicates)
        self.illegal_move_reminder_enabled = False
        self.illegal_move_reminder_fired: set = set()

        # Possible incorrect result reminder (uses set for tracking shown reminders)
        self.possible_incorrect_result_enabled = False
        self.mismatched_result_shown: set = set()

        # Sofia rule reminder
        self.sofia_rule_enabled = False
        self.sofia_rule_threshold = 30
        self.sofia_rule_fired: Dict[int, bool] = {}

        # Possible reminders (2-fold and 45-move warnings)
        self.possible_threefold_enabled = False  # Two-fold warning
        self.possible_fiftymove_enabled = False  # 45-move warning

        # Notification system enabled by default
        self.notifications_enabled = True

        # Phone / Apple Watch push notifications (Pushover)
        self.push = PushNotifier()

        # Followed players (notifications about their games)
        from src.follow import FollowSettings, GameEndTracker
        self.follow = FollowSettings()
        self._game_end_tracker = GameEndTracker()

        # ChessManager tournaments used to verify transmission results
        self.chessmanager_urls: List[str] = []
        self.verify_worker = None

        # Game list and mapping
        self.games: List = []
        self.game_id_to_index: Dict[str, int] = {}

    CLAIM_OPTION_KEYS = {
        ClaimType.THREEFOLD: "claim_threefold",
        ClaimType.FIVEFOLD: "claim_fivefold",
        ClaimType.FIFTY_MOVES: "claim_fifty_moves",
        ClaimType.SEVENTYFIVE_MOVES: "claim_seventyfive_moves",
        ClaimType.CHECKMATE_CLAIM: "claim_checkmate",
        ClaimType.STALEMATE_CLAIM: "claim_stalemate",
        ClaimType.DEAD_POSITION_CLAIM: "claim_dead_position",
    }

    def set_fifty_moves_threshold(self, moves: int) -> None:
        """Moves without capture / pawn move before a 50-move claim is shown."""
        self.claims_manager.fifty_moves_checker.threshold_moves = int(moves)
        self.save_options()

    def set_claim_enabled(self, claim_type: ClaimType, enabled: bool) -> None:
        """Switch reporting of a claim type on/off (Claims menu)."""
        self.claims_manager.enabled[claim_type] = enabled
        self.save_options()

    def make_game_id(self, game) -> str:
        """
        Generates a stable identifier for a game based on PGN headers.
        This ID does NOT change even if the PGN file is rebuilt or reordered.
        """
        return "|".join([
            game.headers.get("Event", ""),
            game.headers.get("Site", ""),
            game.headers.get("Date", ""),
            game.headers.get("Round", ""),
            game.headers.get("White", ""),
            game.headers.get("Black", ""),
        ])

    # -------------------------------------------------------------------------
    # START
    # -------------------------------------------------------------------------

    def do_start(self) -> None:
        app_path = get_appdata_path()
        os.makedirs(app_path, exist_ok=True)

        self.view.set_gui()
        self.load_options()  # Load saved reminder settings
        self.apply_options_to_view()  # Apply loaded settings to view checkboxes
        self.view.show()
        self.start_background_verification()

    # -------------------------------------------------------------------------
    # OPTIONS (Settings Persistence)
    # -------------------------------------------------------------------------

    def load_options(self) -> None:
        """Load saved reminder settings from options.json."""
        try:
            options_path = os.path.join(get_appdata_path(), "options.json")
            if not os.path.exists(options_path):
                return

            with open(options_path, "r") as file:
                data = json.load(file)

            # Notification system
            self.notifications_enabled = data.get("enable_notifications", True)

            # Phone / Apple Watch push notifications
            self.push.settings = PushSettings.from_dict(data.get("push"))
            self.chessmanager_urls = [str(u) for u in data.get("chessmanager_urls", []) if u]
            from src.follow import FollowSettings
            self.follow = FollowSettings.from_dict(data.get("follow"))

            # Claim types reported (3-fold, 5-fold, 50-move, 75-move)
            for claim_type, key in self.CLAIM_OPTION_KEYS.items():
                self.claims_manager.enabled[claim_type] = bool(data.get(key, True))
            self.claims_manager.fifty_moves_checker.threshold_moves = min(74, max(50, int(
                data.get("claim_fifty_moves_threshold", 50))))

            # Scoresheet reminder
            self.scoresheet_reminder_enabled = data.get("scoresheet_enabled", False)
            self.scoresheet_threshold = data.get("scoresheet_move", 55)

            # Timecontrol reminder
            self.timecontrol_reminder_enabled = data.get("timecontrol_enabled", False)
            self.timecontrol_threshold = data.get("timecontrol_move", 40)

            # Possible reminders (2-fold and 50-move warnings)
            self.possible_threefold_enabled = data.get("possible_threefold", False)
            self.possible_fiftymove_enabled = data.get("possible_50_move", False)

            # Possible incorrect result reminder
            self.possible_incorrect_result_enabled = data.get("possible_incorrect_result", False)

            # Low time reminder
            self.low_time_reminder_enabled = data.get("low_time_enabled", False)
            self.low_time_threshold_seconds = data.get("low_time_seconds", 120)

            # Flag fall reminder
            self.flag_fall_reminder_enabled = data.get("flag_fall_reminder", False)

            # Illegal move reminder
            self.illegal_move_reminder_enabled = data.get("illegal_move_reminder", False)

            # Sofia rule reminder
            self.sofia_rule_enabled = data.get("sofia_enabled", False)
            self.sofia_rule_threshold = data.get("sofia_move", 30)

            # Possible incorrect result reminder
            self.possible_incorrect_result_enabled = data.get("possible_incorrect_result", False)

        except Exception:
            # If loading fails, keep default values
            pass

    def save_options(self) -> None:
        """Save current reminder settings to options.json."""
        data = {
            "enable_notifications": self.notifications_enabled,
            "scoresheet_enabled": self.scoresheet_reminder_enabled,
            "scoresheet_move": self.scoresheet_threshold,
            "timecontrol_enabled": self.timecontrol_reminder_enabled,
            "timecontrol_move": self.timecontrol_threshold,
            "possible_threefold": self.possible_threefold_enabled,
            "possible_50_move": self.possible_fiftymove_enabled,
            "possible_incorrect_result": self.possible_incorrect_result_enabled,
            "low_time_enabled": self.low_time_reminder_enabled,
            "low_time_seconds": self.low_time_threshold_seconds,
            "flag_fall_reminder": self.flag_fall_reminder_enabled,
            "illegal_move_reminder": self.illegal_move_reminder_enabled,
            "sofia_enabled": self.sofia_rule_enabled,
            "sofia_move": self.sofia_rule_threshold,
            "push": self.push.settings.to_dict(),
            "language": self.language,
            "chessmanager_urls": self.chessmanager_urls,
            "follow": self.follow.to_dict(),
        }
        for claim_type, key in self.CLAIM_OPTION_KEYS.items():
            data[key] = self.claims_manager.is_enabled(claim_type)
        data["claim_fifty_moves_threshold"] = self.claims_manager.fifty_moves_checker.threshold_moves

        with open(os.path.join(get_appdata_path(), 'options.json'), 'w') as file:
            json.dump(data, file, indent=4)

    def apply_options_to_view(self) -> None:
        """Apply loaded settings to view checkboxes and spinboxes."""
        # Notification system
        self.view.notifications_enabled_action.setChecked(self.notifications_enabled)

        # Claim types
        for claim_type, checkbox in self.view.claim_type_actions.items():
            checkbox.setChecked(self.claims_manager.is_enabled(claim_type))
        self.view.fifty_moves_spin.setValue(self.claims_manager.fifty_moves_checker.threshold_moves)
        
        # Scoresheet reminder
        self.view.scoresheet_action.setChecked(self.scoresheet_reminder_enabled)
        self.view.scoresheet_spin.setValue(self.scoresheet_threshold)
        self.view.scoresheet_spin.setEnabled(self.scoresheet_reminder_enabled)
        
        # Timecontrol reminder
        self.view.timecontrol_action.setChecked(self.timecontrol_reminder_enabled)
        self.view.timecontrol_spin.setValue(self.timecontrol_threshold)
        self.view.timecontrol_spin.setEnabled(self.timecontrol_reminder_enabled)
        
        # Possible reminders
        self.view.possible_threefold_action.setChecked(self.possible_threefold_enabled)
        self.view.possible_fiftymove_action.setChecked(self.possible_fiftymove_enabled)
        self.view.possible_incorrect_result_action.setChecked(self.possible_incorrect_result_enabled)

        # Low time reminder
        self.view.low_time_action.setChecked(self.low_time_reminder_enabled)
        self.view.low_time_spin.setValue(self.low_time_threshold_seconds)
        self.view.low_time_spin.setEnabled(self.low_time_reminder_enabled)
        
        # Flag fall reminder
        self.view.flag_fall_action.setChecked(self.flag_fall_reminder_enabled)

        # Illegal move reminder
        self.view.illegal_move_action.setChecked(self.illegal_move_reminder_enabled)

        # Sofia rule reminder
        self.view.sofia_action.setChecked(self.sofia_rule_enabled)
        self.view.sofia_spin.setValue(self.sofia_rule_threshold)
        self.view.sofia_spin.setEnabled(self.sofia_rule_enabled)


    def extract_clock_from_node(self, node):
        import re

        if not node or not node.comment:
            return None

        match = re.search(r"\[%clk\s+(\d{1,2}:\d{2}:\d{2})\]", node.comment)
        if match:
            return match.group(1)
        return None

    # -------------------------------------------------------------------------
    # ABOUT
    # -------------------------------------------------------------------------
    # HELP MENU
    # -------------------------------------------------------------------------

    def on_user_manual_clicked(self) -> None:
        """Open the user documentation file."""
        self.view.open_user_manual()

    # -------------------------------------------------------------------------
    # RESULT VERIFICATION (ChessManager)
    # -------------------------------------------------------------------------

    def on_chessmanager_sources_clicked(self) -> bool:
        """Edit the ChessManager tournament links. Returns True when saved."""
        from src.views.dialogs.verify_dialog import ChessManagerSourcesDialog
        dialog = ChessManagerSourcesDialog(self.chessmanager_urls, self.view)
        if dialog.exec():
            self.chessmanager_urls = dialog.urls()
            self.save_options()
            return True
        return False

    # Background check of results against ChessManager (minutes)
    VERIFY_INTERVAL_MINUTES = 5

    def start_background_verification(self) -> None:
        """Re-check results every few minutes; the button turns red on discrepancies."""
        from PyQt6.QtCore import QTimer
        self.verify_timer = QTimer(self)
        self.verify_timer.setInterval(self.VERIFY_INTERVAL_MINUTES * 60 * 1000)
        self.verify_timer.timeout.connect(lambda: self._start_verification(show_dialog=False))
        self.verify_timer.start()
        # first background check shortly after start (games are loaded once the scan runs)
        QTimer.singleShot(60 * 1000, lambda: self._start_verification(show_dialog=False))

    def on_verify_results_clicked(self) -> None:
        """Manual check: compare PGN results with ChessManager, then show the report."""
        from PyQt6.QtWidgets import QMessageBox
        from src.i18n import tr

        if not self.chessmanager_urls and not self.on_chessmanager_sources_clicked():
            return
        if not self.chessmanager_urls:
            return
        if not self.games:
            self.on_pgn_updated()
        if not self.games:
            QMessageBox.information(self.view, "Akiba", tr("No games loaded - start the scan first."))
            return
        self._start_verification(show_dialog=True)

    def _start_verification(self, show_dialog: bool) -> None:
        from src.i18n import tr
        from src.verification.worker import VerifyResultsWorker

        if self.verify_worker is not None and self.verify_worker.isRunning():
            if show_dialog:
                self._verification_wants_dialog = True  # show when the running check ends
            return
        if not self.chessmanager_urls or not self.games:
            return

        games = list(self.games)
        self._verification_wants_dialog = show_dialog
        if show_dialog:
            button = self.view.verify_results_button
            button.setEnabled(False)
            button.setText(tr("Verifying..."))
        self.verify_worker = VerifyResultsWorker(games, self.chessmanager_urls)
        self.verify_worker.finished_signal.connect(
            lambda report: self._on_verification_finished(report, games))
        self.verify_worker.start()

    def _on_verification_finished(self, report, games) -> None:
        self.view.verify_results_button.setEnabled(True)
        self.view.set_verification_state(report)
        if getattr(self, "_verification_wants_dialog", False):
            self._verification_wants_dialog = False
            self._show_verification(report, games)

    def _show_verification(self, report, games) -> None:
        from src.views.dialogs.verify_dialog import VerificationResultsDialog

        def open_game(index: int) -> None:
            game = games[index]
            plies = len(list(game.mainline_moves()))
            self.open_viewer_for_claim(index, plies, self.make_game_id(game))

        self.verification_dialog = VerificationResultsDialog(report, games, open_game, self.view)
        self.verification_dialog.show()
        self.verification_dialog.raise_()

    # -------------------------------------------------------------------------
    # FOLLOWED PLAYERS
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # LIVE BOARD MONITOR
    # -------------------------------------------------------------------------

    def on_live_monitor_clicked(self) -> None:
        from src.views.live_monitor import LiveMonitorWindow
        if not self.games:
            self.on_pgn_updated()
        if getattr(self, "live_monitor", None) is None:
            self.live_monitor = LiveMonitorWindow(self)
        self.live_monitor.refresh()
        self.live_monitor.show()
        self.live_monitor.raise_()
        self.live_monitor.activateWindow()

    def add_to_live_monitor(self, game_id: str) -> None:
        self.on_live_monitor_clicked()
        self.live_monitor.add_game(game_id)

    def open_game_in_viewer(self, game_id: str) -> None:
        game = next((g for g in self.games if self.make_game_id(g) == game_id), None)
        if game is not None:
            index = self.games.index(game)
            self.open_viewer_for_claim(index, len(list(game.mainline_moves())), game_id)

    def on_follow_players_clicked(self) -> None:
        from src.views.dialogs.follow_dialog import FollowPlayersDialog
        if not self.games:
            self.on_pgn_updated()
        dialog = FollowPlayersDialog(self.follow, self.games, self.view)
        if dialog.exec():
            self.follow = dialog.get_settings()
            self.save_options()
            logger.info("following %d players, events: %s", len(self.follow.players),
                        ", ".join(sorted(self.follow.events)))

    def _update_game_sources(self) -> None:
        """game id -> source name, read from the per-source PGN files (headers only)."""
        import chess.pgn
        names = self.sources_dialog.source_names_by_file() if self.sources_dialog else {}
        mapping = {}
        cache = self.__dict__.setdefault("_source_file_cache", {})
        for path, name in names.items():
            try:
                stat = os.stat(path)
                mtime = (stat.st_mtime_ns, stat.st_size)  # a file being rewritten changes size too
            except OSError:
                continue
            cached = cache.get(path)
            if cached and cached[0] == mtime:
                ids = cached[1]
            else:
                ids = []
                try:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        while True:
                            headers = chess.pgn.read_headers(f)
                            if headers is None:
                                break
                            ids.append("|".join(headers.get(k, "") for k in
                                                ("Event", "Site", "Date", "Round", "White", "Black")))
                except OSError:
                    pass
                cache[path] = (mtime, ids)
            for gid in ids:
                mapping[gid] = name
        self.game_sources = mapping

    def source_name_of(self, game_id: str) -> str:
        return getattr(self, "game_sources", {}).get(game_id or "", "")

    def _notify_followed_game_ends(self) -> None:
        from src.follow import GAME_END
        from src.i18n import tr
        finished = self._game_end_tracker.new_results(self.games, self.make_game_id)
        if GAME_END not in self.follow.events or not self.follow.players:
            return
        for game in finished:
            white = game.headers.get("White", "")
            black = game.headers.get("Black", "")
            if not self.follow.followed_in(white, black):
                continue
            result = game.headers.get("Result", "*")
            board = ClaimsManager.get_board_number(game)
            logger.info("followed player finished: #%s %s - %s %s", board, white, black, result)
            self.push.notify_game_end(board, white, black, result,
                                      source=self.source_name_of(self.make_game_id(game)))
            if self.notifications_enabled:
                from src.notifications.windows_toast import show_toast
                from src.helpers import resource_path
                toaster = getattr(self.view.claims_table_widget, "notification", None)
                if sys.platform == "win32":
                    prefix = self.source_name_of(self.make_game_id(game))
                    show_toast(toaster, (f"[{prefix}] " if prefix else "") + "\u2605 " + tr("Game finished")
                               + f" #{board}: {result}",
                               f"{white} - {black}", resource_path("notification_icon.png"))

    def on_language_selected(self, language: str) -> None:
        """Store the UI language; it is applied on the next start."""
        from PyQt6.QtWidgets import QMessageBox
        from src.i18n import tr
        if language == self.language:
            return
        self.language = language
        self.save_options()
        QMessageBox.information(self.view, "Akiba", tr("Restart Akiba to change the language."))

    def on_open_log_folder_clicked(self) -> None:
        """Open the folder containing akiba.log in the file manager."""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        from src.logging_setup import get_log_path
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(get_log_path())))

    def on_about_clicked(self) -> None:
        self.view.load_about_dialog()

    # -------------------------------------------------------------------------
    # BOARD VIEWER
    # -------------------------------------------------------------------------

    def on_board_viewer_clicked(self):
        """
        Opens the Board Viewer and jumps to the correct game
        based on the selected claim entry.
        
        FIX: Always get data from column 0 of the clicked row,
        because userData is only stored in column 0 items.
        """
        app_path = get_appdata_path()
        pgn_path = os.path.join(app_path, "games.pgn")

        if self.board_viewer is None:
            self.board_viewer = BoardViewerWindow(pgn_path)

        # Get the current index
        index = self.view.claims_table.currentIndex()
        
        # FIX: Always use column 0 for data lookup (userData is only in column 0)
        if index.isValid():
            # Get index from column 0 of the same row
            col0_index = index.model().index(index.row(), 0, index.parent())
            data = col0_index.data(Qt.ItemDataRole.UserRole)
            
            if data:
                gid = data.get("game_id", "")
                game_index = data.get("game_index", 0)

                if gid and gid == self.current_viewed_gid:
                    self.board_viewer.show()
                    return

                idx = self.game_id_to_index.get(gid, game_index)
                self.current_viewed_gid = gid
                self.board_viewer.load_game_at_index(idx)

        self.board_viewer.show()

    def open_viewer_for_claim(self, game_index: int, move_index: int, game_id: str = None):
        """
        Opens the Board Viewer and jumps to a specific move
        (used when clicking on a table row).
        
        BUG FIX #3: Uses game_id lookup instead of direct index access because 
        the games list may have been reordered after PGN updates.
        
        Args:
            game_index: Original stored index (may be stale)
            move_index: Move position within the game
            game_id: Stable game identifier for accurate lookup (preferred method)
        """
        app_path = get_appdata_path()
        pgn_path = os.path.join(app_path, "games.pgn")

        if self.board_viewer is None:
            self.board_viewer = BoardViewerWindow(pgn_path)
        else:
            self.on_pgn_updated()

        # BUG FIX #3: Use game_id to find the current correct index
        # This ensures we open the right game even after PGN reloads reorder the list
        try:
            actual_index = game_index  # Default fallback
            
            if game_id and game_id in self.game_id_to_index:
                # Primary method: Look up by stable game_id
                actual_index = self.game_id_to_index[game_id]
            elif len(self.games) > game_index:
                # Secondary method: Verify index is still valid
                current_game = self.games[game_index]
                gid = self.make_game_id(current_game)
                actual_index = self.game_id_to_index.get(gid, game_index)

            self.board_viewer.load_game_at_index(actual_index)
            self.board_viewer.jump_to_move(move_index)
        except Exception:
            return

        self.board_viewer.show()
        self.board_viewer.raise_()

    # -------------------------------------------------------------------------
    # PGN UPDATE
    # -------------------------------------------------------------------------

    def on_pgn_updated(self):
        app_path = get_appdata_path()
        pgn_path = os.path.join(app_path, "games.pgn")

        if not os.path.exists(pgn_path):
            return

        import chess.pgn
        self.games = []
        
        # Track illegal moves detected during parsing - stores full game and error info
        illegal_move_games = []  # List of (game_index, san_move)
        
        # Log file for tracking illegal moves
        log_path = os.path.join(app_path, "illegal_moves_log.txt")
        
        import io
        with open(pgn_path, "r", encoding="utf-8") as pgn_file:
            pgn_text = pgn_file.read()

        with io.StringIO(pgn_text) as f:
            game_index = 0
            
            while True:
                try:
                    start = f.tell()
                    g = chess.pgn.read_game(f)
                    if not g:
                        break
                    raw_game = pgn_text[start:f.tell()]
                    if is_phantom_game(g):
                        # fragment of a truncated PGN - not a game (Scan skips it too)
                        logger.info("skipped a PGN fragment without players")
                        continue
                    
                    # Check for errors in the loaded game
                    if g.errors:
                        for error in g.errors:
                            illegal_move_games.append((game_index, str(error), raw_game))
                            
                            # Log detailed info to file
                            with open(log_path, 'a', encoding='utf-8') as log_file:
                                log_file.write(f"\n{'='*60}\n")
                                log_file.write(f"Game #{game_index + 1}\n")
                                log_file.write(f"Error: {error}\n\n")
                                
                                # Log game headers
                                for header in ['Event', 'Site', 'Date', 'Round', 'White', 'Black']:
                                    value = g.headers.get(header, '')
                                    log_file.write(f"{header}: {value}\n")
                                log_file.write("\n")
                                
                                # Log moves before the error
                                board = chess.Board()
                                move_num = 1
                                for node in g.mainline():
                                    try:
                                        move = node.move
                                        san = board.san(move)
                                        board.push(move)
                                        log_file.write(f"{move_num}. {san} ")
                                        if move_num % 2 == 0:
                                            log_file.write("\n")
                                        move_num += 1
                                    except Exception as e:
                                        # This is where the error occurred
                                        log_file.write(f"\n>>> ERROR HERE: {node.move} - {e}\n<<<\n")
                                        break
                                log_file.write("\n\n")
                    
                    self.games.append(g)
                    game_index += 1
                    
                except (chess.InvalidMoveError, chess.IllegalMoveError) as e:
                    # Fallback for direct exceptions
                    illegal_move_games.append((game_index, str(e), ""))
                    
                    with open(log_path, 'a', encoding='utf-8') as log_file:
                        log_file.write(f"\n{'='*60}\n")
                        log_file.write(f"Game #{game_index + 1} (direct exception)\n")
                        log_file.write(f"Error: {e}\n\n")
                    
                    game_index += 1
                    
                except Exception as e:
                    # Other parsing errors
                    break
        
        # Close the log file explicitly
        with open(log_path, 'a', encoding='utf-8') as log_file:
            log_file.write(f"\n{'='*60}\n")
            log_file.write(f"Total games processed: {game_index}\n")
            log_file.write(f"Illegal moves detected: {len(illegal_move_games)}\n")

        # Rebuild stable mapping game_id -> current index
        self.game_id_to_index = {}
        for idx, game in enumerate(self.games):
            gid = self.make_game_id(game)  # Use header-based ID only
            self.game_id_to_index[gid] = idx

        # Reload viewer if open
        if self.board_viewer is not None:
            self.board_viewer.reload_pgn(pgn_path)

        # Which source each game comes from (for named sources)
        self._update_game_sources()
        self.view.set_games_summary(self.games)
        monitor = getattr(self, "live_monitor", None)
        if monitor is not None and monitor.isVisible():
            monitor.refresh()

        # Games of followed players that just finished
        self._notify_followed_game_ends()

        # Process illegal moves detected during parsing and add to claims -
        # only once the move is completed (clock pressed / next move played)
        self._process_illegal_moves(illegal_move_games)

        # Run all reminders through the manager
        self.run_all_reminders()

        # Refresh table
        self.view.refresh_table()

    # Games without clock times: report an illegal move that stays this long
    ILLEGAL_MOVE_GRACE_NO_CLOCK = 60
    # Completed illegal move (clock pressed / next move) must stay this long
    ILLEGAL_MOVE_CONFIRM_SECONDS = 30

    def _process_illegal_moves(self, illegal_move_games) -> None:
        """
        Report an illegal move only when it is a real move: the player pressed
        the clock after it ([%clk] on the move) or another move followed. A piece
        put on a wrong square for a moment is corrected before that and ignored.
        For games without clock data the illegal move must persist for
        ILLEGAL_MOVE_GRACE_NO_CLOCK seconds.
        """
        import time
        from src.claims.illegal_move_confirm import illegal_move_status

        pending = getattr(self, "_pending_illegal", {})
        still_pending = {}
        now = time.monotonic()
        seen = set()
        for game_idx, error_msg, raw_game in illegal_move_games:
            if game_idx in seen or game_idx >= len(self.games):
                continue
            seen.add(game_idx)
            game = self.games[game_idx]
            valid_moves = len(list(game.mainline_moves()))
            key = (self.make_game_id(game), valid_moves, error_msg)
            completed, has_clock = illegal_move_status(raw_game, valid_moves) if raw_game else (True, False)
            first_seen = pending.get(key, now)
            age = now - first_seen
            # DGT boards sometimes misread a move even after the clock press and
            # LiveChess corrects it seconds later - it must also persist
            if (completed and age >= self.ILLEGAL_MOVE_CONFIRM_SECONDS) or \
                    (not has_clock and age >= self.ILLEGAL_MOVE_GRACE_NO_CLOCK):
                self._add_illegal_move_claim(game_idx, error_msg)
            else:
                if key not in pending:
                    logger.info("illegal move on board %s waiting for confirmation (%s): %s",
                                ClaimsManager.get_board_number(game),
                                "clock pressed" if completed else "no clock press", error_msg)
                still_pending[key] = first_seen
        self._pending_illegal = still_pending

    def _add_illegal_move_claim(self, game_idx: int, error_msg: str) -> None:
        """Add an illegal move claim entry for a game that failed to parse."""
        # Skip if illegal move reminder is disabled in menu
        if not self.illegal_move_reminder_enabled:
            return
        
        if game_idx >= len(self.games):
            return
        
        game = self.games[game_idx]
        
        # Check if this game already has an illegal move claim
        game_id = self.make_game_id(game)
        if game_id in self.illegal_move_reminder_fired:
            return
        
        # Extract players from the game (use consistent "White - Black" format)
        white = game.headers.get("White", "Player1")
        black = game.headers.get("Black", "Player2")
        players = f"{white} - {black}"
        
        board_number = ClaimsManager.get_board_number(game)
        
        # Extract SAN move and fullmove number from error message
        import re
        match = re.search(r"illegal san: '([^']+)'", error_msg)
        san_move = match.group(1) if match else "Unknown"
        
        # Get the ACTUAL number of valid moves in this game (before the illegal move)
        try:
            actual_move_count = len(list(game.mainline_moves()))
        except Exception:
            actual_move_count = 0
        
        # For navigation: last valid move is the actual count
        last_valid_move = actual_move_count
        
        # Move number of the illegal move from the count of valid half-moves before it
        illegal_move_num = actual_move_count // 2 + 1
        dots = "." if actual_move_count % 2 == 0 else "..."
        move_display = f"{illegal_move_num}{dots}{san_move}"
        
        entry = ClaimEntry(
            type=ClaimType.ILLEGAL_MOVE,
            board_number=board_number,
            players=players,
            move=move_display,
            game_index=game_idx,
            move_counter=last_valid_move,  # Navigate to last valid position
            start_move_counter=0,
            comment=f"Parsing failed: {error_msg}",
            game_id=self.make_game_id(game),
        )
        
        # Add to claims_store AND update GUI table
        self.claims_store.add_claim(entry)
        self.view.add_item_to_table(entry)
        
        # Mark this game as processed to prevent duplicates
        self.illegal_move_reminder_fired.add(self.make_game_id(game))

    # -------------------------------------------------------------------------
    # MISMATCHED RESULT ANALYSIS
    # -------------------------------------------------------------------------

    def _start_mismatched_analysis(self, game, declared_result: str, final_fen: str):
        """
        Start async Stockfish analysis for a single game.
        Called from Scan worker when a new finished game is detected.
        Uses queue to limit concurrent analyses to prevent Stockfish overload.
        """
        if not self.possible_incorrect_result_enabled:
            return
        game_id = "|".join([
            game.headers.get("Event", ""),
            game.headers.get("Round", ""),
        ])
        # Update engine status to show activity with current timestamp
        current_time = datetime.now().strftime('%H:%M:%S')
        self.view.set_engine_status(True, current_time)
        
        from src.models.workers import EngineWorker, GameAnalysisItem
        
        # Create analysis item and add to queue
        analysis_item = GameAnalysisItem(game, declared_result, final_fen)
        
        if not hasattr(self, 'game_queue'):
            self.game_queue = None
        
        if self.game_queue is None:
            return
        
        if not self.game_queue.put(analysis_item):
            pass  # Game already in queue, skip
    
    def _start_engine_worker(self):
        """Start the engine worker that processes the game queue."""
        if not self.possible_incorrect_result_enabled:
            return
        
        # Reset stopped flag when starting new scan
        self._engine_stopped = False
        
        from src.models.workers import EngineWorker
        # Show engine status when worker starts
        current_time = datetime.now().strftime('%H:%M:%S')
        self.view.set_engine_status(True, current_time)
        
        # Create and start a single persistent worker for the queue
        self.engine_worker = EngineWorker(get_engine_path(), self.game_queue)
        
        def on_analysis_complete(analyzed_game, entry):
            """Callback when analysis finishes."""
            # Check if engine was stopped - don't update status if so
            if getattr(self, '_engine_stopped', False):
                return
            if entry:
                # Do not alert yet: results are often corrected on the board
                # (kings placed again) a moment later - confirm after a delay
                self._schedule_result_alert(analyzed_game, entry)
            # Update timestamp after each analysis
            current_time = datetime.now().strftime('%H:%M:%S')
            self.view.set_engine_status(True, current_time)
        
        self.engine_worker.analysis_complete.connect(on_analysis_complete)
        
        # Connect logging signal (now includes board_number and last_moves)
        def on_log(game_index, board_number, declared_result, engine_score, last_moves):
            self._log_engine_analysis(game_index, board_number, declared_result, engine_score, last_moves)
        
        self.engine_worker.analysis_logged.connect(on_log)
        
        # Start the worker
        self.engine_worker.start()

        # Periodic re-check of all finished games
        from PyQt6.QtCore import QTimer
        if getattr(self, "result_recheck_timer", None) is None:
            self.result_recheck_timer = QTimer(self)
            self.result_recheck_timer.timeout.connect(self._recheck_finished_results)
        self.result_recheck_timer.start(self.RESULT_RECHECK_MINUTES * 60 * 1000)
    # Suspicious result must still be in the PGN after this many seconds
    RESULT_CONFIRM_SECONDS = 30
    # Re-check all finished games with the engine this often (minutes)
    RESULT_RECHECK_MINUTES = 10

    def _schedule_result_alert(self, analyzed_game, entry) -> None:
        from PyQt6.QtCore import QTimer
        result = analyzed_game.headers.get("Result", "*")
        plies = len(list(analyzed_game.mainline_moves()))
        logger.info("suspicious result on board %s (%s), confirming in %d s",
                    entry.board_number, result, self.RESULT_CONFIRM_SECONDS)
        QTimer.singleShot(self.RESULT_CONFIRM_SECONDS * 1000,
                          lambda: self._confirm_result_alert(entry, result, plies))

    def _confirm_result_alert(self, entry, result: str, plies: int) -> None:
        """Show the alert only if the same result and position are still in the PGN."""
        if getattr(self, '_engine_stopped', False):
            return
        current = next((g for g in self.games if self.make_game_id(g) == entry.game_id), None)
        if current is None:
            return
        if (current.headers.get("Result", "*") != result
                or len(list(current.mainline_moves())) != plies):
            logger.info("result on board %s changed (%s -> %s), no alert", entry.board_number,
                        result, current.headers.get("Result", "*"))
            return
        shown = self.__dict__.setdefault("_result_alerts_shown", set())
        key = (entry.type, entry.game_id, result, plies)
        if key in shown:
            return
        shown.add(key)
        self.claims_store.add_claim(entry)
        self.view.add_item_to_table(entry)

    def _recheck_finished_results(self) -> None:
        """Periodic re-analysis of every finished game (catches later changes)."""
        import chess
        from src.models.workers import GameAnalysisItem
        if getattr(self, '_engine_stopped', False) or getattr(self, 'game_queue', None) is None:
            return
        if not self.possible_incorrect_result_enabled:
            return
        count = 0
        for game in list(self.games):
            result = game.headers.get("Result", "*")
            if result == "*":
                continue
            board = chess.Board()
            for move in game.mainline_moves():
                board.push(move)
            self.game_queue.requeue(GameAnalysisItem(game, result, board.fen()))
            count += 1
        logger.info("periodic engine re-check of %d finished games", count)

    def _update_engine_timestamp(self):
        """Update engine status with current timestamp when analysis completes."""
        if self.possible_incorrect_result_enabled:
            current_time = datetime.now().strftime('%H:%M:%S')
            self.view.set_engine_status(True, current_time)
    
    def _log_engine_analysis(self, game_index: int, board_number: str, declared_result: str, engine_score: float, last_moves: str = ""):
        """Log engine analysis to file for debugging."""
        import os
        
        app_path = get_appdata_path()
        log_path = os.path.join(app_path, 'engine_analysis.log')
        
        # Determine if result is correct based on evaluation
        threshold = 3.0
        verdict = "YES"
        
        parsed_result = self._parse_result_for_log(declared_result)
        if parsed_result is not None:
            if parsed_result == 1 and engine_score < -threshold:
                verdict = "NO (mismatch)"
            elif parsed_result == -1 and engine_score > threshold:
                verdict = "NO (mismatch)"
            elif parsed_result == 0 and abs(engine_score) > threshold:
                verdict = "NO (mismatch)"
        
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        # Include last 3 moves for context (helps referee understand why the evaluation is what it is)
        moves_info = f" Moves: {last_moves}" if last_moves else ""
        log_line = f"[{timestamp}] Game {game_index} (Board {board_number}): Result={declared_result}, Engine eval={engine_score:+.2f}{moves_info}, Correct? {verdict}\n"
        
        try:
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(log_line)
        except Exception:
            pass  # Ignore log errors
    
    def _parse_result_for_log(self, result: str) -> int:
        """Parse PGN result string to numeric value."""
        result_map = {
            "1-0": 1,
            "0-1": -1,
            "1/2-1/2": 0,
            "*": None
        }
        return result_map.get(result)
    
    # -------------------------------------------------------------------------
    # NEW MOVE
    # -------------------------------------------------------------------------

    def on_new_move(self):
        if not self.sources_dialog:
            return

        filepaths = self.sources_dialog.get_filepath_list()
        if not filepaths:
            return

        lock = Lock()
        make_pgn = MakePgn(filepaths, self.stop_event, lock)
        make_pgn.make_pgn()

        self.on_pgn_updated()

    # -------------------------------------------------------------------------
    # CENTRAL REMINDER ORCHESTRATION
    # -------------------------------------------------------------------------

    def run_all_reminders(self):
        """
        Central method that runs all enabled reminders via ReminderManager
        and updates the GUI table with returned results.
        """
        if not self.games:
            return

        (
            entries,
            scoresheet_fired,
            time_control_fired,
            low_time_shown,
            flag_fall_shown,
            sofia_rule_fired,
            two_fold_shown,
            fortyfive_moves_shown,
            illegal_move_shown,
            mismatched_result_shown
        ) = self.reminder_manager.run_all_reminders(
            games=self.games,
            claims_model=self.claims_manager,
            scoresheet_enabled=self.scoresheet_reminder_enabled,
            scoresheet_threshold=self.scoresheet_threshold,
            scoresheet_fired=self.scoresheet_reminder_fired,
            time_control_enabled=self.timecontrol_reminder_enabled,
            time_control_threshold=self.timecontrol_threshold,
            time_control_fired=self.timecontrol_reminder_fired,
            two_fold_enabled=self.possible_threefold_enabled,
            fiftymove_enabled=self.possible_fiftymove_enabled,
            low_time_enabled=self.low_time_reminder_enabled,
            low_time_threshold_seconds=self.low_time_threshold_seconds,
            flag_fall_enabled=self.flag_fall_reminder_enabled,
            flag_fall_shown=self.flag_fall_reminder_fired,
            sofia_rule_enabled=self.sofia_rule_enabled,
            sofia_rule_threshold=self.sofia_rule_threshold,
            sofia_rule_fired=self.sofia_rule_fired,
            illegal_move_enabled=self.illegal_move_reminder_enabled,
            mismatched_result_enabled=self.possible_incorrect_result_enabled,
        )

        # Update state dictionaries from manager results
        self.scoresheet_reminder_fired = scoresheet_fired
        self.timecontrol_reminder_fired = time_control_fired
        self.low_time_reminder_fired = low_time_shown
        self.flag_fall_reminder_fired = flag_fall_shown
        self.sofia_rule_fired = sofia_rule_fired
        self.mismatched_result_shown = mismatched_result_shown

        # NEW: Store all claims in central store AND update GUI table
        for entry in entries:
            if entry.type == ClaimType.SOFIA_RULE:
                # Depends on the final result: confirm it is not a transient
                # state of the transmission before showing / notifying
                game = next((g for g in self.games if self.make_game_id(g) == entry.game_id), None)
                if game is not None:
                    self._schedule_result_alert(game, entry)
                    continue
            self.claims_store.add_claim(entry)  # Store in ClaimsStore
            self.view.add_item_to_table(entry)


    # -------------------------------------------------------------------------
    # SCAN / DOWNLOAD / STOP
    # -------------------------------------------------------------------------

    def on_clear_resolved_clicked(self) -> None:
        """
        Clear selected rows from the claims table.
        Called when user clicks "Clear Resolved" button (referee has responded).
        """
        self.view.claims_table_widget.clear_selected_rows()

    def on_sources_button_clicked(self) -> None:
        if not self.sources_dialog:
            self.sources_dialog = SourceDialogController()
            self.sources_dialog.view.accepted.connect(self.update_status_bar_sources)
            self.sources_dialog.do_start()
            return

        if self.scan_worker and self.scan_worker.isRunning():
            self.on_stop_button_clicked(blocking=False)

        self.sources_dialog.do_resume()

    def on_scan_button_clicked(self) -> None:
        # A stop still running in the background must finish first
        if self.stop_worker is not None and self.stop_worker.isRunning():
            self.stop_worker.wait()
        if not self.sources_dialog or not self.sources_dialog.has_valid_sources():
            sources_warning()
            return

        if self.scan_worker and self.scan_worker.isRunning():
            return

        # Clear shown_reminders for fresh scan - this allows reminders to reappear
        self.mismatched_result_shown.clear()

        # DON'T clear acknowledged rows - they should reappear after restart
        # unless they were explicitly cleared via Clear Resolved button
        # self.view.clear_ack_rows()  <-- REMOVE THIS!
        self.view.change_scan_button_text(Status.ACTIVE)

        # Send beacon (anonymous report after clicking Start Scan)
        try:
            import threading
            from src.beacon import send_beacon_async
            threading.Thread(target=send_beacon_async, daemon=True).start()
        except Exception:
            pass  # Ignore errors - app must work without beacon

        download_list = self.sources_dialog.get_download_list()
        if download_list:
            self.start_download_worker(download_list)

        games_pgn_mutex = Lock()
        self.start_make_png_worker(games_pgn_mutex)
        self.start_scan_worker(games_pgn_mutex)

    def on_stop_button_clicked(self, blocking: bool = True) -> None:
        """Stop scanning. blocking=False returns at once and finishes in the background."""
        if not self.scan_worker or not self.scan_worker.isRunning():
            return
        if self.stop_worker is not None and self.stop_worker.isRunning():
            if blocking:
                self.stop_worker.wait()
            return

        # Set flag to prevent engine status from being re-enabled after stop
        self._engine_stopped = True
        
        engine_worker = getattr(self, 'engine_worker', None)
        
        self.stop_worker = Stop(
            self.stop_event,
            self.make_pgn_worker,
            self.scan_worker,
            self.download_worker,
            self.livechess_worker,
            engine_worker
        )

        # Pass game_queue to stop worker for poison pill delivery
        if hasattr(self.stop_worker, 'game_queue'):
            self.stop_worker.game_queue = getattr(self, 'game_queue', None)

        self.stop_worker.enable_signal.connect(self.on_stop_enable_status)
        self.stop_worker.disable_signal.connect(self.on_stop_disable_status)
        self.stop_worker.start()
        if not blocking:
            # GUI stays responsive (e.g. the sources dialog opens at once)
            self.stop_worker.finished.connect(self._after_stop)
            return
        self.stop_worker.wait()
        self._after_stop()

    def _after_stop(self) -> None:
        self.view.change_scan_button_text(Status.STOP)
        
        # Hide engine status immediately when Stop is clicked (like Scan does)
        self.view.set_engine_status(False)

        # DON'T reset checkers - preserve seen_positions for continuity
        # self.reminder_manager.reset_checkers()
        self.stop_event.clear()
        
        # Clear game queue after stopping workers
        if hasattr(self, 'game_queue') and self.game_queue:
            self.game_queue.clear()
        
        # Hide engine status when scan stops
        self.view.set_engine_status(False)
        
        # Clear livechess sources on stop
        self.livechess_sources = []

    def on_stop_enable_status(self) -> None:
        self.view.set_scan_status(Status.ACTIVE)

    def on_stop_disable_status(self) -> None:
        self.view.set_scan_status(Status.INACTIVE)
        self.view.change_scan_button_text(Status.INACTIVE)

    def update_status_bar_sources(self) -> None:
        valid_sources = self.sources_dialog.get_valid_sources()
        if valid_sources:
            self.view.set_sources_status(Status.OK, valid_sources)
        else:
            self.view.set_sources_status(Status.ERROR)

    def update_claims_table(self, entry) -> None:
        # Skip adding to table if already cleared (was marked via Clear Resolved)
        if entry.cleared:
            return
        
        self.claims_store.add_claim(entry)  # Store in ClaimsStore
        self.view.add_item_to_table(entry)

    def update_download_status(self, status: Status) -> None:
        # Failures are logged per source in DownloadGames
        self.view.set_download_status(status)

    def on_pgn_source_failed(self, url: str, reason: str) -> None:
        """A PGN source has been failing continuously - tell the arbiter's phone."""
        self.push.notify_pgn_error(url, reason)

    def on_push_settings_clicked(self) -> None:
        from src.views.dialogs.push_dialog import PushSettingsDialog
        dialog = PushSettingsDialog(self.push.settings, self.push, self.view)
        if dialog.exec():
            self.push.settings = dialog.get_settings()
            self.save_options()

    def update_bar_scan_status(self, status: Status) -> None:
        self.view.set_scan_status(status)
        if status == Status.WAIT and getattr(self, "_notify_priming", False):
            # First full scan pass done: games finished by now are history
            self._notify_priming = False
            self._history_games = {self.make_game_id(g) for g in self.games
                                   if g.headers.get("Result", "*") != "*"}
            logger.info("notifications active; %d finished games are not notified",
                        len(self._history_games))

    def should_notify(self, entry) -> bool:
        """
        Phone / desktop notifications only for what happens while monitoring:
        not during the first scan pass after (re)start - everything found
        then was already notified before - and not for games that had
        already finished when monitoring started. The table shows all.
        """
        if getattr(self, "_notify_priming", False):
            return False
        return (entry.game_id or "") not in getattr(self, "_history_games", set())

    # -------------------------------------------------------------------------
    # WORKERS
    # -------------------------------------------------------------------------

    def start_download_worker(self, downloads: Dict[str, str]) -> None:
        if not downloads:
            return

        self.download_worker = DownloadGames(downloads, self.stop_event)
        self.download_worker.status_signal.connect(self.update_download_status)
        self.download_worker.source_failed_signal.connect(self.on_pgn_source_failed)
        self.download_worker.start()

    def start_make_png_worker(self, lock: Lock) -> None:
        filepaths = self.sources_dialog.get_filepath_list()
        
        # FIX: Run make_pgn SYNCHRONOUSLY first to ensure games.pgn exists
        # This ensures on_pgn_updated() has a valid file to load
        make_pgn_sync = MakePgn(filepaths, None, lock)
        make_pgn_sync.make_pgn()  # Synchronous call
        
        # Then start the background worker for periodic updates
        self.make_pgn_worker = MakePgn(filepaths, self.stop_event, lock)
        self.make_pgn_worker.start()

    def start_scan_worker(self, lock: Lock) -> None:
        self._notify_priming = True
        app_path = get_appdata_path()
        filename = os.path.join(app_path, "games.pgn")

        # Create a new game queue for this scan session
        from src.models.workers import GameQueue
        self.game_queue = GameQueue()
        
        self.scan_worker = Scan(
            self.claims_manager,
            filename,
            lock,
            None,
            self.stop_event,
            self.game_queue
        )

        self.scan_worker.add_entry_signal.connect(self.update_claims_table)
        self.scan_worker.status_signal.connect(self.update_bar_scan_status)

        if hasattr(self.scan_worker, "new_move_signal"):
            self.scan_worker.new_move_signal.connect(self.on_new_move)
        
        # Connect game_queued signal for async Stockfish analysis (now handled by engine worker)
        if hasattr(self.scan_worker, "game_queued_signal"):
            self.scan_worker.game_queued_signal.connect(self._start_mismatched_analysis)

        # Clear old log file on new scan
        try:
            log_path = os.path.join(get_appdata_path(), 'engine_analysis.log')
            if os.path.exists(log_path):
                os.remove(log_path)
        except Exception:
            pass
        
        # Only if games.pgn already exists (created by make_pgn_worker)
        pgn_path = os.path.join(app_path, "games.pgn")
        if os.path.exists(pgn_path):
            self.on_pgn_updated()

        # Start the engine worker to process the queue
        self._start_engine_worker()

        self.scan_worker.finished.connect(self.on_new_move)

        self.scan_worker.start()


# -------------------------------------------------------------------------
# SOURCE DIALOG CONTROLLER
# -------------------------------------------------------------------------

class SourceDialogController:

    def __init__(self) -> None:
        self.view = AddSourceDialog(self)
        self.app_path = get_appdata_path()
        self.threadPool = QThreadPool()
        self.filepaths: List[str] = []
        self.downloads: Dict[str, str] = dict()
        self.livechess_sources: List[LiveChessCloudSource] = []
        self.livechess_worker = None
        self.stop_event = Event()  # Add stop event for polling workers
        self.apply_lock = Lock()
        self.shown_reminders = set()

    def do_start(self) -> None:
        self.view.set_gui()
        self.restore()
        self.view.show()

    def do_resume(self) -> None:
        self.view.show()

    def get_filepath_list(self) -> List[str]:
        return self.filepaths

    def get_valid_sources(self) -> List[SourceHBox]:
        return self.view.sources

    def get_download_list(self) -> Dict[str, str]:
        return self.downloads

    def has_valid_sources(self) -> bool:
        return len(self.filepaths) > 0

    def restore(self) -> None:
        try:
            with open(os.path.join(self.app_path, "sources.json"), "r") as file:
                data = json.load(file)
                
                if not data:
                    self.view.add_default_source()
                    return
                
                for entry in data:
                    option = entry["option"]
                    value = entry["value"]
                    name = str(entry.get("name") or "")
                    # For LiveChessCloud sources, restore round number too (default to 1 if not specified)
                    if option == 2 and "round" in entry:
                        round_num = entry["round"] if entry.get("round", 1) > 0 else 1
                        self.view.add_source(option, value, round_num, name=name)
                    else:
                        self.view.add_source(option, value, name=name)
        except Exception as e:
            self.view.add_default_source()

    def on_delete_button_clicked(self, source_hbox) -> None:
        with self.apply_lock:
            self.remove_hbox_refs(source_hbox)
            self.view.remove_hbox(source_hbox)

    def remove_hbox_refs(self, hbox: SourceHBox) -> None:
        value = hbox.get_value()
        if hbox.has_url() and value in self.downloads:
            filepath = self.downloads[value]
            if filepath in self.filepaths:
                self.filepaths.remove(filepath)
            del self.downloads[value]
        elif hbox.has_local() and value in self.filepaths:
            self.filepaths.remove(value)

    def on_apply_button_clicked(self) -> None:
        Thread(target=self._apply_thread, daemon=True).start()

    def _apply_thread(self) -> None:
        with self.apply_lock:
            self.filepaths = []
            self.downloads = {}
            self.livechess_sources = []

            download_id = 0
            for source_hbox in self.view.sources:
                option = source_hbox.get_source_index()
                value = source_hbox.get_value()
                
                if source_hbox.has_url():
                    self.threadPool.start(CheckDownload(self, source_hbox, download_id))
                    download_id += 1
                elif source_hbox.has_local():
                    filepath = source_hbox.get_value()
                    if os.path.exists(filepath):
                        source_hbox.set_status(Status.OK)
                        if filepath not in self.filepaths:
                            self.filepaths.append(filepath)
                    else:
                        source_hbox.set_status(Status.ERROR)
                elif source_hbox.has_livechess():
                    # Validate LiveChessCloud URL
                    from src.models.download import check_livechess_download
                    url = source_hbox.get_value()
                    round_num = source_hbox.get_round_num()
                    
                    if check_livechess_download(url):
                        source_hbox.set_status(Status.OK)
                        livechess_source = LiveChessCloudSource(url, round_num)
                        self.livechess_sources.append(livechess_source)
                        # Add the generated PGN file path
                        filepath = os.path.join(self.app_path, livechess_source.get_filename())
                        if filepath not in self.filepaths:
                            self.filepaths.append(filepath)
                    else:
                        source_hbox.set_status(Status.ERROR)
                else:
                    pass  # Unknown option, ignore

            self.threadPool.waitForDone()

            if self.filepaths:
                self.view.enable_ok_button()

    def on_ok_button_clicked(self) -> None:
        # Save sources IMMEDIATELY before closing dialog
        self.save_sources()
        
        # Now start background workers (they will run after app main loop starts)
        if self.livechess_sources:
            from src.models.workers import DownloadLiveChessGames
            import time
            
            def background_work():
                time.sleep(1)  # Small delay to let dialog close
                
                self.livechess_worker = DownloadLiveChessGames(
                    self.livechess_sources, 
                    self.stop_event
                )
                self.livechess_worker.start()
            
            Thread(target=background_work, daemon=True).start()

        self.view.accept()
        self.view.close()

    def _exit_thread(self) -> None:
        # Download regular web PGNs
        if self.downloads:
            worker = DownloadGames(self.downloads)
            worker.start()
            worker.wait()

        # Download/update LiveChessCloud live games (with polling)
        if self.livechess_sources:
            self.livechess_worker = DownloadLiveChessGames(
                self.livechess_sources, 
                self.stop_event
            )
            self.livechess_worker.start()
            
            # Wait for FIRST full poll cycle before creating combined PGN
            # This ensures livechess PGN files are created BEFORE MakePgn runs
            if not self.stop_event.is_set():
                import time
                time.sleep(5)  # Wait longer to ensure games are fetched
                self.livechess_worker.wait()  # Wait for initial run to complete

        make_pgn = MakePgn(self.filepaths)
        make_pgn.start()
        make_pgn.join()

        # Save sources using SourceDialogController's method
        if self.sources_dialog:
            self.sources_dialog.save_sources()

        # AUTOMATICALLY start Scan worker so user can see games immediately
        from threading import Thread
        Thread(target=self._auto_start_scan, daemon=True).start()

    def _auto_start_scan(self) -> None:
        """Automatically start scan after PGN is created."""
        import time
        
        # Wait for MakePgn to finish and file system to sync
        time.sleep(2)  # Increased from 0.5 to 2 seconds
        
        if not self.sources_dialog or not self.sources_dialog.has_valid_sources():
            return
            
        if self.scan_worker and self.scan_worker.isRunning():
            return

        # Set status to ACTIVE
        self.view.change_scan_button_text(Status.ACTIVE)

        games_pgn_mutex = Lock()
        self.start_make_png_worker(games_pgn_mutex)
        self.start_scan_worker(games_pgn_mutex)

    def save_sources(self) -> None:
        data = []
        
        for i, source in enumerate(self.view.sources):
            option = source.get_source_index()
            value = source.get_value()
            
            entry = {
                "option": option,
                "value": value
            }
            if source.get_name():
                entry["name"] = source.get_name()
            
            # Add round number for LiveChessCloud sources
            if source.has_livechess():
                round_num = source.get_round_num()
                entry["round"] = round_num if round_num is not None else 1
            
            data.append(entry)

        # Ensure directory exists
        sources_file = os.path.join(self.app_path, 'sources.json')
        
        try:
            os.makedirs(self.app_path, exist_ok=True)
            
            with open(sources_file, 'w') as file:
                json.dump(data, file, indent=4)
        except Exception as e:
            pass  # Silent failure for now

    def source_names_by_file(self) -> Dict[str, str]:
        """PGN file path -> source name, for sources that have a name."""
        names = {}
        for hbox in self.view.sources:
            name = hbox.get_name()
            if not name:
                continue
            value = hbox.get_value()
            if hbox.has_url() and value in self.downloads:
                names[self.downloads[value]] = name
            elif hbox.has_local():
                names[value] = name
            elif hbox.has_livechess():
                for source in self.livechess_sources:
                    if source.url == value:
                        names[os.path.join(self.app_path, source.get_filename())] = name
        return names

    def add_valid_url(self, url: str, download_id: int) -> None:
        filepath = os.path.join(self.app_path, f"games{download_id}.pgn")
        self.downloads[url] = filepath
        if filepath not in self.filepaths:
            self.filepaths.append(filepath)
