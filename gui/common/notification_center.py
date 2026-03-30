from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gui.common.styles import (
    ACCENT,
    BG,
    BORDER,
    BUTTON_STYLE,
    PAGE_BG_STYLE,
    SCROLL_AREA_STYLE,
    STATUS_LABEL_STYLE,
    TEXT,
    TEXT_DIM,
    TEXT_MUTED,
)
from gui.common.strings import t


NOTIFICATION_DIALOG_STYLE = f"""
    QDialog {{
        background: {BG};
    }}
    QFrame#notificationRow {{
        background: #171111;
        border: 1px solid {BORDER};
        border-radius: 12px;
    }}
    QLabel#notificationTitle {{
        color: {TEXT};
        font-size: 13px;
        font-weight: 700;
        background: transparent;
    }}
    QLabel#notificationMessage {{
        color: {TEXT_MUTED};
        font-size: 12px;
        background: transparent;
    }}
    QLabel#notificationTime {{
        color: {TEXT_DIM};
        font-size: 11px;
        background: transparent;
    }}
    QLabel#notificationDot {{
        color: {ACCENT};
        font-size: 16px;
        background: transparent;
    }}
"""


class NotificationEntryWidget(QFrame):
    def __init__(self, entry: dict, actions: list[tuple[str, str]], controller, parent=None):
        super().__init__(parent)
        self.entry = dict(entry or {})
        self.actions = list(actions or [])
        self.controller = controller

        self.setObjectName("notificationRow")
        self.setStyleSheet(NOTIFICATION_DIALOG_STYLE)

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(12)

        dot = QLabel("?", self)
        dot.setObjectName("notificationDot")
        dot.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        dot.setVisible(not bool(self.entry.get("is_read", False)))
        root.addWidget(dot, 0, Qt.AlignTop)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(10)

        title = QLabel(str(self.entry.get("title") or ""), self)
        title.setObjectName("notificationTitle")
        title.setWordWrap(True)
        header.addWidget(title, 1)

        time_label = QLabel(_format_timestamp(self.entry.get("created_at")), self)
        time_label.setObjectName("notificationTime")
        time_label.setAlignment(Qt.AlignRight | Qt.AlignTop)
        header.addWidget(time_label, 0, Qt.AlignTop)
        text_col.addLayout(header)

        message = QLabel(str(self.entry.get("message") or ""), self)
        message.setObjectName("notificationMessage")
        message.setWordWrap(True)
        text_col.addWidget(message)

        actions_row = QHBoxLayout()
        actions_row.setContentsMargins(0, 2, 0, 0)
        actions_row.setSpacing(8)
        for action_key, action_label in self.actions:
            btn = QPushButton(action_label, self)
            btn.setStyleSheet(BUTTON_STYLE)
            btn.clicked.connect(lambda _checked=False, key=action_key: self.controller.execute_action(self.entry, key))
            actions_row.addWidget(btn)

        read_btn = QPushButton(
            t("notifications.dialog.unread_button") if bool(self.entry.get("is_read", False)) else t("notifications.dialog.read"),
            self,
        )
        read_btn.setStyleSheet(BUTTON_STYLE)
        read_btn.clicked.connect(lambda: self.controller.toggle_read(self.entry))
        actions_row.addWidget(read_btn)
        actions_row.addStretch()
        text_col.addLayout(actions_row)

        root.addLayout(text_col, 1)


class NotificationCenterDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle(t("notifications.dialog.title"))
        self.setModal(False)
        self.resize(720, 560)
        self.setStyleSheet(PAGE_BG_STYLE + NOTIFICATION_DIALOG_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(10)

        self.title_label = QLabel(t("notifications.dialog.title"), self)
        self.title_label.setStyleSheet("color: #fff0ec; font-size: 20px; font-weight: 700; background: transparent;")
        header.addWidget(self.title_label)

        self.unread_label = QLabel("", self)
        self.unread_label.setStyleSheet("color: #ffd7cf; font-size: 11px; font-weight: 700; background: #221615; border-radius: 10px; padding: 4px 8px;")
        header.addWidget(self.unread_label, 0, Qt.AlignVCenter)
        header.addStretch()

        self.mark_all_btn = QPushButton(t("notifications.dialog.mark_all_read"), self)
        self.mark_all_btn.setStyleSheet(BUTTON_STYLE)
        self.mark_all_btn.clicked.connect(self.controller.mark_all_read)
        header.addWidget(self.mark_all_btn)

        self.clear_read_btn = QPushButton(t("notifications.dialog.clear_read"), self)
        self.clear_read_btn.setStyleSheet(BUTTON_STYLE)
        self.clear_read_btn.clicked.connect(self.controller.clear_read)
        header.addWidget(self.clear_read_btn)

        root.addLayout(header)

        self.meta_label = QLabel("", self)
        self.meta_label.setStyleSheet(STATUS_LABEL_STYLE)
        root.addWidget(self.meta_label)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet(SCROLL_AREA_STYLE)

        self.container = QWidget(self.scroll)
        self.container.setStyleSheet(PAGE_BG_STYLE)
        self.list_layout = QVBoxLayout(self.container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(10)
        self.list_layout.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(self.container)
        root.addWidget(self.scroll, 1)

    def showEvent(self, event):
        super().showEvent(event)
        self.controller.refresh_dialog()

    def refresh_entries(self, entries: list[dict], unread_count: int):
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self.unread_label.setText(t("notifications.dialog.unread", count=int(unread_count)))
        self.meta_label.setText(t("notifications.dialog.recent_count", count=len(entries)))

        if not entries:
            empty = QLabel(t("notifications.dialog.empty"), self.container)
            empty.setStyleSheet("color: #b18b84; font-size: 13px; background: transparent; padding: 18px 6px;")
            self.list_layout.addWidget(empty)
            return

        for entry in entries:
            widget = NotificationEntryWidget(
                entry,
                self.controller.actions_for(entry),
                self.controller,
                parent=self.container,
            )
            self.list_layout.addWidget(widget)
        self.list_layout.addStretch()


def _format_timestamp(timestamp: int | None) -> str:
    try:
        value = int(timestamp or 0)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        return ""
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")


