from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QStandardItemModel, QPixmap, QStandardItem, QColor
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QTreeView,
    QHeaderView,
    QAbstractItemView,
    QMenu,
)

from src.helpers import resource_path
from src.models.events import ClaimType
from src.i18n import tr

if TYPE_CHECKING:
    from src.controllers import ChessClaimController


# Row colour by how urgently the arbiter has to react:
#   critical - game is over / must be stopped now (automatic results, flag, illegal move)
#   claim    - claimable draw or rule violation to check
#   reminder - early warnings and routine reminders
ROW_PRIORITY = {
    ClaimType.FIVEFOLD: "critical",
    ClaimType.SEVENTYFIVE_MOVES: "critical",
    ClaimType.CHECKMATE_CLAIM: "critical",
    ClaimType.STALEMATE_CLAIM: "critical",
    ClaimType.DEAD_POSITION_CLAIM: "critical",
    ClaimType.FLAG_FALL_REMINDER: "critical",
    ClaimType.ILLEGAL_MOVE: "critical",
    ClaimType.THREEFOLD: "claim",
    ClaimType.FIFTY_MOVES: "claim",
    ClaimType.SOFIA_RULE: "claim",
    ClaimType.MISMATCHED_RESULT: "claim",
    ClaimType.TWO_FOLD_WARNING: "reminder",
    ClaimType.FORTYFIVE_MOVES_WARNING: "reminder",
    ClaimType.SCORESHEET_REMINDER: "reminder",
    ClaimType.TIMECONTROL_REMINDER: "reminder",
    ClaimType.LOW_TIME_REMINDER: "reminder",
}

# (background, text) per priority; acknowledged rows are greyed out
ROW_COLORS = {
    "critical": ("#f8d0d0", "#7a0000"),
    "claim": ("#ffe8bf", "#5c3b00"),
    "reminder": ("#dde9f7", "#1f3a5c"),
    "acknowledged": ("#ffffff", "#9a9a9a"),
}

PRIORITY_ROLE = Qt.ItemDataRole.UserRole + 1


def claim_family(claim_type: ClaimType):
    """(family, level): a stronger level replaces a weaker one of the same game."""
    return {
        ClaimType.TWO_FOLD_WARNING: ("repetition", 1),
        ClaimType.THREEFOLD: ("repetition", 2),
        ClaimType.FIVEFOLD: ("repetition", 3),
        ClaimType.FORTYFIVE_MOVES_WARNING: ("moves", 1),
        ClaimType.FIFTY_MOVES: ("moves", 2),
        ClaimType.SEVENTYFIVE_MOVES: ("moves", 3),
    }.get(claim_type, (claim_type.name, 1))


class ClaimsTable(QWidget):
    """
    Claims table component with styling and interaction logic.
    
    Displays detected claims/reminders in a sortable table.
    Handles row clicks to open Board Viewer at specific game positions.
    """
    
    __slots__ = [
        "controller",
        "table",
        "model",
        "ok_pixmap",
        "error_pixmap",
        "current_entry_game_index",
        "notification",
    ]

    def __init__(self, controller: ChessClaimController) -> None:
        super().__init__()
        self.controller = controller
        
        # Use same ClaimsStore as controller - SINGLE SOURCE OF TRUTH
        self.claims_store = controller.claims_store

        # Status icons
        self.ok_pixmap = QPixmap(resource_path("check_icon.png"))
        self.error_pixmap = QPixmap(resource_path("error_icon.png"))

        # Table setup
        self.table = QTreeView()
        self.model = QStandardItemModel()
        
        # Initialize notification system
        import platform
        if platform.system() == "Darwin":
            from src.notifications.mac import Notification
            self.notification = Notification()
        elif platform.system() == "Windows":
            from src.notifications.windows_toast import make_toaster
            self.notification = make_toaster("Akiba")
        else:
            self.notification = None

        self._filter_text = ""

        self._setup_table()
        self._build_layout()

        # Keep the filter applied when rows are added / removed
        self.model.rowsInserted.connect(lambda *_: self.apply_filter())
        self.model.rowsRemoved.connect(lambda *_: self.apply_filter())

    def keyPressEvent(self, event):
        """
        Handle keyboard events for the table.
        Space bar toggles Ack status on selected row(s).
        
        Args:
            event: The key event object
        """
        if event.key() == Qt.Key.Key_Space:
            # Get currently selected rows
            selected_rows = self.get_selected_rows()
            
            if selected_rows:
                # Space acknowledges all selected rows (or un-acknowledges if all already are)
                all_acked = all(self._is_acked(r) for r in selected_rows)
                self.set_ack(selected_rows, not all_acked)
                return  # Event handled
        
        super().keyPressEvent(event)

    def _setup_table(self) -> None:
        """Configure the claims table view and model."""
        # Allow focus for keyboard events (space bar handling)
        self.table.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.header().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setSortingEnabled(True)
        self.table.setIndentation(0)
        self.table.setUniformRowHeights(True)
        
        # Allow multiple row selection with Ctrl/Cmd or Shift
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        
        # IMPORTANT: Set selection behavior to preserve existing selection on clicks
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        
        # Set column headers - added "Ack" column at the end (after Move)
        labels = [tr(x) for x in ["#", "Timestamp", "Type", "Board", "Players", "Move", "Ack"]]
        self.model.setHorizontalHeaderLabels(labels)
        # Newest first (new rows are inserted at the top)
        self.table.header().setSortIndicator(0, Qt.SortOrder.DescendingOrder)
        self.table.setModel(self.model)

        # Row click → open Board Viewer on DOUBLE CLICK (not single click)
        self.table.doubleClicked.connect(self.on_claim_double_clicked)

        # Context menu for Ack toggle (right-click)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)

        header = self.table.header()

        # Auto-size for small columns
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)   # #
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)   # Timestamp
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)   # Type
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)   # Board
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)   # Ack

        # Players → stretch (fills available space), with minimum width of 150px
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(4, 150)   # Minimum width

        # Move → fixed width (250px - 50px = 200px)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(5, 200)   # Fixed width reduced by 50px

        # Make sure header does not stretch the last section
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(20)

        # Allow horizontal scrollbar if needed
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    def _build_layout(self) -> None:
        """Build the widget layout."""
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.table)
        self.setLayout(layout)

    def add_claim(self, entry) -> bool:
        """
        Add a new row to the claims table.

        One open row per game and family (repetitions 2x/3x/5x, move counters
        45/50/75): a new entry replaces open rows of the same or a weaker
        level; an entry weaker than an open row is not added.

        Returns:
            True if a row was added (the caller then may notify).
        """
        # Skip adding to table if already cleared (was marked via Clear Resolved)
        if entry.cleared:
            return False
        if not self._replace_family_rows(entry):
            return False
            
        claim_type = tr(entry.type.value)
        board_number = entry.board_number
        players = entry.players
        move = entry.move

        # Store game_index for safe row removal
        self.current_entry_game_index = entry.game_index


        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # Items to display in the table - added empty Ack column at the end
        items = [
            str(self.model.rowCount() + 1),  # Sequential number
            timestamp,
            claim_type,
            board_number,
            players,
            move,
            "",                               # Ack (empty by default)
        ]

        row = []
        for idx, item in enumerate(items):
            standard_item = self._create_standard_item(item, idx)
            row.append(standard_item)

        # Newest entries on top
        self.model.insertRow(0, row)

        # Colour the whole row by priority
        new_row = 0
        self.model.item(new_row, 0).setData(ROW_PRIORITY.get(entry.type, "reminder"), PRIORITY_ROLE)
        self._paint_row(new_row)

        # Store game and move index for Board Viewer navigation
        first_item = self.model.item(0, 0)
        first_item.setData({
            "game_index": entry.game_index,
            "move_index": entry.move_counter,
            "start_move_index": entry.start_move_counter,
            "game_id": entry.game_id,
            "type": entry.type.name,
        }, Qt.ItemDataRole.UserRole)

        # Set fixed row height for the new row to maintain consistency
        # Note: QTreeView doesn't support setRowHeight, relying on uniform row heights instead
        
        # No resize - let Stretch mode handle Move column automatically
        # Backup calls resizeColumnToContents but it may cause issues with Fixed columns
        self.table.scrollToTop()

        self.table.clearSelection()
        return True

    def notify_desktop(self, entry) -> None:
        """Windows / macOS notification for an entry (named source and board first)."""
        claim_type = tr(entry.type.value)
        board_number = entry.board_number
        source = self.controller.source_name_of(entry.game_id)
        headline = f"#{board_number} {claim_type}" if board_number and board_number != "-" else claim_type
        self._notify(f"[{source}] {headline}" if source else headline, entry.players, entry.move)

    def _replace_family_rows(self, entry) -> bool:
        """Remove open rows superseded by entry; False if a stronger open row exists."""
        family, level = claim_family(entry.type)
        to_remove = []
        for row in range(self.model.rowCount()):
            data = self.model.item(row, 0).data(Qt.ItemDataRole.UserRole) or {}
            if data.get("game_id") != entry.game_id or self._is_acked(row):
                continue
            try:
                row_type = ClaimType[data.get("type", "")]
            except KeyError:
                continue
            row_family, row_level = claim_family(row_type)
            if row_family != family:
                continue
            if row_level > level:
                return False  # e.g. a 2-fold after a 3-fold of the same game
            to_remove.append(row)
        for row in reversed(to_remove):
            self.model.removeRow(row)
        if to_remove:
            for i in range(self.model.rowCount()):
                self.model.item(i, 0).setData(self.model.rowCount() - i, Qt.ItemDataRole.DisplayRole)
        return True

    def show_context_menu(self, pos):
        """
        Show context menu on right-click to toggle Ack status.
        
        Args:
            pos: Mouse position in table coordinates
        """
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        
        row = index.row()
        context_menu = QMenu(self)

        # Right-click on a row outside the selection acts on that row only
        selected = self.get_selected_rows()
        rows = selected if row in selected else [row]

        if len(rows) == 1:
            has_ack = self._is_acked(row)
            action = context_menu.addAction(tr("Unacknowledge") if has_ack else tr("Acknowledge"))
            action.triggered.connect(lambda: self.toggle_ack(row))
        else:
            context_menu.addAction(tr("Acknowledge selected ({n})").format(n=len(rows))) \
                .triggered.connect(lambda: self.set_ack(rows, True))
            context_menu.addAction(tr("Unacknowledge selected ({n})").format(n=len(rows))) \
                .triggered.connect(lambda: self.set_ack(rows, False))
            context_menu.addAction(tr("Remove selected ({n})").format(n=len(rows))) \
                .triggered.connect(lambda: self.remove_rows(rows))

        # Follow this board live
        data = self.model.item(row, 0).data(Qt.ItemDataRole.UserRole) or {}
        if data.get("game_id"):
            context_menu.addSeparator()
            context_menu.addAction(tr("Add to live board monitor")).triggered.connect(
                lambda: self.controller.add_to_live_monitor(data["game_id"]))

        # Whole round of the clicked row (e.g. after round 1 when round 2 starts)
        rnd = self.row_round(row)
        if rnd:
            round_rows = self.rows_of_round(rnd)
            context_menu.addSeparator()
            context_menu.addAction(tr("Acknowledge all of round {round} ({n})").format(
                round=rnd, n=len(round_rows))).triggered.connect(lambda: self.set_ack(round_rows, True))
            context_menu.addAction(tr("Remove all of round {round} ({n})").format(
                round=rnd, n=len(round_rows))).triggered.connect(
                lambda: self._confirm_remove_round(rnd, round_rows))

        # Show menu at global position
        context_menu.exec(self.table.mapToGlobal(pos))

    # ----------------------------------------------------------- bulk actions
    def _is_acked(self, row: int) -> bool:
        ack_item = self.model.item(row, 6)
        return ack_item is not None and ack_item.text() == "✅"

    def row_round(self, row: int) -> str:
        """Round number of a row ("1" for Round "1.4"), from the game's PGN headers."""
        data = self.model.item(row, 0).data(Qt.ItemDataRole.UserRole) or {}
        parts = (data.get("game_id") or "").split("|")
        if len(parts) < 4:
            return ""
        rnd = parts[3].strip()
        if not rnd or rnd in ("?", "-", "None"):
            return ""
        return rnd.split(".")[0]

    def rows_of_round(self, rnd: str) -> list:
        return [r for r in range(self.model.rowCount()) if self.row_round(r) == rnd]

    def set_ack(self, rows, acknowledged: bool) -> None:
        """Acknowledge / unacknowledge several rows at once."""
        for row in rows:
            ack_item = self.model.item(row, 6)
            if ack_item is None:
                continue
            ack_item.setText("✅" if acknowledged else "")
            self._paint_row(row)

    def remove_rows(self, rows) -> None:
        """Remove rows and mark their claims cleared so they do not come back."""
        for row in sorted(set(rows), reverse=True):
            data = self.model.item(row, 0).data(Qt.ItemDataRole.UserRole)
            game_index = data.get("game_index") if data else None
            if game_index is not None:
                self.claims_store.mark_cleared(game_index)
            self.model.removeRow(row)
        for i in range(self.model.rowCount()):
            self.model.item(i, 0).setData(self.model.rowCount() - i, Qt.ItemDataRole.DisplayRole)
        self.table.clearSelection()

    def _confirm_remove_round(self, rnd: str, rows) -> None:
        from PyQt6.QtWidgets import QMessageBox
        answer = QMessageBox.question(
            self, "Akiba",
            tr("Remove all {n} entries of round {round} from the table?").format(n=len(rows), round=rnd),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.remove_rows(rows)

    def toggle_ack(self, row):
        """
        Toggle Ack status (add/remove ✅) for a specific row.
        Maintains fixed row height by using consistent font sizing.
        
        Args:
            row: Row index to toggle
        """
        ack_item = self.model.item(row, 6)  # Ack column at position 6 (last)
        
        if ack_item.text() == "✅":
            ack_item.setText("")
        else:
            ack_item.setText("✅")
        self._paint_row(row)

    def _paint_row(self, row: int) -> None:
        """Apply the priority colour (or grey when acknowledged) to every cell of a row."""
        from PyQt6.QtGui import QBrush
        first = self.model.item(row, 0)
        if first is None:
            return
        ack_item = self.model.item(row, 6)
        acknowledged = ack_item is not None and ack_item.text() == "✅"
        priority = "acknowledged" if acknowledged else (first.data(PRIORITY_ROLE) or "reminder")
        background, foreground = ROW_COLORS[priority]
        for col in range(self.model.columnCount()):
            item = self.model.item(row, col)
            if item is not None:
                item.setBackground(QBrush(QColor(background)))
                item.setForeground(QBrush(QColor(foreground)))

    @staticmethod
    def _create_standard_item(item: str, idx: int) -> QStandardItem:
        """
        Create a styled QStandardItem for the claims table.
        
        Bold for claim type column, red color for strong claims.
        
        Args:
            item: Text to display
            idx: Column index
            
        Returns:
            Configured QStandardItem
        """
        standard_item = QStandardItem(item)
        if idx == 0 and str(item).isdigit():
            # numeric, so sorting by '#' is 1, 2, ... 10 and not 1, 10, 2
            standard_item.setData(int(item), Qt.ItemDataRole.DisplayRole)
        standard_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Set slightly larger font size for all items to give more vertical space (prevents height changes with emoji)
        font = standard_item.font()
        font.setPixelSize(13)
        standard_item.setFont(font)

        # Bold text for claim type column (now at position 3 after adding Ack column)
        if idx == 3:
            font = standard_item.font()
            font.setBold(True)
            standard_item.setFont(font)

        # Fixed font size for Ack column (position 6) to prevent row height changes
        if idx == 6:
            font = standard_item.font()
            font.setPixelSize(15)  # Slightly larger for emoji visibility
            standard_item.setFont(font)

        return standard_item

    def on_claim_double_clicked(self, index) -> None:
        """
        Handle double-click on a claim row and open Board Viewer
        at the correct game and move.
        
        Args:
            index: QModelIndex of the clicked cell
        """
        # Get data from column 0 of the SAME row (regardless of which column was clicked)
        first_col_index = index.model().index(index.row(), 0, index.parent())
        first_col_item = self.model.itemFromIndex(first_col_index)
        
        if first_col_item is None:
            return

        # Get data from userData (stored as dictionary)
        data = first_col_item.data(Qt.ItemDataRole.UserRole)
        
        if data is None:
            return
        
        game_index = data.get("game_index")
        move_index = data.get("move_index")
        game_id = data.get("game_id")

        if game_index is None or move_index is None:
            return

        # Open Board Viewer for this claim
        self.controller.open_viewer_for_claim(game_index, move_index, game_id)

    def _notify(self, claim_type: str, players: str, move: str) -> None:
        """Send a desktop notification depending on the OS."""
        import platform
        from src.helpers import resource_path
        
        # Check if notifications are enabled in controller settings
        if not getattr(self.controller, 'notifications_enabled', True):
            return  # Skip notification if disabled
        
        if platform.system() == "Darwin":
            self.notification.clearNotifications()
            self.notification.notify(claim_type, players, move)

        elif platform.system() == "Windows":
            from src.notifications.windows_toast import show_toast
            show_toast(self.notification, claim_type, f"{players}\n{move}",
                       resource_path("notification_icon.png"))

    # Columns searched by the filter: claim type, board, players, move
    FILTER_COLUMNS = (2, 3, 4, 5)

    def set_filter(self, text: str) -> None:
        """Show only rows whose type, board, players or move contain the text."""
        self._filter_text = (text or "").strip().lower()
        self.apply_filter()

    def apply_filter(self) -> None:
        from PyQt6.QtCore import QModelIndex
        needle = self._filter_text
        for row in range(self.model.rowCount()):
            visible = True
            if needle:
                visible = any(
                    needle in (self.model.item(row, col).text().lower()
                               if self.model.item(row, col) else "")
                    for col in self.FILTER_COLUMNS
                )
            self.table.setRowHidden(row, QModelIndex(), not visible)

    def remove_rows_by_claim_type(self, claim_type: str, players: str) -> None:
        """
        Remove previous rows for the same players and same or weaker claim type.
        Uses game_index instead of players string to avoid mismatches.
        
        Args:
            claim_type: Type of claim to match
            players: Player names (not used in comparison, kept for API compatibility)
        """
        model = self.model
        rows_to_remove = []

        for row in range(model.rowCount()):
            # Claim type is now at column 3 (was 2 before adding Ack column)
            model_type = model.item(row, 3).text()

            data = model.item(row, 0).data(Qt.ItemDataRole.UserRole)
            model_game_index = data.get("game_index") if data else None

            if model_type == claim_type and model_game_index == self.current_entry_game_index:
                rows_to_remove.append(row)

        for row in reversed(rows_to_remove):
            model.removeRow(row)

        # Re-number remaining rows
        for i in range(model.rowCount()):
            model.item(i, 0).setData(model.rowCount() - i, Qt.ItemDataRole.DisplayRole)

    def clear(self) -> None:
        """Clear all rows from the claims table."""
        # Use removeRow like baseline to preserve model structure and column settings
        while self.model.rowCount() > 0:
            self.model.removeRow(0)

    # Aliases for backward compatibility with controller
    refresh_table = resize_table = lambda self: None  # No-op to avoid layout issues

    def currentIndex(self):
        """Alias for table.currentIndex() - returns current selected index."""
        return self.table.currentIndex()

    def get_selected_rows(self) -> list:
        """
        Get list of selected row indices.
        
        Returns:
            List of unique row indices that are currently selected, sorted in ascending order
        """
        # Use selectionModel to get all selected indexes and extract unique rows
        selection = self.table.selectionModel().selectedRows()
        if not selection:
            return []
        # Use set to ensure uniqueness and sort for consistent order
        return sorted(set([index.row() for index in selection]))

    def clear_ack_rows(self) -> None:
        """
        Clear only acknowledged (✅) rows from the table view.
        
        Does NOT remove from ClaimsStore - these claims may reappear after restart
        if user hasn't explicitly cleared them via Clear Resolved button.
        """
        model = self.model
        rows_to_remove = []
        
        # Find all rows with Ack status (✅ in column 6) that have cleared=True
        for row in range(model.rowCount()):
            ack_item = model.item(row, 6)
            if ack_item and ack_item.text() == "✅":
                data = model.item(row, 0).data(Qt.ItemDataRole.UserRole)
                game_index = data.get("game_index") if data else None
                
                # Only remove rows that have cleared=True (were explicitly cleared via Clear Resolved)
                if game_index is not None:
                    claim = self.claims_store.get_claim_by_game_index(game_index)
                    if claim and claim.cleared:
                        rows_to_remove.append(row)
        
        if not rows_to_remove:
            return  # No acknowledged rows found
        
        # Remove from highest to lowest to maintain valid indices
        for row in reversed(rows_to_remove):
            self.model.removeRow(row)
        
        # Re-number remaining rows
        for i in range(self.model.rowCount()):
            self.model.item(i, 0).setData(self.model.rowCount() - i, Qt.ItemDataRole.DisplayRole)
        
        # Clear selection after removing
        self.table.clearSelection()

    def clear_selected_rows(self) -> None:
        """
        Remove all acknowledged (✅) rows from the table and mark them as cleared.
        These claims will NOT reappear after restart.
        
        Called when user clicks "Clear Resolved" button.
        """
        model = self.model
        rows_to_remove = []
        
        # Find all rows with Ack status (✅ in column 6)
        for row in range(model.rowCount()):
            ack_item = model.item(row, 6)
            if ack_item and ack_item.text() == "✅":
                # Get game_index to mark as cleared in ClaimsStore
                data = model.item(row, 0).data(Qt.ItemDataRole.UserRole)
                game_index = data.get("game_index") if data else None
                
                # Mark claim as cleared in ClaimsStore
                if game_index is not None:
                    self.claims_store.mark_cleared(game_index)
                
                rows_to_remove.append(row)
        
        if not rows_to_remove:
            return  # No acknowledged rows found
        
        # Remove from highest to lowest to maintain valid indices
        for row in reversed(rows_to_remove):
            self.model.removeRow(row)

        # Re-number remaining rows
        for i in range(self.model.rowCount()):
            self.model.item(i, 0).setData(self.model.rowCount() - i, Qt.ItemDataRole.DisplayRole)
        
        # Clear selection
        self.table.clearSelection()
