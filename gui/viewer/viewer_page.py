import os
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QScrollArea,
    QPushButton, QComboBox, QHBoxLayout, QDialog, QApplication
)
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen
from PySide6.QtCore import Qt, QPoint, QEvent, QTimer, Signal, QObject, QRect

from progress_store import get_instance as get_progress_store

FILMSTRIP_W   = 25
IMAGE_STRIP_W = 50
PREVIEW_W     = FILMSTRIP_W + IMAGE_STRIP_W

TILE_GAP      = 2
TILE_PADDING  = 2
TILE_MIN_H    = 14
TILE_MAX_H    = 120

LAZY_WINDOW   = 2000    # px above/below viewport to keep loaded
BATCH_MS      = 50      # flush decoded images to UI every N ms
NUM_WORKERS   = 8


class ContinueDialog(QDialog):

    def __init__(self, chapter: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Resume reading?")
        self.setModal(True)
        self.setFixedWidth(360)
        self.setStyleSheet("""
            QDialog { background: #1e1e1e; color: #e0e0e0; }
            QLabel  { color: #e0e0e0; font-size: 13px; background: transparent; }
            QPushButton { padding: 8px 20px; border-radius: 6px;
                          font-size: 13px; font-weight: 600; border: none; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(16)
        msg = QLabel(
            f"You have saved progress in <b>{chapter}</b>.<br>"
            "Would you like to continue from where you left off?"
        )
        msg.setWordWrap(True)
        msg.setTextFormat(Qt.RichText)
        layout.addWidget(msg)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        restart_btn = QPushButton("Start over")
        restart_btn.setStyleSheet("QPushButton{background:#2e2e2e;color:#ccc;} QPushButton:hover{background:#3a3a3a;}")
        restart_btn.clicked.connect(self.reject)
        continue_btn = QPushButton("Continue")
        continue_btn.setStyleSheet("QPushButton{background:#2979ff;color:#fff;} QPushButton:hover{background:#448aff;}")
        continue_btn.clicked.connect(self.accept)
        btn_layout.addWidget(restart_btn)
        btn_layout.addWidget(continue_btn)
        layout.addLayout(btn_layout)


class ImageLoader(QObject):
    image_ready = Signal(int, QPixmap)

    def __init__(self):
        super().__init__()
        self.executor   = ThreadPoolExecutor(max_workers=NUM_WORKERS)
        self._cancelled = False
        self._queued    = set()   # track which indices are in-flight

    def cancel(self):
        self._cancelled = True
        self._queued.clear()

    def reset(self):
        self._cancelled = False

    def load(self, index: int, path: str, width: int):
        if index in self._queued:
            return
        self._queued.add(index)
        self.executor.submit(self._load_task, index, path, width)

    def _load_task(self, index: int, path: str, width: int):
        if self._cancelled:
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return
        scaled = pixmap.scaledToWidth(width, Qt.SmoothTransformation)
        if not self._cancelled:
            self.image_ready.emit(index, scaled)


class ChapterPreview(QWidget):

    def __init__(self, scroll_area: QScrollArea, parent=None):
        super().__init__(parent)
        self.scroll_area  = scroll_area
        self.image_labels = []
        self.setFixedWidth(PREVIEW_W)
        self.setCursor(Qt.PointingHandCursor)
        self._dragging = False

    def set_image_labels(self, labels: list):
        self.image_labels = labels
        self.update()

    def notify_image_loaded(self):
        self.update()

    def _tile_height(self) -> int:
        n = len(self.image_labels)
        if n == 0:
            return TILE_MAX_H
        available = self.height() - (n - 1) * TILE_GAP
        return int(max(TILE_MIN_H, min(TILE_MAX_H, available / n)))

    def _tile_rect(self, index: int, tile_h: int) -> QRect:
        y = index * (tile_h + TILE_GAP)
        return QRect(TILE_PADDING, y, FILMSTRIP_W - TILE_PADDING * 2, tile_h)

    def _tile_index_at(self, pos: QPoint) -> int | None:
        if pos.x() >= FILMSTRIP_W:
            return None
        tile_h = self._tile_height()
        stride = tile_h + TILE_GAP
        index  = pos.y() // stride
        if index < 0 or index >= len(self.image_labels):
            return None
        if pos.y() > index * stride + tile_h:
            return None
        return index

    def _current_image_index(self) -> int:
        if not self.image_labels:
            return 0
        scroll_top = self.scroll_area.verticalScrollBar().value()
        cumulative = 0
        for i, label in enumerate(self.image_labels):
            if cumulative + label.height() > scroll_top:
                return i
            cumulative += label.height()
        return len(self.image_labels) - 1

    def _offset_within_current(self, idx: int) -> float:
        if not self.image_labels or idx >= len(self.image_labels):
            return 0.0
        scroll_top = self.scroll_area.verticalScrollBar().value()
        cumulative = sum(self.image_labels[i].height() for i in range(idx))
        h          = self.image_labels[idx].height()
        return max(0.0, min(1.0, (scroll_top - cumulative) / h)) if h > 0 else 0.0

    def _viewport_fraction_of_image(self, idx: int) -> float:
        if not self.image_labels or idx >= len(self.image_labels):
            return 1.0
        img_h  = self.image_labels[idx].height()
        view_h = self.scroll_area.viewport().height()
        return min(1.0, view_h / img_h) if img_h > 0 else 1.0

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(QRect(0, 0, FILMSTRIP_W, self.height()),            QColor("#1a1a1a"))
        painter.fillRect(QRect(FILMSTRIP_W, 0, IMAGE_STRIP_W, self.height()), QColor("#141414"))
        if not self.image_labels:
            return
        current_idx = self._current_image_index()
        self._paint_filmstrip(painter, current_idx)
        self._paint_image_strip(painter, current_idx)

    def _paint_filmstrip(self, painter: QPainter, current_idx: int):
        tile_h = self._tile_height()
        tile_w = FILMSTRIP_W - TILE_PADDING * 2
        for i, label in enumerate(self.image_labels):
            rect = self._tile_rect(i, tile_h)
            src  = getattr(label, '_original_pixmap', None)
            if src and not src.isNull():
                sw, sh   = src.width(), src.height()
                scale    = max(tile_w / sw, tile_h / sh)
                dw, dh   = int(sw * scale), int(sh * scale)
                cx, cy   = (dw - tile_w) // 2, (dh - tile_h) // 2
                src_crop = QRect(
                    int(cx / scale), int(cy / scale),
                    int(tile_w / scale), int(tile_h / scale)
                )
                painter.drawPixmap(rect, src, src_crop)
            else:
                painter.fillRect(rect, QColor("#2a2a2a"))
            if i == current_idx:
                painter.fillRect(rect, QColor(41, 121, 255, 50))
                pen = QPen(QColor(41, 121, 255, 220))
                pen.setWidth(1)
                painter.setPen(pen)
                painter.drawRect(rect.adjusted(0, 0, -1, -1))
            else:
                painter.setPen(QColor("#0e0e0e"))
                painter.drawLine(rect.left(), rect.bottom() + 1,
                                 rect.right(), rect.bottom() + 1)

    def _paint_image_strip(self, painter: QPainter, current_idx: int):
        strip_rect = QRect(FILMSTRIP_W, 0, IMAGE_STRIP_W, self.height())
        src        = getattr(self.image_labels[current_idx], '_original_pixmap', None)
        if src and not src.isNull():
            painter.drawPixmap(strip_rect, src, src.rect())
        else:
            painter.fillRect(strip_rect, QColor("#2a2a2a"))
        offset_frac   = self._offset_within_current(current_idx)
        viewport_frac = self._viewport_fraction_of_image(current_idx)
        rect_top      = int(offset_frac * self.height())
        rect_h        = max(3, int(viewport_frac * self.height()))
        vp_rect       = QRect(FILMSTRIP_W, rect_top, IMAGE_STRIP_W, rect_h)
        painter.fillRect(vp_rect, QColor(41, 121, 255, 55))
        pen = QPen(QColor(41, 121, 255, 230))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawRect(vp_rect.adjusted(0, 0, -1, -1))

    def _jump_to_image(self, index: int):
        if not self.image_labels or index >= len(self.image_labels):
            return
        cumulative = sum(self.image_labels[i].height() for i in range(index))
        bar = self.scroll_area.verticalScrollBar()
        bar.setValue(max(0, min(cumulative, bar.maximum())))

    def _scrub_strip_to_y(self, widget_y: int):
        idx = self._current_image_index()
        if not self.image_labels or idx >= len(self.image_labels):
            return
        frac       = max(0.0, min(1.0, widget_y / self.height()))
        img_h      = self.image_labels[idx].height()
        cumulative = sum(self.image_labels[i].height() for i in range(idx))
        view_h     = self.scroll_area.viewport().height()
        target     = cumulative + int(frac * img_h) - view_h // 2
        bar = self.scroll_area.verticalScrollBar()
        bar.setValue(max(0, min(target, bar.maximum())))

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        self._dragging = True
        self._handle_pos(event.pos())

    def mouseMoveEvent(self, event):
        if self._dragging:
            self._handle_pos(event.pos())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = False

    def _handle_pos(self, pos: QPoint):
        if pos.x() < FILMSTRIP_W:
            idx = self._tile_index_at(pos)
            if idx is not None:
                self._jump_to_image(idx)
        else:
            self._scrub_strip_to_y(pos.y())

    def resizeEvent(self, event):
        self.update()
        super().resizeEvent(event)


class ViewerPage(QWidget):

    def __init__(self, main_window):
        super().__init__()
        self.main_window           = main_window
        self.webtoon               = None
        self.current_chapter_index = 0
        self.progress_store        = get_progress_store()

        self._restore_image_index  = None
        self._restore_image_offset = 0.0

        self._pending_batch: dict[int, QPixmap] = {}

        self.loader = ImageLoader()
        self.loader.image_ready.connect(self._on_image_ready)

        self._batch_timer = QTimer()
        self._batch_timer.setSingleShot(True)
        self._batch_timer.setInterval(BATCH_MS)
        self._batch_timer.timeout.connect(self._flush_batch)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(6, 6, 6, 6)
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(self.go_back)
        self.prev_button = QPushButton("Previous Chapter")
        self.prev_button.clicked.connect(self.prev_chapter)
        self.next_button = QPushButton("Next Chapter")
        self.next_button.clicked.connect(self.next_chapter)
        self.chapter_selector = QComboBox()
        self.chapter_selector.currentIndexChanged.connect(self.load_selected_chapter)
        top_bar.addWidget(self.back_button)
        top_bar.addWidget(self.prev_button)
        top_bar.addWidget(self.next_button)
        top_bar.addWidget(self.chapter_selector)
        main_layout.addLayout(top_bar)

        content_row = QHBoxLayout()
        content_row.setContentsMargins(0, 0, 0, 0)
        content_row.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.preview = ChapterPreview(self.scroll)

        content_row.addWidget(self.scroll)
        content_row.addWidget(self.preview)
        main_layout.addLayout(content_row)

        self.auto_scroll        = False
        self.auto_scroll_origin = QPoint()
        self.current_mouse_pos  = QPoint()

        self.scroll_timer = QTimer()
        self.scroll_timer.timeout.connect(self.perform_auto_scroll)

        self._resize_timer = QTimer()
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(150)
        self._resize_timer.timeout.connect(self.rescale_images)

        self.scroll.viewport().installEventFilter(self)
        self.scroll.setMouseTracking(True)

        self.container    = QWidget()
        self.image_layout = QVBoxLayout(self.container)
        self.image_layout.setSpacing(0)
        self.image_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll.setWidget(self.container)

        self.image_labels = []

        self.scroll.verticalScrollBar().valueChanged.connect(self.check_visible_images)
        self.scroll.verticalScrollBar().valueChanged.connect(self.preview.update)

        self._progress_save_timer = QTimer()
        self._progress_save_timer.setSingleShot(True)
        self._progress_save_timer.setInterval(1000)
        self._progress_save_timer.timeout.connect(self._save_progress)
        self.scroll.verticalScrollBar().valueChanged.connect(
            lambda: self._progress_save_timer.start()
        )

    # ------------------------------------------------------------------ #
    #  Public entry points                                                 #
    # ------------------------------------------------------------------ #

    def load_webtoon(self, webtoon, start_chapter: int = 0, start_scroll: float = 0.0):
        webtoon.path = os.path.abspath(webtoon.path)
        self.webtoon = webtoon
        self._unpack_restore(start_scroll)

        self.chapter_selector.blockSignals(True)
        self.chapter_selector.clear()
        self.chapter_selector.addItems(webtoon.chapters)
        self.chapter_selector.blockSignals(False)

        self.current_chapter_index = start_chapter
        self._load_chapter_no_prompt(start_chapter)

    # ------------------------------------------------------------------ #
    #  Progress save                                                       #
    # ------------------------------------------------------------------ #

    def _current_packed_position(self) -> float:
        if not self.image_labels:
            return 0.0
        scroll_top = self.scroll.verticalScrollBar().value()
        cumulative = 0
        for i, label in enumerate(self.image_labels):
            h = label.height()
            if cumulative + h > scroll_top:
                offset_frac = ((scroll_top - cumulative) / h) if h > 0 else 0.0
                return i + offset_frac
            cumulative += h
        return len(self.image_labels) - 1

    def _save_progress(self):
        if not self.webtoon or not self.image_labels:
            return
        chapter = self.webtoon.chapters[self.current_chapter_index]
        packed  = self._current_packed_position()
        total   = len(self.image_labels)
        self.progress_store.save(self.webtoon.name, chapter, packed, total)

    # ------------------------------------------------------------------ #
    #  Progress restore                                                    #
    # ------------------------------------------------------------------ #

    def _unpack_restore(self, packed: float):
        if packed < 0.005:
            self._restore_image_index  = None
            self._restore_image_offset = 0.0
        else:
            self._restore_image_index  = int(packed)
            self._restore_image_offset = packed - int(packed)

    def _apply_restore(self):
        idx = self._restore_image_index
        if idx is None or idx >= len(self.image_labels):
            return
        for i in range(idx + 1):
            lbl = self.image_labels[i]
            if lbl.pixmap() is None or lbl.pixmap().isNull():
                return
        if self._jump_to_packed(idx, self._restore_image_offset):
            self._restore_image_index = None

    def _jump_to_packed(self, idx: int, offset_frac: float) -> bool:
        cumulative = sum(self.image_labels[i].height() for i in range(idx))
        target_px  = cumulative + int(self.image_labels[idx].height() * offset_frac)

        QApplication.processEvents()

        bar = self.scroll.verticalScrollBar()
        if target_px > bar.maximum():
            bar.setMaximum(target_px)
        bar.setValue(target_px)

        if bar.value() < target_px - 5:
            return False
        return True

    # ------------------------------------------------------------------ #
    #  Batch flush                                                       #
    # ------------------------------------------------------------------ #

    def _on_image_ready(self, index: int, pixmap: QPixmap):
        """
        Called from the loader signal (main thread). Instead of immediately
        updating the label and triggering a layout recalculation, queue the
        pixmap and let the batch timer flush them all at once.
        """
        self._pending_batch[index] = pixmap
        if not self._batch_timer.isActive():
            self._batch_timer.start()

    def _flush_batch(self):
        """
        Apply all queued pixmaps in one pass. Qt only needs to recalculate
        the layout once after all setFixedHeight calls settle, rather than
        once per image.
        """
        if not self._pending_batch:
            return

        # Suspend layout updates while we apply all pixmaps
        self.container.setUpdatesEnabled(False)
        try:
            needs_restore_check = False
            restore_idx         = self._restore_image_index

            for index, pixmap in self._pending_batch.items():
                if index >= len(self.image_labels):
                    continue
                label = self.image_labels[index]
                label._original_pixmap = pixmap
                self._apply_pixmap_to_label(label)
                if restore_idx is not None and index <= restore_idx:
                    needs_restore_check = True
        finally:
            self._pending_batch.clear()
            self.container.setUpdatesEnabled(True)

        self.preview.notify_image_loaded()

        # Trigger lazy load in case new heights opened up off-screen content
        self.check_visible_images()

        if needs_restore_check:
            self._apply_restore()

    # ------------------------------------------------------------------ #
    #  Chapter loading                                                     #
    # ------------------------------------------------------------------ #

    def load_selected_chapter(self, index):
        self._load_chapter_with_prompt(index)

    def _load_chapter_with_prompt(self, index):
        if not self.webtoon:
            return
        chapter = self.webtoon.chapters[index]

        saved_scroll = self.progress_store.get_for_chapter(self.webtoon.name, chapter)
        packed = 0.0
        if saved_scroll > 0.005:
            dlg = ContinueDialog(chapter, parent=self)
            if dlg.exec() == QDialog.Accepted:
                packed = saved_scroll

        self._unpack_restore(packed)
        self._load_chapter_no_prompt(index)

    def _load_chapter_no_prompt(self, index):
        if not self.webtoon:
            return
        self.current_chapter_index = index
        chapter = self.webtoon.chapters[index]

        self.chapter_selector.blockSignals(True)
        self.chapter_selector.setCurrentIndex(index)
        self.chapter_selector.blockSignals(False)

        self._load_chapter_images(chapter)
        self.update_nav_buttons()

        if self._restore_image_index is None:
            self.scroll.verticalScrollBar().setValue(0)

    def clear_images(self):
        self._batch_timer.stop()
        self._pending_batch.clear()

        self.loader.cancel()
        self.loader.reset()
        self.scroll.takeWidget()

        for label in self.image_labels:
            label.deleteLater()
        self.image_labels = []

        self.container    = QWidget()
        self.image_layout = QVBoxLayout(self.container)
        self.image_layout.setSpacing(0)
        self.image_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll.setWidget(self.container)

        self.preview.set_image_labels([])

    def _load_chapter_images(self, chapter):
        self.clear_images()

        chapter_path = os.path.join(self.webtoon.path, chapter)
        image_files  = sorted(
            f for f in os.listdir(chapter_path)
            if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        )

        for img_file in image_files:
            label          = QLabel()
            label.img_path = os.path.join(chapter_path, img_file)
            label.setAlignment(Qt.AlignCenter)
            label.setMinimumHeight(400)
            self.image_layout.addWidget(label)
            self.image_labels.append(label)

        self.preview.set_image_labels(self.image_labels)

        QTimer.singleShot(50, self.check_visible_images)

        if self._restore_image_index is not None:
            QTimer.singleShot(50, self._preload_restore_target)

    def _preload_restore_target(self):
        idx = self._restore_image_index
        if idx is None or idx >= len(self.image_labels):
            return
        vw  = self.scroll.viewport().width() // 2
        end = min(len(self.image_labels), idx + 3)
        for i in range(end):
            self.loader.load(i, self.image_labels[i].img_path, vw)

    # ------------------------------------------------------------------ #
    #  Lazy loading                                                        #
    # ------------------------------------------------------------------ #

    def check_visible_images(self):
        viewport_top    = self.scroll.verticalScrollBar().value()
        viewport_bottom = viewport_top + self.scroll.viewport().height()
        viewport_width  = self.scroll.viewport().width() // 2

        cumulative = 0
        for i, label in enumerate(self.image_labels):
            h   = label.height()
            pos = cumulative
            cumulative += h

            if pos + h < viewport_top - LAZY_WINDOW:
                continue
            if pos > viewport_bottom + LAZY_WINDOW:
                continue
            if label.pixmap() is not None and not label.pixmap().isNull():
                continue

            self.loader.load(i, label.img_path, viewport_width)

    # ------------------------------------------------------------------ #
    #  Image scaling                                                       #
    # ------------------------------------------------------------------ #

    def _apply_pixmap_to_label(self, label):
        if not hasattr(label, '_original_pixmap'):
            return
        viewport_width = self.scroll.viewport().width() // 2
        scaled = label._original_pixmap.scaledToWidth(viewport_width, Qt.SmoothTransformation)
        label.setPixmap(scaled)
        label.setFixedHeight(scaled.height())

    def rescale_images(self):
        packed = self._current_packed_position()
        idx    = int(packed)
        frac   = packed - idx

        self.container.setUpdatesEnabled(False)
        try:
            for label in self.image_labels:
                self._apply_pixmap_to_label(label)
        finally:
            self.container.setUpdatesEnabled(True)

        if (self.image_labels
                and idx < len(self.image_labels)
                and hasattr(self.image_labels[idx], '_original_pixmap')):
            QApplication.processEvents()
            self._jump_to_packed(idx, frac)

        self.preview.update()

    # ------------------------------------------------------------------ #
    #  Navigation                                                          #
    # ------------------------------------------------------------------ #

    def next_chapter(self):
        if self.current_chapter_index < len(self.webtoon.chapters) - 1:
            self._save_progress()
            self._restore_image_index = None
            self._load_chapter_no_prompt(self.current_chapter_index + 1)

    def prev_chapter(self):
        if self.current_chapter_index > 0:
            self._save_progress()
            self._restore_image_index = None
            self._load_chapter_no_prompt(self.current_chapter_index - 1)

    def update_nav_buttons(self):
        self.prev_button.setEnabled(self.current_chapter_index > 0)
        self.next_button.setEnabled(
            self.current_chapter_index < len(self.webtoon.chapters) - 1
        )

    def go_back(self):
        self._save_progress()
        self.main_window.library.refresh_progress()
        self.main_window.open_detail(self.webtoon)

    # ------------------------------------------------------------------ #
    #  Resize                                                              #
    # ------------------------------------------------------------------ #

    def resizeEvent(self, event):
        self._resize_timer.start()
        super().resizeEvent(event)

    # ------------------------------------------------------------------ #
    #  Middle-click auto scroll                                            #
    # ------------------------------------------------------------------ #

    def eventFilter(self, obj, event):
        if obj == self.scroll.viewport():
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.MiddleButton:
                self.auto_scroll = not self.auto_scroll
                if self.auto_scroll:
                    self.auto_scroll_origin = event.pos()
                    self.current_mouse_pos  = event.pos()
                    self.scroll_timer.start(16)
                else:
                    self.scroll_timer.stop()
                return True

            if event.type() == QEvent.MouseMove and self.auto_scroll:
                self.current_mouse_pos = event.pos()
                return True

            if (event.type() == QEvent.MouseButtonPress
                    and event.button() == Qt.LeftButton
                    and self.auto_scroll):
                self.auto_scroll = False
                self.scroll_timer.stop()

        return super().eventFilter(obj, event)

    def perform_auto_scroll(self):
        delta = self.current_mouse_pos - self.auto_scroll_origin
        speed = delta.y() * 0.5
        bar   = self.scroll.verticalScrollBar()
        bar.setValue(bar.value() + int(speed))