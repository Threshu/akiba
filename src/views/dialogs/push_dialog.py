from __future__ import annotations

from html import escape

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from src.push.base import (
    DEFAULT_BODY, DEFAULT_TITLE, ERR_PGN, ERR_PGN_BODY, ERR_PGN_TITLE, EVENT_TYPES,
    PRIORITY_LABELS, SAMPLE_VALUES, TEMPLATE_FIELDS, PushSettings,
    PushTypeSettings, render_message,
)
from src.i18n import tr


def _preview(code: str, title: str, body: str) -> tuple:
    sample = {} if code == ERR_PGN else SAMPLE_VALUES  # no board / players for errors
    message = render_message(code, PushTypeSettings(title_template=title, body_template=body),
                             **sample)
    return message.title, message.body


class TemplateEditorDialog(QDialog):
    """Edit one event's title / body by clicking fields instead of typing {codes}."""

    def __init__(self, code: str, title: str, body: str, parent=None) -> None:
        super().__init__(parent)
        self.code = code
        label = EVENT_TYPES[code][0]
        self.setWindowTitle(tr("Akiba - Message for {label}").format(label=tr(label)))
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.resize(560, 420)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("Click a field to insert it where the cursor is "
                                   "(in Title or Message). Any other text is sent as typed.")))

        fields = QHBoxLayout()
        for key, text in TEMPLATE_FIELDS:
            button = QPushButton(tr(text))
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # keep cursor in the editor
            button.clicked.connect(lambda _=False, k=key: self._insert(k))
            fields.addWidget(button)
        fields.addStretch(1)
        layout.addLayout(fields)

        form = QFormLayout()
        self.title_edit = QLineEdit(title)
        self.body_edit = QPlainTextEdit(body)
        self.body_edit.setFixedHeight(80)
        form.addRow(tr("Title:"), self.title_edit)
        form.addRow(tr("Message:"), self.body_edit)
        layout.addLayout(form)
        self._last_editor = self.body_edit

        preview_box = QGroupBox(tr("Preview on the phone"))
        preview_layout = QVBoxLayout(preview_box)
        self.preview_title = QLabel()
        self.preview_title.setStyleSheet("font-weight: bold;")
        self.preview_body = QLabel()
        self.preview_body.setWordWrap(True)
        preview_layout.addWidget(self.preview_title)
        preview_layout.addWidget(self.preview_body)
        layout.addWidget(preview_box)

        self.apply_all = QCheckBox(tr("Use this message for all events"))
        layout.addWidget(self.apply_all)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel
                                   | QDialogButtonBox.StandardButton.RestoreDefaults)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(self._restore)
        layout.addWidget(buttons)

        self.title_edit.textChanged.connect(self._update_preview)
        self.body_edit.textChanged.connect(self._update_preview)
        QApplication.instance().focusChanged.connect(self._remember_editor)
        self._update_preview()
        self.body_edit.setFocus()

    def _remember_editor(self, _old, new) -> None:
        if new in (self.title_edit, self.body_edit):
            self._last_editor = new

    def _insert(self, key: str) -> None:
        editor = self._last_editor
        text = "{" + key + "}"
        if editor is self.title_edit:
            editor.insert(text)
        else:
            editor.insertPlainText(text)
        editor.setFocus()

    def _restore(self) -> None:
        self.title_edit.setText(ERR_PGN_TITLE if self.code == ERR_PGN else DEFAULT_TITLE)
        self.body_edit.setPlainText(tr(ERR_PGN_BODY) if self.code == ERR_PGN else DEFAULT_BODY)

    def _update_preview(self) -> None:
        title, body = _preview(self.code, self.title_edit.text(), self.body_edit.toPlainText())
        self.preview_title.setText(title)
        self.preview_body.setText(body)

    def values(self) -> tuple:
        return self.title_edit.text(), self.body_edit.toPlainText()


class PushSettingsDialog(QDialog):
    """Settings for phone / Apple Watch notifications (Pushover)."""

    def __init__(self, settings: PushSettings, notifier, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Akiba - Phone notifications"))
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.resize(960, 640)
        self._settings = PushSettings.from_dict(settings.to_dict())
        self._notifier = notifier
        self._rows = {}
        self._templates = {code: (t.title_template, t.body_template)
                           for code, t in self._settings.types.items()}
        self._build()

    # ------------------------------------------------------------------ GUI
    def _build(self) -> None:
        layout = QVBoxLayout(self)

        s = self._settings
        self.enabled_box = QCheckBox(tr("Send notifications to phone / Apple Watch (Pushover)"))
        self.enabled_box.setChecked(s.enabled)
        layout.addWidget(self.enabled_box)

        account = QGroupBox(tr("Pushover account"))
        form = QFormLayout(account)
        self.user_key = QLineEdit(s.user_key)
        self.user_key.setPlaceholderText(tr("Your User Key (pushover.net dashboard)"))
        self.app_token = QLineEdit(s.app_token)
        self.app_token.setPlaceholderText(tr("API Token of the 'Akiba' application"))
        self.app_token.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("User key:", self.user_key)
        form.addRow("API token:", self.app_token)

        hint = QLabel(tr("iOS: enable Critical Alerts for High priority in the Pushover "
                         "app settings so alerts break through Silent / Focus mode."))
        hint.setWordWrap(True)
        form.addRow(hint)
        layout.addWidget(account)

        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        for col, header in enumerate(["On", "Event", "Priority", "Message (preview)", "", ""]):
            grid.addWidget(QLabel(f"<b>{tr(header)}</b>" if header else ""), 0, col)

        for row, (code, (label, _)) in enumerate(EVENT_TYPES.items(), start=1):
            t = s.types[code]
            enabled = QCheckBox()
            enabled.setChecked(t.enabled)
            priority = QComboBox()
            for value, text in PRIORITY_LABELS.items():
                priority.addItem(tr(text), value)
            priority.setCurrentIndex(priority.findData(t.priority))
            preview = QLabel()
            preview.setFrameShape(QFrame.Shape.StyledPanel)
            preview.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            # Shrink instead of pushing Edit / Test out of view; full text in tooltip
            preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            preview.setMinimumWidth(180)
            edit = QPushButton(tr("Edit..."))
            edit.clicked.connect(lambda _=False, c=code: self._on_edit(c))
            test = QPushButton("Test")
            test.clicked.connect(lambda _=False, c=code: self._on_test(c))

            grid.addWidget(enabled, row, 0)
            grid.addWidget(QLabel(tr(label)), row, 1)
            grid.addWidget(priority, row, 2)
            grid.addWidget(preview, row, 3)
            grid.addWidget(edit, row, 4)
            grid.addWidget(test, row, 5)
            self._rows[code] = (enabled, priority, preview)
            self._refresh_preview(code)
        grid.setColumnStretch(3, 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
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

    def _refresh_preview(self, code: str) -> None:
        title, body = _preview(code, *self._templates[code])
        one_line = body.replace("\n", " | ")
        preview = self._rows[code][2]
        preview.setText(f"<b>{escape(title)}</b> &nbsp; {escape(one_line)}")
        preview.setToolTip(f"{title}\n{body}")

    def _on_edit(self, code: str) -> None:
        dialog = TemplateEditorDialog(code, *self._templates[code], parent=self)
        if not dialog.exec():
            return
        title, body = dialog.values()
        codes = list(self._templates) if dialog.apply_all.isChecked() else [code]
        for c in codes:
            self._templates[c] = (title, body)
            self._refresh_preview(c)

    # --------------------------------------------------------------- values
    def get_settings(self) -> PushSettings:
        s = PushSettings.from_dict(self._settings.to_dict())
        s.enabled = self.enabled_box.isChecked()
        s.user_key = self.user_key.text().strip()
        s.app_token = self.app_token.text().strip()
        for code, (enabled, priority, _) in self._rows.items():
            t = s.types[code]
            t.enabled = enabled.isChecked()
            t.priority = int(priority.currentData())
            t.title_template, t.body_template = self._templates[code]
        return s

    def _on_test(self, code: str) -> None:
        self._set_result(tr("Sending {name}...").format(name=tr(EVENT_TYPES[code][0])), "#505050")
        self._notifier.send_test(self.get_settings(), code, self._on_test_done)

    @pyqtSlot(str)
    def _on_test_done(self, error: str) -> None:
        if error:
            self._set_result(error, "#aa0000")
        else:
            self._set_result(tr("Sent. Check your phone / watch."), "#006000")

    def _set_result(self, text: str, color: str) -> None:
        self.result_label.setText(text)
        self.result_label.setStyleSheet(f"color: {color};")
