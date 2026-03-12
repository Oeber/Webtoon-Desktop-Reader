from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from gui.common.styles import BUTTON_STYLE, INPUT_STYLE
BTN_STYLE = BUTTON_STYLE + """
    QPushButton:disabled { color: #555; border-color: #222; }
"""

STATUS_COLORS = {
    "Ready": "#888888",
    "Downloading": "#f0a500",
    "Completed": "#4caf50",
    "Failed": "#f44336",
    "Cancelled": "#888888",
}


def format_last_updated(timestamp: int | None) -> str:
    if timestamp is None:
        return "Last updated: Never"
    stamp = datetime.fromtimestamp(int(timestamp)).strftime("%Y-%m-%d %H:%M:%S")
    return f"Last updated: {stamp}"


class SpinnerCircle(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(32, 32)
        self._angle = 0
        self._spinning = True
        self._percent = None

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(30)

    def _tick(self):
        if self._spinning:
            self._angle = (self._angle + 10) % 360
            self.update()

    def set_progress(self, percent: int):
        percent = max(0, min(100, int(percent)))
        self._spinning = False
        self._percent = percent
        self._timer.stop()
        self.update()

    def set_complete(self, percent: int):
        self.set_progress(percent)

    def set_failed(self):
        self._spinning = False
        self._percent = 0
        self._timer.stop()
        self.update()

    def set_spinning(self):
        self._spinning = True
        self._percent = None
        if not self._timer.isActive():
            self._timer.start(30)
        self.update()

    def set_idle(self):
        self._spinning = False
        self._percent = None
        self._timer.stop()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(3, 3, -3, -3)

        if self._spinning:
            painter.setPen(QPen(QColor("#333333"), 3))
            painter.drawEllipse(rect)
            pen = QPen(QColor("#f0a500"), 3)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.drawArc(rect, -self._angle * 16, 270 * 16)
        elif self._percent == 0:
            painter.setPen(QPen(QColor("#333333"), 3))
            painter.drawEllipse(rect)
            painter.setPen(QPen(QColor("#888888"), 2))
            margin = 8
            diagonal_rect = self.rect().adjusted(margin, margin, -margin, -margin)
            painter.drawLine(diagonal_rect.topLeft(), diagonal_rect.bottomRight())
            painter.drawLine(diagonal_rect.topRight(), diagonal_rect.bottomLeft())
        elif self._percent is None:
            painter.setPen(QPen(QColor("#333333"), 3))
            painter.drawEllipse(rect)
            painter.setPen(QPen(QColor("#777777"), 3))
            painter.drawArc(rect, 90 * 16, -360 * 16)
        else:
            painter.setPen(QPen(QColor("#333333"), 3))
            painter.drawEllipse(rect)
            pen = QPen(QColor("#22c55e"), 3)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            span = int(self._percent / 100.0 * 360 * 16)
            painter.drawArc(rect, 90 * 16, -span)

            font = painter.font()
            font.setPixelSize(9)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor("#e0e0e0"))
            painter.drawText(self.rect(), Qt.AlignCenter, f"{self._percent}%")


class DownloadEntry(QFrame):

    def __init__(self, name: str, on_open=None):
        super().__init__()
        self.name = name
        self.on_open = on_open
        self.thumbnail_path = ""
        self.setCursor(Qt.ArrowCursor)
        self.setStyleSheet("""
            QFrame {
                background-color: #1a1a1a;
                border: 1px solid #2a2a2a;
                border-radius: 8px;
            }
            QFrame[clickable="true"] {
                border: 1px solid #3a3a3a;
            }
            QFrame[clickable="true"]:hover {
                background-color: #202020;
                border: 1px solid #4a4a4a;
            }
        """)
        self.setProperty("clickable", False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(52, 78)
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setStyleSheet("""
            QLabel {
                background-color: #161616;
                border: 1px solid #2a2a2a;
                border-radius: 6px;
            }
        """)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(4)

        self.name_label = QLabel(name)
        self.name_label.setStyleSheet(
            "color: #eeeeee; font-size: 13px; background: transparent; border: none; font-weight: 600;"
        )
        self.name_label.setWordWrap(True)

        self.sub_label = QLabel("")
        self.sub_label.setStyleSheet(
            "color: #777777; font-size: 11px; background: transparent; border: none;"
        )
        self.sub_label.hide()

        text_col.addWidget(self.name_label)
        text_col.addWidget(self.sub_label)

        self.spinner = SpinnerCircle()

        self.status_label = QLabel("Downloading")
        self.status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.status_label.setFixedWidth(110)
        self.status_label.setStyleSheet(
            f"color: {STATUS_COLORS['Downloading']}; font-size: 12px; background: transparent; border: none;"
        )

        layout.addWidget(self.thumb_label)
        layout.addLayout(text_col, stretch=1)
        layout.addWidget(self.spinner)
        layout.addWidget(self.status_label)

    def set_progress(self, current: int, total: int):
        total = max(1, total)
        current = max(0, min(current, total))
        percent = int((current / total) * 100)

        self.spinner.set_progress(percent)
        self.status_label.setText(f"{current} / {total}")
        self.status_label.setStyleSheet(
            f"color: {STATUS_COLORS['Downloading']}; font-size: 12px; background: transparent; border: none;"
        )

    def set_status(self, status: str):
        color = STATUS_COLORS.get(status, "#cccccc")
        self.status_label.setText(status)
        self.status_label.setStyleSheet(
            f"color: {color}; font-size: 12px; background: transparent; border: none;"
        )

        clickable = status == "Completed"
        self.setProperty("clickable", clickable)
        self.setCursor(Qt.PointingHandCursor if clickable else Qt.ArrowCursor)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

        if clickable:
            self.sub_label.setText("Click to open details")
            self.sub_label.show()
        else:
            self.sub_label.hide()

        if status == "Completed":
            self.spinner.set_complete(100)
        elif status in ("Failed", "Cancelled"):
            self.spinner.set_failed()
        elif status == "Ready":
            self.spinner.set_idle()
        else:
            self.spinner.set_spinning()

    def set_thumbnail(self, path: str):
        self.thumbnail_path = path or ""
        pixmap = QPixmap(self.thumbnail_path)
        if pixmap.isNull():
            self.thumb_label.clear()
            return

        target_w = self.thumb_label.width()
        target_h = self.thumb_label.height()
        pixmap = pixmap.scaled(
            target_w,
            target_h,
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation
        )
        x = max(0, (pixmap.width() - target_w) // 2)
        y = max(0, (pixmap.height() - target_h) // 2)
        self.thumb_label.setPixmap(pixmap.copy(x, y, target_w, target_h))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.status_label.text() == "Completed":
            if callable(self.on_open):
                self.on_open(self.name)
            event.accept()
            return
        super().mousePressEvent(event)


class CancellableDownloadEntry(DownloadEntry):

    def __init__(self, name: str, on_open=None, on_cancel=None):
        super().__init__(name, on_open=on_open)
        self.on_cancel = on_cancel

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setStyleSheet(BTN_STYLE)
        self.cancel_btn.setFixedWidth(100)
        self.cancel_btn.clicked.connect(self._cancel_requested)
        self.cancel_btn.hide()

        controls = QWidget()
        controls.setStyleSheet("background: transparent; border: none;")
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(8)
        controls_layout.addWidget(self.status_label, alignment=Qt.AlignRight)
        controls_layout.addWidget(self.cancel_btn, alignment=Qt.AlignRight)

        self.layout().removeWidget(self.status_label)
        self.layout().addWidget(controls)

    def set_status(self, status: str):
        super().set_status(status)
        self.cancel_btn.setVisible(status == "Downloading")
        self.cancel_btn.setEnabled(status == "Downloading")

    def _cancel_requested(self):
        if callable(self.on_cancel):
            self.on_cancel(self)


class UpdateEntry(DownloadEntry):

    def __init__(self, webtoon_name: str, source_url: str, last_update_at: int | None, on_update):
        super().__init__(webtoon_name)
        self.source_url = source_url
        self.last_update_at = last_update_at
        self.on_update = on_update
        self.setProperty("clickable", False)
        self.setCursor(Qt.ArrowCursor)

        self.sub_label.setWordWrap(True)
        self._refresh_sub_label()
        self.sub_label.show()

        self.update_btn = QPushButton("Update")
        self.update_btn.setStyleSheet(BTN_STYLE)
        self.update_btn.setFixedWidth(100)
        self.update_btn.clicked.connect(lambda: self.on_update(self))

        controls = QWidget()
        controls.setStyleSheet("background: transparent; border: none;")
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(8)
        controls_layout.addWidget(self.status_label, alignment=Qt.AlignRight)
        controls_layout.addWidget(self.update_btn, alignment=Qt.AlignRight)

        self.layout().removeWidget(self.status_label)
        self.layout().addWidget(controls)

    def set_status(self, status: str):
        super().set_status(status)
        self.setProperty("clickable", False)
        self.setCursor(Qt.ArrowCursor)
        self.style().unpolish(self)
        self.style().polish(self)
        self._refresh_sub_label()

    def set_last_update_at(self, timestamp: int):
        self.last_update_at = int(timestamp)
        self._refresh_sub_label()

    def _refresh_sub_label(self):
        self.sub_label.setText(f"{self.source_url}\n{format_last_updated(self.last_update_at)}")
        self.sub_label.show()
