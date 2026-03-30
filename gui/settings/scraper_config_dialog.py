from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from gui.common.styles import (
    BUTTON_STYLE,
    CHECKBOX_STYLE,
    EDIT_DIALOG_FORM_FRAME_STYLE,
    EDIT_DIALOG_STYLE,
    EDIT_DIALOG_TITLE_STYLE,
    FORM_LABEL_STYLE,
    INPUT_STYLE,
    TEXT_MUTED_BODY_STYLE,
    VERTICAL_SCROLLBAR_STYLE,
)
from gui.common.strings import t


class ScraperConfigDialog(QDialog):

    def __init__(
        self,
        scraper_class,
        current_config: dict | None = None,
        *,
        reset_values: dict | None = None,
        reset_label: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.scraper_class = scraper_class
        self.fields = list(scraper_class.get_source_config_fields())
        self._widgets: dict[str, object] = {}
        self._defaults = scraper_class.normalize_source_config(current_config)
        self._reset_values = (
            scraper_class.normalize_source_config(reset_values)
            if isinstance(reset_values, dict)
            else None
        )
        self._reset_label = str(reset_label or "").strip()

        display_name = str(getattr(scraper_class, "site_display_name", "") or getattr(scraper_class, "site_name", t("scraper_config.source_fallback"))).strip()
        self.setWindowTitle(t("scraper_config.window", display_name=display_name))
        self.setModal(True)
        self.resize(560, 0)
        self.setStyleSheet(EDIT_DIALOG_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(18)

        title = QLabel(t("scraper_config.title", display_name=display_name))
        title.setStyleSheet(EDIT_DIALOG_TITLE_STYLE)
        root.addWidget(title)

        subtitle = QLabel(t("scraper_config.subtitle"))
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(TEXT_MUTED_BODY_STYLE)
        root.addWidget(subtitle)

        form_frame = QWidget()
        form_frame.setStyleSheet(EDIT_DIALOG_FORM_FRAME_STYLE)
        form = QFormLayout(form_frame)
        form.setContentsMargins(16, 16, 16, 16)
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignLeft)
        form.setFormAlignment(Qt.AlignTop)

        for field in self.fields:
            form.addRow(self._form_label(field.label), self._field_container(field))

        root.addWidget(form_frame)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        if self._reset_values is not None and self._reset_label:
            reset_button = QPushButton(self._reset_label)
            reset_button.setStyleSheet(BUTTON_STYLE)
            reset_button.clicked.connect(self._reset_to_defaults)
            buttons.addButton(reset_button, QDialogButtonBox.ResetRole)
        buttons.button(QDialogButtonBox.Save).setText(t("scraper_config.save"))
        buttons.button(QDialogButtonBox.Save).setStyleSheet(BUTTON_STYLE)
        buttons.button(QDialogButtonBox.Cancel).setText(t("scraper_config.cancel"))
        buttons.button(QDialogButtonBox.Cancel).setStyleSheet(BUTTON_STYLE)
        root.addWidget(buttons)

    def _form_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setFixedWidth(110)
        label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        label.setStyleSheet(FORM_LABEL_STYLE)
        return label

    def _field_container(self, field):
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        widget = self._build_field_widget(field)
        self._widgets[field.key] = widget
        layout.addWidget(widget)
        if field.description:
            hint = QLabel(field.description)
            hint.setWordWrap(True)
            hint.setStyleSheet(TEXT_MUTED_BODY_STYLE)
            layout.addWidget(hint)
        return wrapper

    def _build_field_widget(self, field):
        value = self._defaults.get(field.key, field.default)
        if field.control == "boolean":
            widget = QCheckBox()
            widget.setStyleSheet(CHECKBOX_STYLE)
            widget.setChecked(bool(value))
            return widget

        if field.control == "integer":
            widget = QSpinBox()
            widget.setStyleSheet(INPUT_STYLE)
            widget.setRange(int(field.min_value if field.min_value is not None else -999999), int(field.max_value if field.max_value is not None else 999999))
            widget.setValue(int(value if value is not None else field.default or 0))
            return widget

        if field.control == "select":
            widget = QComboBox()
            widget.setStyleSheet(INPUT_STYLE)
            for option in field.options:
                widget.addItem(option.label, option.value)
            index = widget.findData(value)
            widget.setCurrentIndex(max(0, index))
            return widget

        if field.control == "multi_select":
            widget = QListWidget()
            widget.setSelectionMode(QAbstractItemView.NoSelection)
            widget.setStyleSheet(
                """
                QListWidget {
                    background: #181212;
                    border: 1px solid #4b302c;
                    border-radius: 6px;
                    padding: 6px 8px;
                    color: #fff0ec;
                    outline: none;
                }
                QListWidget::item {
                    background: transparent;
                    border: none;
                    padding: 3px 0px;
                }
                QListWidget::item:hover {
                    background: #221615;
                }
                """
                + VERTICAL_SCROLLBAR_STYLE
            )
            for option in field.options:
                item = QListWidgetItem(option.label)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setData(Qt.UserRole, option.value)
                widget.addItem(item)
            widget.setMinimumHeight(max(120, min(220, 32 * max(3, widget.count()))))
            self._set_widget_value(field, widget, value)
            return widget

        widget = QLineEdit()
        widget.setStyleSheet(INPUT_STYLE)
        widget.setPlaceholderText(field.placeholder or "")
        widget.setText(str(value or ""))
        return widget

    def _set_widget_value(self, field, widget, value):
        if field.control == "boolean":
            widget.setChecked(bool(value))
            return
        if field.control == "integer":
            widget.setValue(int(value if value is not None else field.default or 0))
            return
        if field.control == "select":
            index = widget.findData(value)
            widget.setCurrentIndex(max(0, index))
            return
        if field.control == "multi_select":
            selected = {str(item) for item in (value or [])}
            for index in range(widget.count()):
                item = widget.item(index)
                option_value = str(item.data(Qt.UserRole) or "")
                item.setCheckState(Qt.Checked if option_value in selected else Qt.Unchecked)
            return
        widget.setText(str(value or ""))

    def _reset_to_defaults(self):
        if self._reset_values is None:
            return
        for field in self.fields:
            widget = self._widgets.get(field.key)
            if widget is None:
                continue
            self._set_widget_value(field, widget, self._reset_values.get(field.key, field.default))

    def config_values(self) -> dict:
        values = {}
        for field in self.fields:
            widget = self._widgets.get(field.key)
            if field.control == "boolean":
                values[field.key] = bool(widget.isChecked())
            elif field.control == "integer":
                values[field.key] = int(widget.value())
            elif field.control == "select":
                values[field.key] = str(widget.currentData() or "")
            elif field.control == "multi_select":
                selected = []
                for index in range(widget.count()):
                    item = widget.item(index)
                    if item.checkState() == Qt.Checked:
                        selected.append(str(item.data(Qt.UserRole) or ""))
                values[field.key] = selected
            else:
                values[field.key] = str(widget.text() or "").strip()
        return self.scraper_class.normalize_source_config(values)
