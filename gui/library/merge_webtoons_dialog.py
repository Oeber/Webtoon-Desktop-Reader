from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from gui.common.strings import t


class MergeWebtoonsDialog(QDialog):
    def __init__(self, names: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("library.merge.choose_title"))
        self.setModal(True)
        self.resize(460, 420)

        ordered_names = [str(name or "").strip() for name in names if str(name or "").strip()]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel(t("library.merge.dialog_title"), self)
        title.setWordWrap(True)
        layout.addWidget(title)

        target_label = QLabel(t("library.merge.choose_prompt"), self)
        target_label.setWordWrap(True)
        layout.addWidget(target_label)

        self.target_combo = QComboBox(self)
        self.target_combo.setEditable(True)
        self.target_combo.addItems(ordered_names)
        if ordered_names:
            self.target_combo.setCurrentText(ordered_names[0])
        layout.addWidget(self.target_combo)

        help_label = QLabel(t("library.merge.order_help"), self)
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        list_row = QHBoxLayout()
        list_row.setSpacing(10)

        self.order_list = QListWidget(self)
        self.order_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.order_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.order_list.setDefaultDropAction(Qt.MoveAction)
        self.order_list.setSortingEnabled(False)
        for name in ordered_names:
            self.order_list.addItem(QListWidgetItem(name))
        if self.order_list.count() > 0:
            self.order_list.setCurrentRow(0)
        list_row.addWidget(self.order_list, 1)

        controls = QVBoxLayout()
        controls.setSpacing(8)

        self.move_up_btn = QPushButton(t("library.merge.move_up"), self)
        self.move_up_btn.clicked.connect(lambda: self._move_current(-1))
        controls.addWidget(self.move_up_btn)

        self.move_down_btn = QPushButton(t("library.merge.move_down"), self)
        self.move_down_btn.clicked.connect(lambda: self._move_current(1))
        controls.addWidget(self.move_down_btn)
        controls.addStretch(1)
        list_row.addLayout(controls)

        layout.addLayout(list_row, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def ordered_names(self) -> list[str]:
        return [
            str(self.order_list.item(index).text() or "").strip()
            for index in range(self.order_list.count())
            if str(self.order_list.item(index).text() or "").strip()
        ]

    def target_name(self) -> str:
        return str(self.target_combo.currentText() or "").strip()

    def _move_current(self, delta: int) -> None:
        row = self.order_list.currentRow()
        if row < 0:
            return
        target_row = row + int(delta)
        if target_row < 0 or target_row >= self.order_list.count():
            return
        item = self.order_list.takeItem(row)
        self.order_list.insertItem(target_row, item)
        self.order_list.setCurrentRow(target_row)
