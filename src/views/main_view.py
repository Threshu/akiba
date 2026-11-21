from __future__ import annotations

import platform
from datetime import datetime
from typing import Optional, Callable, List

from PyQt6.QtCore import Qt, QSize, QEvent
from PyQt6.QtGui import QPixmap, QMovie, QColor, QAction
from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QPushButton,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QStatusBar,
    QMessageBox,
    QMenu,
    QWidgetAction,
    QCheckBox,
    QSpinBox,
    QSizePolicy,
    QLineEdit,
)

from src.helpers import resource_path, Status
from src.models.events import ClaimType, ClaimsStore

# Import new component classes
from src.views.components.button_box import ButtonBox
from src.views.components.claims_table import ClaimsTable
from src.views.components.menu_builder import MenuBarBuilder
from src.views.dialogs.about_dialog import AboutDialog
from src.i18n import tr


def sources_warning() -> None:
    """Display a warning dialog when no valid PGN sources are found."""
    from PyQt6.QtWidgets import QMessageBox
    
    warning_dialog = QMessageBox()
    warning_dialog.setIcon(QMessageBox.Icon.Warning)
    warning_dialog.setWindowTitle(tr("Warning"))
    warning_dialog.setText(tr("PGN File(s) Not Found"))
    warning_dialog.setInformativeText(tr("Please enter at least one valid PGN source."))
    warning_dialog.exec()

class ChessClaimView(QMainWindow):
    """
    Main application window.

    Displays:
    - claims table (via ClaimsTable component)
    - control buttons (via ButtonBox component)
    - status bar with sources / download / scan status
    """
    ICON_SIZE = 16
    
    __slots__ = [
        "controller",
        "button_box",
        "claims_table_widget",
        "ok_pixmap",
        "error_pixmap",
        "source_label",
        "source_image",
        "download_label",
        "download_image",
        "scan_label",
        "scan_image",
        "engine_label",
        "engine_image",
        "spinner",
        "status_bar",
        "about_dialog",
        "notification",
        "notifications_enabled_action",
        "scoresheet_action",
        "scoresheet_spin",
        "possible_threefold_action",
        "possible_fiftymove_action",
        "possible_incorrect_result_action",
        "timecontrol_action",
        "timecontrol_spin",
        "low_time_action",
        "low_time_spin",
        "flag_fall_action",
        "sofia_action",
        "sofia_spin",
        "claims_store",
        "clear_resolved_button",
    ]

    def __init__(self, controller) -> None:
        super().__init__()
        self.controller = controller
        
        # Use same ClaimsStore as controller - SINGLE SOURCE OF TRUTH
        self.claims_store = controller.claims_store

        # Basic window setup
        self.resize(720, 275)
        self.setWindowTitle("Akiba")
        self.center()

        # Status icons and labels (shared by status bar components)
        self.ok_pixmap = QPixmap(resource_path("check_icon.png"))
        self.error_pixmap = QPixmap(resource_path("error_icon.png"))

        # Initialize notification system
        if platform.system() == "Darwin":
            from src.notifications.mac import Notification
            self.notification = Notification()
        elif platform.system() == "Windows":
            from src.notifications.windows_toast import make_toaster
            self.notification = make_toaster("Akiba")
        else:
            self.notification = None

        # Main widgets - using new components
        self.claims_table_widget = ClaimsTable(controller)
        self.button_box = ButtonBox()
        
        # Alias for backward compatibility with controller
        self.claims_table = self.claims_table_widget

        # Status bar labels and images
        self.source_label = QLabel()
        self.source_image = QLabel()
        self.download_label = QLabel()
        self.download_image = QLabel()
        self.scan_label = QLabel()
        self.scan_image = QLabel()
        
        # Engine status labels (for mismatched result analysis) - hidden by default
        self.engine_label = QLabel(tr("Engine"))
        self.engine_image = QLabel()
        self.engine_label.setVisible(False)
        self.engine_image.setVisible(False)

        # Spinner for scan status
        self.spinner = QMovie(resource_path("spinner.gif"))

        # Clear Resolved button (for status bar)
        self.clear_resolved_button = QPushButton(tr("Clear Resolved"))
        self.clear_resolved_button.setObjectName("clearResolved")

        # Status bar
        self.status_bar = QStatusBar()

        # About dialog
        self.about_dialog = AboutDialog()

        # Menu widgets (settings) - kept in main view as they control controller state
        self._setup_menu_widgets()

        # Build the UI
        self._build_ui()
        
        # Connect buttons to controller callbacks
        self.button_box.set_scan_button_callback(self.controller.on_scan_button_clicked)
        self.button_box.set_stop_button_callback(self.controller.on_stop_button_clicked)
        self.button_box.set_board_button_callback(self.controller.on_board_viewer_clicked)
        
        self.create_status_bar()
        self.create_menu()

    def _setup_menu_widgets(self) -> None:
        """Setup menu widgets for settings."""
        
        # Notification system toggle
        self.notifications_enabled_action = QCheckBox(tr("Enable Windows/Mac notifications"))
        self.notifications_enabled_action.setChecked(True)
        
        # Scoresheet reminder
        self.scoresheet_action = QCheckBox(tr("Scoresheet change after move"))
        self.scoresheet_action.setChecked(False)
        self.scoresheet_spin = QSpinBox()
        self.scoresheet_spin.setRange(1, 200)
        self.scoresheet_spin.setValue(55)
        self.scoresheet_spin.setEnabled(False)

        # Time control reminder
        self.timecontrol_action = QCheckBox(tr("Time control after move"))
        self.timecontrol_action.setChecked(False)
        self.timecontrol_spin = QSpinBox()
        self.timecontrol_spin.setRange(1, 200)
        self.timecontrol_spin.setValue(38)
        self.timecontrol_spin.setEnabled(False)

        # Possible Threefold Reminder
        self.possible_threefold_action = QCheckBox(tr("Possible threefold (2 fold)"))
        self.possible_threefold_action.setChecked(False)

        # Possible 50-move Reminder
        self.possible_fiftymove_action = QCheckBox(tr("Possible 50-move (45 moves)"))
        self.possible_fiftymove_action.setChecked(False)

        # Possible Incorrect Result Reminder
        self.possible_incorrect_result_action = QCheckBox(tr("Possible incorrect result"))
        self.possible_incorrect_result_action.setChecked(False)

        # Low Time Reminder
        self.low_time_action = QCheckBox(tr("Low time - if less second than"))
        self.low_time_action.setChecked(False)
        self.low_time_spin = QSpinBox()
        self.low_time_spin.setRange(1, 600)
        self.low_time_spin.setValue(120)
        self.low_time_spin.setEnabled(False)

        # Flag Fall Reminder
        self.flag_fall_action = QCheckBox(tr("Flag fall (00:00:00)"))
        self.flag_fall_action.setChecked(False)

        # Illegal Move Reminder
        self.illegal_move_action = QCheckBox(tr("First illegal move"))
        self.illegal_move_action.setChecked(False)

        # Draw agreement (Sofia rule)
        self.sofia_action = QCheckBox(tr("Draw before move completed (Sofia rule)"))
        self.sofia_action.setChecked(False)
        self.sofia_spin = QSpinBox()
        self.sofia_spin.setRange(1, 200)
        self.sofia_spin.setValue(30)
        self.sofia_spin.setEnabled(False)

    def _build_ui(self) -> None:
        """Build the main UI layout."""
        # Main layout - use claims_table_widget.table from component
        container_layout = QVBoxLayout()
        container_layout.setSpacing(0)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText(tr("Filter by player, board or claim..."))
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self.claims_table_widget.set_filter)

        # Games in progress / total moves, next to the filter (updated after each PGN reload)
        self.games_label = QLabel()
        self.games_label.setStyleSheet("color: #333333; font-weight: 600; padding-left: 10px;")
        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.addWidget(self.filter_edit, 1)
        filter_row.addWidget(self.games_label)
        container_layout.addLayout(filter_row)
        container_layout.addSpacing(4)

        container_layout.addWidget(self.claims_table_widget.table)
        container_layout.addWidget(self.button_box)

        container_widget = QWidget()
        container_widget.setLayout(container_layout)

        self.setCentralWidget(container_widget)
        self.setStatusBar(self.status_bar)
        
        # Adjust window width to fit table properly (from backup: create_claims_table does this)
        self.resize(860, self.height())

    def center(self) -> None:
        """Center the window on the screen."""
        from PyQt6.QtGui import QGuiApplication
        
        # Get primary screen geometry (PyQt6 replacement for QDesktopWidget)
        screen = QGuiApplication.primaryScreen().geometry()
        size = self.geometry()
        self.move(
            int((screen.width() - size.width()) / 2),
            int((screen.height() - size.height()) / 2),
        )

    VERIFY_ALERT_STYLE = (
        "QPushButton { background-color: #d92d20; color: white; border: 1px solid #b42318;"
        " font-size: 13px; font-weight: 700; padding: 6px 14px; border-radius: 4px; }"
        "QPushButton:hover { background-color: #b42318; }"
    )

    def set_verification_state(self, report) -> None:
        """Red button with a count when results differ from ChessManager, normal otherwise."""
        button = self.verify_results_button
        count = len(report.discrepancies)
        if count:
            button.setText("⚠ " + tr("Verify results") + f" ({count})")
            button.setStyleSheet(self.VERIFY_ALERT_STYLE)
            button.setToolTip(tr("Results differ from ChessManager - click to see"))
        else:
            button.setText(tr("Verify results"))
            button.setStyleSheet("")
            if report.checked and not report.errors:
                button.setToolTip(tr("Last check: {time} - all results match").format(time=report.finished_at))
            else:
                button.setToolTip(tr("Compare transmission results with ChessManager"))

    def set_games_summary(self, games) -> None:
        """'In progress: 18/36 · Moves: 1234' with a per-source breakdown in the tooltip."""
        total = len(games)
        ongoing = sum(1 for g in games if g.headers.get("Result", "*") == "*")
        moves = sum((len(list(g.mainline_moves())) + 1) // 2 for g in games)
        self.games_label.setText(tr("In progress: {ongoing}/{total} · Moves: {moves}").format(
            ongoing=ongoing, total=total, moves=moves))
        per_source = {}
        for g in games:
            name = self.controller.source_name_of(self.controller.make_game_id(g)) or tr("Other")
            stats = per_source.setdefault(name, [0, 0, 0])
            stats[0] += 1
            stats[1] += g.headers.get("Result", "*") == "*"
            stats[2] += (len(list(g.mainline_moves())) + 1) // 2
        lines = [tr("{name}: {ongoing}/{total} in progress, {moves} moves").format(
            name=n, total=s[0], ongoing=s[1], moves=s[2]) for n, s in sorted(per_source.items())]
        self.games_label.setToolTip("\n".join(lines))

    def set_gui(self) -> None:
        """Initialize all GUI components (wrapper for backward compatibility)."""
        # All initialization is already done in __init__; restore last size / position
        self.restore_window_geometry()

    @staticmethod
    def _geometry_path() -> str:
        import os
        from src.helpers import get_appdata_path
        return os.path.join(get_appdata_path(), "window_geometry.dat")

    def restore_window_geometry(self) -> None:
        """Restore window size and position saved on the last clean close."""
        from PyQt6.QtCore import QByteArray
        try:
            with open(self._geometry_path(), "rb") as f:
                self.restoreGeometry(QByteArray(f.read()))
        except OSError:
            pass  # first start: keep the default centred window

    def save_window_geometry(self) -> None:
        try:
            with open(self._geometry_path(), "wb") as f:
                f.write(bytes(self.saveGeometry()))
        except OSError:
            pass

    def create_menu(self) -> None:
        """Create the main menu bar using MenuBarBuilder."""
        builder = MenuBarBuilder(self)
        builder.build_menu()

        # Connect signals to update settings in controller
        for claim_type, checkbox in self.claim_type_actions.items():
            checkbox.stateChanged.connect(
                lambda state, t=claim_type: self.controller.set_claim_enabled(t, bool(state))
            )
        self.fifty_moves_spin.valueChanged.connect(self.controller.set_fifty_moves_threshold)

        self.notifications_enabled_action.stateChanged.connect(
            lambda state: (setattr(self.controller, "notifications_enabled", bool(state)), self.controller.save_options())
        )
        
        self.scoresheet_action.stateChanged.connect(self._update_scoresheet_settings)
        self.scoresheet_spin.valueChanged.connect(self._update_scoresheet_settings)
        
        self.timecontrol_action.stateChanged.connect(self._update_timecontrol_settings)
        self.timecontrol_spin.valueChanged.connect(self._update_timecontrol_settings)
        
        self.possible_threefold_action.stateChanged.connect(
            lambda state: (setattr(self.controller, "possible_threefold_enabled", bool(state)), self.controller.save_options())
        )

        self.possible_fiftymove_action.stateChanged.connect(
            lambda state: (setattr(self.controller, "possible_fiftymove_enabled", bool(state)), self.controller.save_options())
        )

        # Possible incorrect result reminder connection
        self.possible_incorrect_result_action.stateChanged.connect(
            lambda state: (setattr(self.controller, "possible_incorrect_result_enabled", bool(state)), self.controller.save_options())
        )

        self.low_time_action.stateChanged.connect(self._update_low_time_settings)
        self.low_time_spin.valueChanged.connect(self._update_low_time_settings)

        self.flag_fall_action.stateChanged.connect(
            lambda state: (setattr(self.controller, "flag_fall_reminder_enabled", bool(state)), self.controller.save_options())
        )

        # Illegal move reminder connection
        self.illegal_move_action.stateChanged.connect(
            lambda state: (setattr(self.controller, "illegal_move_reminder_enabled", bool(state)), self.controller.save_options())
        )

        # Sofia rule reminder connection
        self.sofia_action.stateChanged.connect(self._update_sofia_settings)
        self.sofia_spin.valueChanged.connect(self._update_sofia_settings)

    def _update_scoresheet_settings(self) -> None:
        """Update controller settings from menu widgets."""
        enabled = self.scoresheet_action.isChecked()
        self.scoresheet_spin.setEnabled(enabled)

        self.controller.scoresheet_reminder_enabled = enabled
        self.controller.scoresheet_threshold = self.scoresheet_spin.value()
        
        # Save options to persist settings
        self.controller.save_options()
        
    def _update_timecontrol_settings(self) -> None:
        enabled = self.timecontrol_action.isChecked()
        self.timecontrol_spin.setEnabled(enabled)
        self.controller.timecontrol_reminder_enabled = enabled
        self.controller.timecontrol_threshold = self.timecontrol_spin.value()
        
        # Save options to persist settings
        self.controller.save_options()

    def _update_low_time_settings(self):
        enabled = self.low_time_action.isChecked()
        self.low_time_spin.setEnabled(enabled)
        self.controller.low_time_reminder_enabled = enabled
        self.controller.low_time_threshold_seconds = self.low_time_spin.value()
        
        # Save options to persist settings
        self.controller.save_options()
    
    def _update_sofia_settings(self) -> None:
        """Update controller settings from Sofia rule menu widgets."""
        enabled = self.sofia_action.isChecked()
        self.sofia_spin.setEnabled(enabled)

        self.controller.sofia_rule_enabled = enabled
        self.controller.sofia_rule_threshold = self.sofia_spin.value()
        
        # Save options to persist settings
        self.controller.save_options()

    def create_status_bar(self) -> None:
        """Create and configure the status bar."""
        sources_button = QPushButton(tr("Add Sources"))
        sources_button.setObjectName("sources")
        sources_button.clicked.connect(self.controller.on_sources_button_clicked)
        
        # Set fixed width for Add Sources button (based on Clear Resolved text length)
        sources_button.setMinimumWidth(125)

        self.source_image.setObjectName("source-image")
        self.download_image.setObjectName("download-image")
        self.scan_image.setObjectName("scan-image")

        self.spinner.setScaledSize(QSize(self.ICON_SIZE, self.ICON_SIZE))
        self.spinner.start()

        self.status_bar.setSizeGripEnabled(False)
        
        # Create a layout for the entire status bar content
        status_layout = QHBoxLayout()
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(10)
        
        # Add sources button on the far left side (fixed width)
        sources_button.setMinimumWidth(125)
        status_layout.addWidget(sources_button)
        
        # Create center container for Source and Scan labels with spacing from sources
        center_container = QWidget()
        center_layout = QHBoxLayout()
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(10)  # uniform spacing between all elements
        
        # Add widgets with consistent spacing
        center_layout.addWidget(self.source_label)
        center_layout.addWidget(self.source_image)
        center_layout.addWidget(self.download_label)
        center_layout.addWidget(self.download_image)
        center_layout.addWidget(self.scan_label)
        center_layout.addWidget(self.scan_image)
        center_layout.addWidget(self.engine_label)
        center_layout.addWidget(self.engine_image)

        
        center_container.setLayout(center_layout)
        status_layout.addWidget(center_container)
        
        # Add stretchable space to push everything left
        status_layout.addStretch()
        
        # Verify results against ChessManager
        self.verify_results_button = QPushButton(tr("Verify results"))
        self.verify_results_button.setObjectName("clearResolved")  # same look as Clear Resolved
        self.verify_results_button.setMinimumWidth(125)
        self.verify_results_button.setToolTip(tr("Compare transmission results with ChessManager"))
        self.verify_results_button.clicked.connect(self.controller.on_verify_results_clicked)
        status_layout.addWidget(self.verify_results_button)
        status_layout.addSpacing(8)

        # Add clear resolved button on the right side (same width as Add Sources)
        self.clear_resolved_button.setMinimumWidth(125)
        self.clear_resolved_button.clicked.connect(self.controller.on_clear_resolved_clicked)
        status_layout.addWidget(self.clear_resolved_button)
        
        # Create a widget to hold the layout and set it as the status bar
        status_widget = QWidget()
        status_widget.setLayout(status_layout)
        self.status_bar.addPermanentWidget(status_widget, stretch=1)
        
        self.status_bar.setContentsMargins(10, 5, 9, 5)

    def add_item_to_table(self, entry) -> None:
        """Add a claim to the table using ClaimsTable component."""
        if not self.claims_table_widget.add_claim(entry):
            return  # superseded by a stronger open entry of the same game
        if not self.controller.should_notify(entry):
            return
        self.claims_table_widget.notify_desktop(entry)
        self.controller.push.notify_claim(entry, self.controller.follow,
                                          source=self.controller.source_name_of(entry.game_id))

    def clear_claims_table(self) -> None:
        """Clear all rows from the claims table using component method."""
        self.claims_table_widget.clear()

    def clear_ack_rows(self) -> None:
        """Clear only acknowledged (✅) rows from the claims table."""
        self.claims_table_widget.clear_ack_rows()

    # Alias for backward compatibility with controller
    clear_table = clear_claims_table
    
    def refresh_table(self) -> None:
        """Refresh the claims table (alias for resize_columns)."""
        self.claims_table_widget.refresh_table()

    def set_sources_status(self, status: Status, valid_sources: Optional[List] = None) -> None:
        """
        Update the sources status in the status bar.

        Shows a tooltip listing all valid sources.
        """
        if valid_sources is None:
            valid_sources = []

        count = len(valid_sources)
        self.source_label.setText(tr("Sources ({count})").format(count=count))

        try:
            text = ""
            for idx, source in enumerate(valid_sources):
                text += f"{idx + 1}) {source.get_value()}"
                if idx != len(valid_sources) - 1:
                    text += "\n"
            self.source_label.setToolTip(text)
        except TypeError:
            # In case valid_sources is not iterable or contains unexpected types
            pass

        self.set_pixmap(self.source_image, status)

    def set_download_status(self, status: Status) -> None:
        """Update the download status in the status bar."""
        timestamp = str(datetime.now().strftime("%H:%M:%S"))
        self.download_label.setText(tr("{timestamp} Download:").format(timestamp=timestamp))

        self.set_pixmap(self.download_image, status)

        if status == Status.STOP:
            self.download_image.clear()
            self.download_label.clear()

    def set_scan_status(self, status: Status) -> None:
        """Update the scan status in the status bar."""
        timestamp = str(datetime.now().strftime("%H:%M:%S"))
        self.scan_label.setText(tr("Scan ({timestamp}):").format(timestamp=timestamp))
        self.set_pixmap(self.scan_image, status)

        if status == Status.ACTIVE:
            self.scan_image.clear()
            self.scan_image.setMovie(self.spinner)
        elif status == Status.STOP:
            self.scan_label.clear()
            self.scan_image.clear()

    def change_scan_button_text(self, status: Status) -> None:
        """Change the scan button text depending on the scan status."""
        if status == Status.ACTIVE:
            self.button_box.scan_button.setText(tr("Scanning PGN..."))
        elif status == Status.STOP:
            self.button_box.scan_button.setText(tr("Start Scan"))
        elif status == Status.WAIT:
            self.button_box.scan_button.setText(tr("Please Wait"))

    def set_pixmap(self, image: QLabel, status: Status) -> None:
        """Set the appropriate status icon."""
        if status in (Status.OK, Status.WAIT):
            image.setPixmap(
                self.ok_pixmap.scaled(
                    self.ICON_SIZE,
                    self.ICON_SIZE,
                    transformMode=Qt.TransformationMode.SmoothTransformation,
                )
            )
        elif status == Status.ERROR:
            image.setPixmap(
                self.error_pixmap.scaled(
                    self.ICON_SIZE,
                    self.ICON_SIZE,
                    transformMode=Qt.TransformationMode.SmoothTransformation,
                )
            )

    def enable_buttons(self) -> None:
        """Enable main control buttons."""
        self.button_box.scan_button.setEnabled(True)
        self.button_box.stop_button.setEnabled(True)

    def disable_buttons(self) -> None:
        """Disable main control buttons."""
        self.button_box.scan_button.setEnabled(False)
        self.button_box.stop_button.setEnabled(False)

    def enable_status_bar(self) -> None:
        """Show download and scan status messages in the status bar."""
        self.download_label.setVisible(True)
        self.scan_label.setVisible(True)
        self.download_image.setVisible(True)
        self.scan_image.setVisible(True)

    def disable_status_bar(self) -> None:
        """Hide download and scan status messages from the status bar."""
        self.download_label.setVisible(False)
        self.download_image.setVisible(False)
        self.scan_label.setVisible(False)
        self.scan_image.setVisible(False)

    def set_engine_status(self, enabled: bool, last_analysis_time: Optional[str] = None) -> None:
        """Update the engine status in the status bar."""
        if enabled:
            # Show engine status when analysis is active
            self.engine_label.setVisible(True)
            self.engine_image.setVisible(True)
            
            if last_analysis_time:
                self.engine_image.setPixmap(
                    self.ok_pixmap.scaled(
                        self.ICON_SIZE,
                        self.ICON_SIZE,
                        transformMode=Qt.TransformationMode.SmoothTransformation,
                    )
                )
                self.engine_label.setText(tr("Engine ({time})").format(time=last_analysis_time))
            else:
                self.engine_image.setPixmap(
                    self.ok_pixmap.scaled(
                        self.ICON_SIZE,
                        self.ICON_SIZE,
                        transformMode=Qt.TransformationMode.SmoothTransformation,
                    )
                )
                self.engine_label.setText(tr("Engine (waiting)"))
        else:
            # Hide only the tick icon, keep label with last timestamp visible
            self.engine_image.setVisible(False)

    def closeEvent(self, event: QEvent) -> None:
        """
        Reimplement the close event to warn if scanning is in progress.
        """
        try:
            if self.controller.scan_worker and self.controller.scan_worker.isRunning():
                exit_dialog = QMessageBox()
                exit_dialog.setWindowTitle(tr("Warning"))
                exit_dialog.setText(tr("Scanning in Progress"))
                exit_dialog.setInformativeText(tr("Do you want to quit?"))
                exit_dialog.setIcon(QMessageBox.Icon.Warning)
                exit_dialog.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
                exit_dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)

                # --- button width adjustment ---
                yes_button = exit_dialog.button(QMessageBox.StandardButton.Yes)
                cancel_button = exit_dialog.button(QMessageBox.StandardButton.Cancel)
                yes_button.setMinimumWidth(100)
                cancel_button.setMinimumWidth(100)
                # --------------------------------

                replay = exit_dialog.exec()

                if replay == QMessageBox.StandardButton.Yes:
                    event.accept()
                else:
                    event.ignore()
            else:
                event.accept()
        except Exception:
            event.accept()
        if event.isAccepted():
            self.save_window_geometry()

    def load_about_dialog(self) -> None:
        """Display the About dialog."""
        self.about_dialog.set_gui()
        self.about_dialog.show()
    def open_user_manual(self) -> None:
        """Open the user documentation file in an internal browser window."""
        import os
        from src.helpers import resource_path
        
        # Get the path to USER_DOCUMENTATION.md using resource_path (works for both dev and frozen mode)
        from src.i18n import current_language
        manual_path = resource_path("USER_DOCUMENTATION.md")
        if current_language() == "pl":
            polish_manual = resource_path("DOKUMENTACJA_UZYTKOWNIKA.md")
            if os.path.exists(polish_manual):
                manual_path = polish_manual
        
        if not os.path.exists(manual_path):
            QMessageBox.warning(
                self,
                tr("User Manual Not Found"),
                "The user documentation file could not be found."
            )
            return
        
        # Read the markdown file
        try:
            with open(manual_path, 'r', encoding='utf-8') as f:
                markdown_content = f.read()
        except Exception as e:
            QMessageBox.warning(
                self,
                tr("Error Reading Manual"),
                tr("Could not read the user documentation:") + f"\n\n{str(e)}"
            )
            return
        
        # Create a new window for the manual
        from PyQt6.QtWidgets import QMainWindow, QTextBrowser, QVBoxLayout, QWidget
        from PyQt6.QtCore import Qt
        
        self.manual_window = QMainWindow()
        self.manual_window.setWindowTitle(tr("Akiba - User Manual"))
        self.manual_window.setMinimumSize(800, 600)
        
        # Create central widget and layout
        central_widget = QWidget()
        self.manual_window.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        
        # Create QTextBrowser with markdown support
        self.manual_browser = QTextBrowser()
        # Convert markdown to rich text (QTextBrowser has limited markdown support, but basic formatting works)
        self.manual_browser.setMarkdown(markdown_content)
        self.manual_browser.setFontFamily("Segoe UI")
        self.manual_browser.setFontPointSize(10)
        layout.addWidget(self.manual_browser)
        
        # Show the window
        self.manual_window.show()
    def notify(self, claim_type: ClaimType, players: str, move: str) -> None:
        """Send a desktop notification depending on the OS."""
        # Check if notifications are enabled in controller settings
        if not getattr(self.controller, 'notifications_enabled', True):
            return

        if self.notification is None:
            return

        if platform.system() == "Darwin":
            from src.notifications.mac import Notification
            if isinstance(self.notification, Notification):
                self.notification.clearNotifications()
                self.notification.notify(claim_type.value, players, move)

        elif platform.system() == "Windows":
            from src.notifications.windows_toast import show_toast
            show_toast(self.notification, claim_type.value, f"{players}\n{move}",
                       resource_path("notification_icon.png"))

