from __future__ import annotations

import os
import uuid

from PySide6.QtCore import QEvent, QModelIndex, QObject, QPoint, QRect, Qt, QSize, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QImageReader, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from core.app_logging import get_logger
from gui.common.strings import t
from gui.common.styles import (
    BG,
    BORDER,
    BUTTON_STYLE,
    STATUS_LABEL_STYLE,
    SURFACE,
    TEXT,
    TEXT_MUTED_BODY_STYLE,
)


logger = get_logger(__name__)

SUPPORTED_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".avif")
THUMB_W = 126
THUMB_H = 178
THUMB_RADIUS = 12


def _page_sort_key(name: str):
    import re

    match = re.search(r"(\d+(?:\.\d+)?)", str(name or ""))
    if match:
        try:
            return (0, float(match.group(1)), str(name).lower())
        except Exception:
            pass
    return (1, float("inf"), str(name).lower())


def _preview_placeholder() -> QPixmap:
    pixmap = QPixmap(THUMB_W, THUMB_H)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, THUMB_W, THUMB_H, THUMB_RADIUS, THUMB_RADIUS)
    painter.fillPath(path, QColor("#1d1514"))
    painter.setPen(QPen(QColor("#3c2522"), 1))
    painter.drawPath(path)
    painter.end()
    return pixmap


def _rounded_pixmap_from_image(image: QImage) -> QPixmap:
    pixmap = QPixmap.fromImage(image)
    scaled = pixmap.scaled(THUMB_W, THUMB_H, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    x = max(0, (scaled.width() - THUMB_W) // 2)
    y = max(0, (scaled.height() - THUMB_H) // 2)
    cropped = scaled.copy(x, y, THUMB_W, THUMB_H)
    rounded = QPixmap(THUMB_W, THUMB_H)
    rounded.fill(Qt.transparent)
    painter = QPainter(rounded)
    painter.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, THUMB_W, THUMB_H, THUMB_RADIUS, THUMB_RADIUS)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, cropped)
    painter.end()
    return rounded


def _preview_icon(path: str) -> QIcon:
    reader = QImageReader(path)
    size = reader.size()
    if size.isValid() and size.width() > 0 and size.height() > 0:
        scale = max(THUMB_W / size.width(), THUMB_H / size.height())
        reader.setScaledSize(QSize(max(THUMB_W, int(size.width() * scale)), max(THUMB_H, int(size.height() * scale))))
    image = reader.read()
    if image.isNull():
        image = QImage(path)
    if image.isNull():
        return QIcon(_preview_placeholder())
    return QIcon(_rounded_pixmap_from_image(image))


class _ListOrderWatcher(QObject):
    order_changed = Signal()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.ChildRemoved:
            self.order_changed.emit()
        return super().eventFilter(watched, event)


class ChapterEditorPageGrid(QListWidget):
    orderChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._press_row = -1
        self._press_pos: QPoint | None = None
        self._drag_active = False
        self._indicator_row: int | None = None
        self._drag_preview = QLabel(None, Qt.ToolTip | Qt.FramelessWindowHint)
        self._drag_preview.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._drag_preview.setStyleSheet(
            """
            QLabel {
                background: rgba(23, 17, 17, 0.96);
                border: 1px solid rgba(255, 138, 122, 0.65);
                border-radius: 12px;
                padding: 6px;
            }
            """
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            index = self.indexAt(event.position().toPoint())
            self._press_row = int(index.row()) if index.isValid() else -1
            self._press_pos = event.position().toPoint()
            self._drag_active = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._press_row >= 0
            and self._press_pos is not None
            and bool(event.buttons() & Qt.LeftButton)
        ):
            moved = (event.position().toPoint() - self._press_pos).manhattanLength()
            if moved >= QApplication.startDragDistance():
                self._drag_active = True
                self.viewport().setCursor(Qt.ClosedHandCursor)
                self._show_drag_preview(event)
                target_row = self._target_row(event.position().toPoint())
                if target_row is not None:
                    self._set_indicator_row(target_row)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        try:
            if event.button() == Qt.LeftButton and self._drag_active and self._press_row >= 0:
                target_row = self._target_row(event.position().toPoint())
                if target_row is not None:
                    self._move_row(self._press_row, target_row)
                event.accept()
                return
            super().mouseReleaseEvent(event)
        finally:
            self._reset_drag_state()

    def leaveEvent(self, event):
        if not bool(QApplication.mouseButtons() & Qt.LeftButton):
            self._reset_drag_state()
        super().leaveEvent(event)

    def _reset_drag_state(self) -> None:
        self._press_row = -1
        self._press_pos = None
        self._drag_active = False
        self._set_indicator_row(None)
        self._drag_preview.hide()
        self.viewport().unsetCursor()

    def _move_row(self, source_row: int, target_row: int) -> None:
        if source_row < 0 or source_row >= self.count():
            return
        insert_row = max(0, min(int(target_row), self.count()))
        if insert_row > source_row:
            insert_row -= 1
        if insert_row == source_row:
            return
        item = self.takeItem(source_row)
        if item is None:
            return
        self.insertItem(insert_row, item)
        self.orderChanged.emit()

    def _show_drag_preview(self, event) -> None:
        if self._press_row < 0 or self._press_row >= self.count():
            return
        item = self.item(self._press_row)
        if item is None:
            return
        pixmap = item.icon().pixmap(QSize(THUMB_W, THUMB_H))
        if pixmap.isNull():
            return
        self._drag_preview.setPixmap(pixmap)
        self._drag_preview.adjustSize()
        pos = event.globalPosition().toPoint() + QPoint(18, 18)
        self._drag_preview.move(pos)
        self._drag_preview.show()

    def _set_indicator_row(self, row: int | None) -> None:
        normalized = None if row is None else max(0, min(int(row), self.count()))
        if normalized == self._indicator_row:
            return
        self._indicator_row = normalized
        self.viewport().update()

    def _target_row(self, pos: QPoint) -> int | None:
        if self.count() <= 0 or not self.viewport().rect().contains(pos):
            return None
        index = self.indexAt(pos)
        if index.isValid():
            return self._insert_row_for_index(index, pos)
        return self._nearest_insert_row(pos)

    def _nearest_insert_row(self, pos: QPoint) -> int | None:
        best_row = None
        best_distance = None
        for row in range(self.count()):
            item = self.item(row)
            if item is None:
                continue
            rect = self.visualItemRect(item)
            if not rect.isValid():
                continue
            center = rect.center()
            distance = abs(center.x() - pos.x()) + abs(center.y() - pos.y())
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_row = self._insert_row_for_rect(row, rect, pos)
        return best_row

    def _insert_row_for_index(self, index: QModelIndex, pos: QPoint) -> int:
        return self._insert_row_for_rect(int(index.row()), self.visualRect(index), pos)

    @staticmethod
    def _insert_row_for_rect(row: int, rect, pos: QPoint) -> int:
        if not rect.isValid():
            return row
        return row + 1 if pos.x() >= rect.center().x() else row

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._indicator_row is None or self.count() <= 0:
            return
        indicator_rect = self._indicator_rect(self._indicator_row)
        if indicator_rect is None:
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing)
        color = QColor("#ff8a7a")
        painter.fillRect(indicator_rect, color)
        painter.setPen(QPen(QColor("#ffd7cf"), 1))
        painter.drawRoundedRect(indicator_rect.adjusted(0, 0, -1, -1), 2, 2)
        painter.end()

    def _indicator_rect(self, row: int):
        if self.count() <= 0:
            return None
        marker_width = 5
        marker_height = max(48, THUMB_H + 28)
        if row <= 0:
            base_rect = self.visualItemRect(self.item(0))
            if not base_rect.isValid():
                return None
            x = max(6, base_rect.left() - 8)
            y = max(8, base_rect.top() + 6)
            return QRect(x, y, marker_width, marker_height)
        if row >= self.count():
            base_rect = self.visualItemRect(self.item(self.count() - 1))
            if not base_rect.isValid():
                return None
            x = min(self.viewport().width() - marker_width - 6, base_rect.right() + 4)
            y = max(8, base_rect.top() + 6)
            return QRect(x, y, marker_width, marker_height)
        left_rect = self.visualItemRect(self.item(row - 1))
        right_rect = self.visualItemRect(self.item(row))
        if not left_rect.isValid() and not right_rect.isValid():
            return None
        if right_rect.isValid() and left_rect.isValid() and right_rect.top() > left_rect.bottom():
            x = max(6, right_rect.left() - 8)
            y = max(8, right_rect.top() + 6)
            return QRect(x, y, marker_width, marker_height)
        base_rect = right_rect if right_rect.isValid() else left_rect
        x = max(6, base_rect.left() - 8)
        y = max(8, base_rect.top() + 6)
        return QRect(x, y, marker_width, marker_height)


class ChapterEditorDialog(QDialog):
    def __init__(self, webtoon_name: str, chapter_name: str, chapter_path: str, progress_store, scene_bookmark_store, parent=None):
        super().__init__(parent)
        self.webtoon_name = str(webtoon_name or "").strip()
        self.chapter_name = str(chapter_name or "").strip()
        self.chapter_path = str(chapter_path or "").strip()
        self.progress_store = progress_store
        self.scene_bookmark_store = scene_bookmark_store
        self.changed = False
        self._original_paths = self._chapter_image_paths()
        self._deleted_paths: set[str] = set()
        self._watcher = _ListOrderWatcher(self)

        self.setWindowTitle(t("chapter_editor.window", chapter=self.chapter_name))
        self.setModal(True)
        self.resize(980, 760)
        self.setStyleSheet(
            f"""
            QDialog {{
                background: {BG};
                color: {TEXT};
            }}
            QLabel {{
                background: transparent;
                color: {TEXT};
            }}
            QListWidget {{
                background: {SURFACE};
                border: 1px solid {BORDER};
                border-radius: 14px;
                padding: 12px;
                color: {TEXT};
                outline: none;
            }}
            QListWidget::item {{
                margin: 6px;
                padding: 6px;
                border-radius: 12px;
            }}
            QListWidget::item:selected {{
                background: rgba(255, 138, 122, 0.14);
                border: 2px solid rgba(255, 158, 144, 0.72);
            }}
            QListWidget::item:hover:!selected {{
                background: rgba(255, 240, 236, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.10);
            }}
            QListWidget QScrollBar:vertical {{
                background: transparent;
                width: 18px;
                margin: 8px 4px 8px 4px;
                border: none;
                border-radius: 9px;
            }}
            QListWidget QScrollBar::handle:vertical {{
                margin: 1px 2px 1px 2px;
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 0,
                    stop: 0 rgba(255, 138, 122, 0.78),
                    stop: 1 rgba(255, 194, 184, 0.92)
                );
                min-height: 52px;
                border-radius: 7px;
                border: 1px solid rgba(255, 255, 255, 0.08);
            }}
            QListWidget QScrollBar::handle:vertical:hover {{
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 0,
                    stop: 0 rgba(255, 158, 144, 0.92),
                    stop: 1 rgba(255, 222, 216, 0.98)
                );
                border: 1px solid rgba(255, 255, 255, 0.14);
            }}
            QListWidget QScrollBar::handle:vertical:pressed {{
                background: rgba(255, 212, 203, 0.98);
                border: 1px solid rgba(255, 255, 255, 0.2);
            }}
            QListWidget QScrollBar::add-line:vertical,
            QListWidget QScrollBar::sub-line:vertical,
            QListWidget QScrollBar::add-page:vertical,
            QListWidget QScrollBar::sub-page:vertical,
            QListWidget QScrollBar:horizontal,
            QListWidget QScrollBar::handle:horizontal,
            QListWidget QScrollBar::add-line:horizontal,
            QListWidget QScrollBar::sub-line:horizontal,
            QListWidget QScrollBar::add-page:horizontal,
            QListWidget QScrollBar::sub-page:horizontal {{
                background: transparent;
                border: none;
                height: 0px;
                width: 0px;
            }}
            {BUTTON_STYLE}
            """
        )

        self._build_ui()
        self._load_pages()
        self._sync_state()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel(t("chapter_editor.title", chapter=self.chapter_name))
        title.setStyleSheet("color: #fff0ec; font-size: 20px; font-weight: 700;")
        layout.addWidget(title)

        subtitle = QLabel(t("chapter_editor.subtitle"))
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(TEXT_MUTED_BODY_STYLE)
        layout.addWidget(subtitle)

        self.grid = ChapterEditorPageGrid(self)
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setFlow(QListView.LeftToRight)
        self.grid.setWrapping(True)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Snap)
        self.grid.setDragEnabled(False)
        self.grid.setAcceptDrops(False)
        self.grid.setDropIndicatorShown(False)
        self.grid.setDragDropMode(QListWidget.NoDragDrop)
        self.grid.setSelectionMode(QListWidget.ExtendedSelection)
        self.grid.setSpacing(10)
        self.grid.setGridSize(QSize(154, 236))
        self.grid.setIconSize(QSize(THUMB_W, THUMB_H))
        self.grid.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.grid.orderChanged.connect(self._sync_state)
        self.grid.model().rowsMoved.connect(lambda *_args: self._sync_state())
        self.grid.viewport().installEventFilter(self._watcher)
        self._watcher.order_changed.connect(self._sync_state)
        self.grid.itemSelectionChanged.connect(self._sync_state)
        layout.addWidget(self.grid, 1)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(10)

        self.selection_label = QLabel("")
        self.selection_label.setStyleSheet(STATUS_LABEL_STYLE)
        action_row.addWidget(self.selection_label, 1)

        self.delete_btn = QPushButton(t("chapter_editor.delete"))
        self.delete_btn.setStyleSheet(BUTTON_STYLE)
        self.delete_btn.clicked.connect(self._delete_selected_pages)
        action_row.addWidget(self.delete_btn)
        layout.addLayout(action_row)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(STATUS_LABEL_STYLE)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save, parent=self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self._save)
        self.save_btn = buttons.button(QDialogButtonBox.Save)
        self.save_btn.setText(t("chapter_editor.save"))
        cancel_btn = buttons.button(QDialogButtonBox.Cancel)
        cancel_btn.setText(t("chapter_editor.cancel"))
        layout.addWidget(buttons)

    def _chapter_image_paths(self) -> list[str]:
        if not os.path.isdir(self.chapter_path):
            return []
        return sorted(
            (
                entry.path
                for entry in os.scandir(self.chapter_path)
                if entry.is_file() and entry.name.lower().endswith(SUPPORTED_IMAGE_EXTENSIONS)
            ),
            key=lambda value: _page_sort_key(os.path.basename(value)),
        )

    def _load_pages(self) -> None:
        for index, path in enumerate(self._original_paths, start=1):
            item = QListWidgetItem(_preview_icon(path), t("chapter_editor.page_label", number=index, name=os.path.basename(path)))
            item.setData(Qt.UserRole, path)
            item.setTextAlignment(Qt.AlignHCenter)
            self.grid.addItem(item)
        self._refresh_page_labels()

    def _refresh_page_labels(self) -> None:
        for index in range(self.grid.count()):
            item = self.grid.item(index)
            if item is None:
                continue
            item.setText(
                t(
                    "chapter_editor.page_label",
                    number=index + 1,
                    name=os.path.basename(str(item.data(Qt.UserRole) or "")),
                )
            )

    def _selected_paths(self) -> list[str]:
        paths: list[str] = []
        for item in self.grid.selectedItems():
            paths.append(str(item.data(Qt.UserRole) or ""))
        return [path for path in paths if path]

    def _current_paths(self) -> list[str]:
        return [
            str(self.grid.item(index).data(Qt.UserRole) or "")
            for index in range(self.grid.count())
        ]

    def _sync_state(self) -> None:
        self._refresh_page_labels()
        current_paths = self._current_paths()
        changed = current_paths != self._original_paths or bool(self._deleted_paths)
        self.save_btn.setEnabled(changed and bool(current_paths))
        selected_paths = self._selected_paths()
        selected_count = len(selected_paths)
        if selected_count == 1:
            self.selection_label.setText(
                t("chapter_editor.selection_single", name=os.path.basename(selected_paths[0]))
            )
        elif selected_count > 1:
            self.selection_label.setText(
                t("chapter_editor.selection_multi", count=selected_count)
            )
        else:
            self.selection_label.setText(t("chapter_editor.selection_none"))
        self.delete_btn.setEnabled(selected_count > 0 and self.grid.count() > 1)
        self.delete_btn.setText(
            t("chapter_editor.delete_selected", count=selected_count)
            if selected_count > 1
            else t("chapter_editor.delete")
        )
        self.status_label.setText(
            t("chapter_editor.status.changed")
            if changed
            else t("chapter_editor.status.default", count=len(current_paths))
        )

    def _delete_selected_pages(self) -> None:
        selected_items = list(self.grid.selectedItems())
        if not selected_items:
            return
        if self.grid.count() - len(selected_items) <= 0:
            QMessageBox.information(
                self,
                t("chapter_editor.delete_title"),
                t("chapter_editor.delete_last_page"),
            )
            return
        selected_paths = [str(item.data(Qt.UserRole) or "") for item in selected_items if str(item.data(Qt.UserRole) or "")]
        if not selected_paths:
            return
        answer = QMessageBox.question(
            self,
            t("chapter_editor.delete_title"),
            (
                t("chapter_editor.delete_confirm_single", name=os.path.basename(selected_paths[0]))
                if len(selected_paths) == 1
                else t("chapter_editor.delete_confirm_multi", count=len(selected_paths))
            ),
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        rows = sorted((self.grid.row(item) for item in selected_items), reverse=True)
        for path in selected_paths:
            self._deleted_paths.add(path)
        for row in rows:
            removed = self.grid.takeItem(row)
            del removed
        if self.grid.count() > 0:
            self.grid.setCurrentRow(min(rows[-1], self.grid.count() - 1))
        self._sync_state()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self._delete_selected_pages()
            event.accept()
            return
        if event.key() == Qt.Key_A and bool(event.modifiers() & Qt.ControlModifier):
            self.grid.selectAll()
            event.accept()
            return
        super().keyPressEvent(event)

    def _save(self) -> None:
        current_paths = self._current_paths()
        if not current_paths:
            QMessageBox.warning(
                self,
                t("chapter_editor.empty_title"),
                t("chapter_editor.empty_text", chapter=self.chapter_name),
            )
            return
        if current_paths == self._original_paths:
            self.reject()
            return

        old_to_new_index = {
            old_index: current_paths.index(path)
            for old_index, path in enumerate(self._original_paths)
            if path in current_paths
        }
        deleted_old_indexes = {
            old_index
            for old_index, path in enumerate(self._original_paths)
            if path in self._deleted_paths
        }
        try:
            self._rename_files(current_paths)
            page_count = len(self._original_paths)
            if self.progress_store is not None:
                self.progress_store.apply_chapter_page_changes(
                    self.webtoon_name,
                    self.chapter_name,
                    old_to_new_index,
                    page_count=page_count,
                    deleted_old_indexes=deleted_old_indexes,
                    new_page_count=len(current_paths),
                )
            if self.scene_bookmark_store is not None:
                self.scene_bookmark_store.apply_chapter_page_changes(
                    self.webtoon_name,
                    self.chapter_name,
                    old_to_new_index,
                    page_count=page_count,
                    deleted_old_indexes=deleted_old_indexes,
                    new_page_count=len(current_paths),
                )
        except Exception as exc:
            logger.exception("Failed to save chapter page order for %s / %s", self.webtoon_name, self.chapter_name)
            QMessageBox.critical(self, t("chapter_editor.save_failed_title"), str(exc))
            return

        self.changed = True
        self.accept()

    def _rename_files(self, ordered_paths: list[str]) -> None:
        width = max(3, len(str(len(ordered_paths))))
        temp_paths: dict[str, str] = {}
        target_paths: dict[str, str] = {}

        for path in ordered_paths:
            ext = os.path.splitext(path)[1]
            if not ext:
                raise OSError(f"Cannot reorder file without an extension: {path}")

        for index, path in enumerate(ordered_paths, start=1):
            ext = os.path.splitext(path)[1]
            target_paths[path] = os.path.join(self.chapter_path, f"{index:0{width}d}{ext.lower()}")

        for path in self._original_paths:
            temp_path = f"{path}.reorder-{uuid.uuid4().hex}.tmp"
            os.replace(path, temp_path)
            temp_paths[path] = temp_path

        try:
            for path in ordered_paths:
                os.replace(temp_paths[path], target_paths[path])
            for path in self._deleted_paths:
                temp_path = temp_paths.get(path)
                if temp_path and os.path.exists(temp_path):
                    os.remove(temp_path)
        except Exception:
            for original_path, temp_path in temp_paths.items():
                if os.path.exists(temp_path) and not os.path.exists(original_path):
                    try:
                        os.replace(temp_path, original_path)
                    except OSError:
                        logger.warning("Could not roll back temporary page rename %s", temp_path, exc_info=True)
            raise
