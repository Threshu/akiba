from __future__ import annotations

from typing import List

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
)

from src.follow import GAME_END, FollowSettings
from src.i18n import tr
from src.push.base import EVENT_TYPES, ERR_PGN
from src.verification.chessmanager import normalize_name

# Alert types offered besides "game finished" (push event codes)
FOLLOW_EVENTS = [code for code in EVENT_TYPES if code not in (ERR_PGN, GAME_END)]


class FollowPlayersDialog(QDialog):
    """Choose followed players (with search) and the events to be notified about."""

    def __init__(self, settings: FollowSettings, games: List, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Akiba - Followed players"))
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.resize(780, 640)
        # Visible check boxes (the app style hides the frame of unchecked ones)
        self.setStyleSheet(
            "QListWidget::indicator, QCheckBox::indicator { width: 14px; height: 14px;"
            " border: 1px solid #8a8a8a; border-radius: 3px; background: white; }"
            "QListWidget::indicator:checked, QCheckBox::indicator:checked {"
            " background: #4caf50; border-color: #388e3c; }")
        self._followed = {normalize_name(p): p for p in settings.players}

        layout = QVBoxLayout(self)
        hint = QLabel(tr("Tick the players you want to follow. Players from the loaded PGN are "
                         "listed; you can also add a name by hand."))
        hint.setWordWrap(True)
        layout.addWidget(hint)

        columns = QHBoxLayout()
        layout.addLayout(columns, 1)

        # --- players with search -------------------------------------------
        left = QVBoxLayout()
        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Search player..."))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_search)
        self.search.returnPressed.connect(self._add_typed)
        add = QPushButton(tr("Add"))
        add.clicked.connect(self._add_typed)
        search_row.addWidget(self.search, 1)
        search_row.addWidget(add)
        left.addLayout(search_row)

        self.players = QListWidget()
        self.players.itemChanged.connect(self._on_item_changed)
        left.addWidget(self.players, 1)
        self.count_label = QLabel()
        left.addWidget(self.count_label)
        columns.addLayout(left, 3)

        # --- events ---------------------------------------------------------
        events_box = QGroupBox(tr("Notify me about:"))
        grid = QGridLayout(events_box)
        grid.setVerticalSpacing(2)
        self.event_boxes = {}
        codes = [GAME_END] + FOLLOW_EVENTS
        for i, code in enumerate(codes):
            label = tr("Game finished") if code == GAME_END else tr(EVENT_TYPES[code][0])
            box = QCheckBox(label)
            box.setChecked(code in settings.events)
            if code == GAME_END:
                font = box.font()
                font.setBold(True)
                box.setFont(font)
            grid.addWidget(box, i, 0)
            self.event_boxes[code] = box
        info = QLabel(tr("Notifications go to the phone (Pushover) and to Windows notifications."))
        info.setWordWrap(True)
        info.setStyleSheet("color: #505050;")
        grid.addWidget(info, len(codes), 0)
        grid.setRowStretch(len(codes) + 1, 1)
        columns.addWidget(events_box, 2)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._fill_players(games)
        self.search.setFocus()

    # ------------------------------------------------------------------ list
    def _fill_players(self, games: List) -> None:
        from src.verification.chessmanager import round_of
        from src.helpers import get_board_number
        seen = {}
        for game in games:
            for colour in ("White", "Black"):
                name = game.headers.get(colour, "").strip()
                if not name or name == "?":
                    continue
                key = normalize_name(name)
                if key and key not in seen:
                    seen[key] = (name, tr("board {board}, round {round}").format(
                        board=get_board_number(game), round=round_of(game) or "?"))
        # followed players that are not in the PGN (added by hand / other round)
        for key, name in self._followed.items():
            seen.setdefault(key, (name, ""))

        self.players.blockSignals(True)
        for key, (name, where) in sorted(seen.items(), key=lambda kv: (kv[0] not in self._followed, kv[0])):
            self._add_item(name, where, key in self._followed)
        self.players.blockSignals(False)
        self._update_count()

    def _add_item(self, name: str, where: str, checked: bool) -> QListWidgetItem:
        item = QListWidgetItem(f"{name}   ({where})" if where else name)
        item.setData(Qt.ItemDataRole.UserRole, name)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self._style_item(item, checked)
        self.players.addItem(item)
        return item

    @staticmethod
    def _style_item(item: QListWidgetItem, followed: bool) -> None:
        font = item.font()
        font.setBold(followed)
        item.setFont(font)

    def _apply_search(self, text: str) -> None:
        needle = normalize_name(text)
        for i in range(self.players.count()):
            item = self.players.item(i)
            name = item.data(Qt.ItemDataRole.UserRole)
            item.setHidden(bool(needle) and needle not in normalize_name(name))

    def _add_typed(self) -> None:
        name = self.search.text().strip()
        if not name:
            return
        key = normalize_name(name)
        for i in range(self.players.count()):
            item = self.players.item(i)
            if normalize_name(item.data(Qt.ItemDataRole.UserRole)) == key:
                item.setCheckState(Qt.CheckState.Checked)
                self.search.clear()
                return
        self._followed[key] = name
        self._add_item(name, "", True)
        self.search.clear()
        self._update_count()

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        name = item.data(Qt.ItemDataRole.UserRole)
        key = normalize_name(name)
        checked = item.checkState() == Qt.CheckState.Checked
        if checked:
            self._followed[key] = name
        else:
            self._followed.pop(key, None)
        self.players.blockSignals(True)
        self._style_item(item, checked)
        self.players.blockSignals(False)
        self._update_count()

    def _update_count(self) -> None:
        self.count_label.setText(tr("Followed: {n}").format(n=len(self._followed)))

    # ---------------------------------------------------------------- values
    def get_settings(self) -> FollowSettings:
        return FollowSettings(
            players=sorted(self._followed.values()),
            events={code for code, box in self.event_boxes.items() if box.isChecked()},
        )
