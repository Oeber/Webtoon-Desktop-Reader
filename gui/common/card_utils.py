from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFontMetrics, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QLabel, QPushButton, QSizePolicy, QWidget
import qtawesome as qta


def retain_hidden_size(widget: QWidget) -> None:
    policy = widget.sizePolicy()
    policy.setRetainSizeWhenHidden(True)
    widget.setSizePolicy(policy)


class ElidedLabel(QLabel):
    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full_text = text
        self.setText(text)

    def setText(self, text: str):
        self._full_text = text or ""
        self._update_elided_text()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_elided_text()

    def _update_elided_text(self):
        metrics = QFontMetrics(self.font())
        available_width = max(0, self.contentsRect().width())
        if available_width <= 0:
            super().setText(self._full_text)
            return
        super().setText(metrics.elidedText(self._full_text, Qt.ElideRight, available_width))


def rounded_cover_pixmap(path: str, width: int, height: int, radius: int) -> QPixmap:
    pixmap = QPixmap(path)
    if pixmap.isNull():
        return QPixmap()

    pixmap = pixmap.scaled(width, height, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    x = (pixmap.width() - width) // 2
    y = (pixmap.height() - height) // 2
    pixmap = pixmap.copy(x, y, width, height)

    rounded = QPixmap(width, height)
    rounded.fill(Qt.transparent)
    painter = QPainter(rounded)
    painter.setRenderHint(QPainter.Antialiasing)
    path_obj = QPainterPath()
    path_obj.addRoundedRect(0, 0, width, height, radius, radius)
    painter.setClipPath(path_obj)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return rounded


def load_rounded_cover(label: QLabel, path: str, width: int, height: int, radius: int, fallback_text: str = "") -> bool:
    rounded = rounded_cover_pixmap(path, width, height, radius)
    if rounded.isNull():
        label.clear()
        if fallback_text:
            label.setText(fallback_text)
        return False
    label.setPixmap(rounded)
    label.setText("")
    return True


def card_toggle_icon(button: QPushButton, checked: bool, *, checked_icon: str = 'fa5s.check', unchecked_icon: str = 'fa5s.circle', checked_color: str = '#ffffff', unchecked_color: str = '#ffffff', size: int = 12) -> None:
    if checked:
        button.setIcon(qta.icon(checked_icon, color=checked_color))
    else:
        button.setIcon(qta.icon(unchecked_icon, color=unchecked_color))
    button.setIconSize(QSize(size, size))
