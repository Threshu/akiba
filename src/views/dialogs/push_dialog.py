from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from src.push.base import (
    EVENT_TYPES, PRIORITY_LABELS, TEMPLATE_HELP, PushSettings,
)


class PushSettingsDialog(QDialog):
    """Settings for phone / Apple Watch notifications (Pushover)."""

    def __init__(self, settings: PushSettings, notifier, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Akiba - Phone notifications")
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.resize(900, 620)
        self._settings = PushSettings.from_dict(settings.to_dict())
        self._notifier = notifier
        self._rows = {}
        self._build()

    # ------------------------------------------------------------------ GUI
    def _build(self) -> None:
        layout = QVBoxLayout(self)

        s = self._settings
        self.enabled_box = QCheckBox("Send notifications to phone / Apple Watch (Pushover)")
        self.enabled_box.setChecked(s.enabled)
        layout.addWidget(self.enabled_box)

        account = QGroupBox("Pushover account")
        form = QFormLayout(account)
        self.user_key = QLineEdit(s.user_key)
        self.user_key.setPlaceholderText("Your User Key (pushover.net dashboard)")
        self.app_token = QLineEdit(s.app_token)
        self.app_token.setPlaceholderText("API Token of the 'Akiba' application")
        self.app_token.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("User key:", self.user_key)
        form.addRow("API token:", self.app_token)

        hint = QLabel("iOS: enable Critical Alerts for High priority in the Pushover "
                      "app settings so alerts break through Silent / Focus mode.")
        hint.setWordWrap(True)
        form.addRow(hint)
        layout.addWidget(account)

        layout.addWidget(QLabel(f"Templates: {TEMPLATE_HELP}"))

        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        for col, header in enumerate(["On", "Event", "Priority", "Title", "Body", ""]):
            grid.addWidget(QLabel(f"<b>{header}</b>"), 0, col)

        for row, (code, (label, _)) in enumerate(EVENT_TYPES.items(), start=1):
            t = s.types[code]
            enabled = QCheckBox()
            enabled.setChecked(t.enabled)
            priority = QComboBox()
            for value, text in PRIORITY_LABELS.items():
                priority.addItem(text, value)
            priority.setCurrentIndex(priority.findData(t.priority))
            title = QLineEdit(t.title_template)
            body = QLineEdit(t.body_template.replace("\n", "\\n"))
            test = QPushButton("Test")
            test.clicked.connect(lambda _=False, c=code: self._on_test(c))

            grid.addWidget(enabled, row, 0)
            grid.addWidget(QLabel(f"{label} ({code})"), row, 1)
            grid.addWidget(priority, row, 2)
            grid.addWidget(title, row, 3)
            grid.addWidget(body, row, 4)
            grid.addWidget(test, row, 5)
            self._rows[code] = (enabled, priority, title, body)
        grid.setColumnStretch(3, 1)
        grid.setColumnStretch(4, 2)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(grid_widget)
        layout.addWidget(scroll, 1)

        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # --------------------------------------------------------------- values
    def get_settings(self) -> PushSettings:
        s = PushSettings.from_dict(self._settings.to_dict())
        s.enabled = self.enabled_box.isChecked()
        s.user_key = self.user_key.text().strip()
        s.app_token = self.app_token.text().strip()
        for code, (enabled, priority, title, body) in self._rows.items():
            t = s.types[code]
            t.enabled = enabled.isChecked()
            t.priority = int(priority.currentData())
            t.title_template = title.text()
            t.body_template = body.text().replace("\\n", "\n")
        return s

    def _on_test(self, code: str) -> None:
        self._set_result(f"Sending {code}...", "#505050")
        self._notifier.send_test(self.get_settings(), code, self._on_test_done)

    @pyqtSlot(str)
    def _on_test_done(self, error: str) -> None:
        if error:
            self._set_result(error, "#aa0000")
        else:
            self._set_result("Sent. Check your phone / watch.", "#006000")

    def _set_result(self, text: str, color: str) -> None:
        self.result_label.setText(text)
        self.result_label.setStyleSheet(f"color: {color};")
