import os
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QScrollArea,
    QPushButton, QComboBox, QHBoxLayout, QDialog, QApplication, QSlider
)
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QImage
from PySide6.QtCore import Qt, QPoint, QEvent, QTimer, Signal, QObject, QRect

from progress_store import get_instance as get_progress_store

FILMSTRIP_W   = 25
IMAGE_STRIP_W = 50
PREVIEW_W     = FILMSTRIP_W + IMAGE_STRIP_W

TILE_GAP      = 2
TILE_PADDING  = 2
TILE_MIN_H    = 14
TILE_MAX_H    = 120

LAZY_WINDOW   = 2000
BATCH_MS      = 16
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
    panel_starts_ready = Signal(int, list)

    def __init__(self):
        super().__init__()
        self.executor = ThreadPoolExecutor(max_workers=NUM_WORKERS)
        self._cancelled = False
        self._queued = set()

    def cancel(self):
        self._cancelled = True
        self._queued.clear()

    def reset(self):
        self._cancelled = False

    def load(self, index: int, path: str, width: int):
        if index in self._queued:
            return
        self._queued.add(index)
        self.executor.submit(self._load_task, index, path)

    def _load_task(self, index: int, path: str):
        if self._cancelled:
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return
        if not self._cancelled:
            self.image_ready.emit(index, pixmap)

    def build_panel_starts(self, generation: int, payload: list):
        self.executor.submit(self._panel_task, generation, payload)

    def _panel_task(self, generation: int, payload: list):
        MIN_BLANK = 18
        ROW_STEP = 4

        starts = [0]
        cumulative = 0

        for item in payload:
            h = item["height"]
            path = item["path"]

            if not path or h <= 0:
                cumulative += max(0, h)
                continue

            img = QImage(path)
            if img.isNull():
                cumulative += h
                continue

            ih = img.height()
            if ih <= 0:
                cumulative += h
                continue

            scale = ih / h

            in_blank = self._is_blank_row(img, 0)
            blank_run = 0

            for doc_y in range(ROW_STEP, h, ROW_STEP):
                src_y = min(ih - 1, int(doc_y * scale))
                is_blank = self._is_blank_row(img, src_y)

                if is_blank:
                    blank_run += ROW_STEP
                else:
                    if in_blank and blank_run >= MIN_BLANK:
                        starts.append(cumulative + doc_y)
                    blank_run = 0

                in_blank = is_blank

            cumulative += h

        if not self._cancelled:
            self.panel_starts_ready.emit(generation, sorted(set(starts)))

    def _is_blank_row(self, image: QImage, y: int, sample_step: int = 12) -> bool:
        w = image.width()
        if w <= 0:
            return True

        lums = []
        for x in range(0, w, sample_step):
            c = image.pixelColor(x, y)
            lum = 0.299 * c.redF() + 0.587 * c.greenF() + 0.114 * c.blueF()
            lums.append(lum)

        if not lums:
            return True

        avg = sum(lums) / len(lums)
        variance = sum((l - avg) ** 2 for l in lums) / len(lums)

        is_extreme = avg < 0.12 or avg > 0.88
        is_uniform = variance < 0.003
        return is_extreme and is_uniform


class ChapterPreview(QWidget):

    def __init__(self, scroll_area: QScrollArea, parent=None):
        super().__init__(parent)
        self.scroll_area = scroll_area
        self.image_labels = []
        self.setFixedWidth(PREVIEW_W)
        self.setCursor(Qt.PointingHandCursor)
        self._dragging = False
        self._zoom = 1.0

    def set_zoom(self, zoom: float):
        self._zoom = zoom
        self.update()

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
        index = pos.y() // stride
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

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(QRect(0, 0, FILMSTRIP_W, self.height()), QColor("#1a1a1a"))
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
            src = getattr(label, '_original_pixmap', None)
            if src and not src.isNull():
                sw, sh = src.width(), src.height()
                scale = max(tile_w / sw, tile_h / sh)
                dw, dh = int(sw * scale), int(sh * scale)
                cx, cy = (dw - tile_w) // 2, (dh - tile_h) // 2
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
                painter.drawLine(rect.left(), rect.bottom() + 1, rect.right(), rect.bottom() + 1)

    def _paint_image_strip(self, painter: QPainter, current_idx: int):
        strip_x = FILMSTRIP_W
        strip_w = IMAGE_STRIP_W
        strip_h = self.height()
        strip_rect = QRect(strip_x, 0, strip_w, strip_h)

        total_content_h = sum(lbl.height() for lbl in self.image_labels)
        if total_content_h == 0:
            painter.fillRect(strip_rect, QColor("#2a2a2a"))
            return

        scroll_top = self.scroll_area.verticalScrollBar().value()
        view_h = self.scroll_area.viewport().height()
        scroll_max = max(1, self.scroll_area.verticalScrollBar().maximum())

        viewport_content_frac = view_h / (total_content_h + view_h)
        coverage = max(0.20, viewport_content_frac)

        scroll_frac = scroll_top / scroll_max
        window_top_frac = scroll_frac * (1.0 - coverage)
        window_bot_frac = window_top_frac + coverage

        if window_bot_frac > 1.0:
            window_bot_frac = 1.0
            window_top_frac = 1.0 - coverage
        window_top_frac = max(0.0, window_top_frac)

        content_top = window_top_frac * total_content_h
        content_bot = window_bot_frac * total_content_h

        painter.save()
        painter.setClipRect(strip_rect)

        cumulative = 0
        for lbl in self.image_labels:
            img_h = lbl.height()
            img_top = cumulative
            img_bot = cumulative + img_h
            cumulative += img_h

            if img_bot <= content_top or img_top >= content_bot:
                continue

            src = getattr(lbl, '_original_pixmap', None)

            src_frac_top = max(0.0, (content_top - img_top) / img_h) if img_h else 0.0
            src_frac_bot = min(1.0, (content_bot - img_top) / img_h) if img_h else 1.0

            dst_top = int((img_top - content_top) / (content_bot - content_top) * strip_h)
            dst_bot = int((img_bot - content_top) / (content_bot - content_top) * strip_h)
            dst_top = max(0, dst_top)
            dst_bot = min(strip_h, dst_bot)
            dst_rect = QRect(strip_x, dst_top, strip_w, dst_bot - dst_top)

            if src and not src.isNull():
                sw, sh = src.width(), src.height()
                src_rect = QRect(
                    0,
                    int(src_frac_top * sh),
                    sw,
                    max(1, int((src_frac_bot - src_frac_top) * sh))
                )
                painter.drawPixmap(dst_rect, src, src_rect)
            else:
                painter.fillRect(dst_rect, QColor("#2a2a2a"))

        painter.restore()

        indicator_top = int(((scroll_top / total_content_h) - window_top_frac) / coverage * strip_h)
        indicator_h = max(3, int((view_h / total_content_h) / coverage * strip_h))
        indicator_top = max(0, min(strip_h - indicator_h, indicator_top))

        vp_rect = QRect(strip_x, indicator_top, strip_w, indicator_h)
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
        if not self.image_labels:
            return
        total_content_h = sum(lbl.height() for lbl in self.image_labels)
        if total_content_h == 0:
            return

        bar = self.scroll_area.verticalScrollBar()
        view_h = self.scroll_area.viewport().height()
        scroll_max = max(1, bar.maximum())

        viewport_content_frac = view_h / (total_content_h + view_h)
        coverage = max(0.20, viewport_content_frac)

        scroll_frac = bar.value() / scroll_max
        window_top_frac = scroll_frac * (1.0 - coverage)
        window_bot_frac = window_top_frac + coverage
        if window_bot_frac > 1.0:
            window_bot_frac = 1.0
            window_top_frac = 1.0 - coverage
        window_top_frac = max(0.0, window_top_frac)

        click_frac = max(0.0, min(1.0, widget_y / self.height()))
        content_frac = window_top_frac + click_frac * coverage
        target = int(content_frac * total_content_h) - view_h // 2
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
        self.setFocusPolicy(Qt.StrongFocus)

        self.main_window = main_window
        self.webtoon = None
        self.current_chapter_index = 0
        self.progress_store = get_progress_store()

        self._restore_image_index = None
        self._restore_image_offset = 0.0
        self._resize_packed = None

        self._pending_batch: dict[int, QPixmap] = {}

        self.loader = ImageLoader()
        self.loader.image_ready.connect(self._on_image_ready)
        self.loader.panel_starts_ready.connect(self._on_panel_starts_ready)

        self._batch_timer = QTimer()
        self._batch_timer.setSingleShot(True)
        self._batch_timer.setInterval(BATCH_MS)
        self._batch_timer.timeout.connect(self._flush_batch)

        self._panel_starts = []
        self._panel_starts_dirty = True
        self._panel_build_generation = 0
        self._panel_build_inflight = False

        self._panel_warm_timer = QTimer()
        self._panel_warm_timer.setSingleShot(True)
        self._panel_warm_timer.setInterval(180)
        self._panel_warm_timer.timeout.connect(self._warm_panel_cache)

        self._zoom = 0.5

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

        zoom_out_btn = QPushButton("−")
        zoom_out_btn.setFixedWidth(28)
        zoom_out_btn.setToolTip("Decrease image width")
        zoom_out_btn.clicked.connect(self._zoom_out)

        zoom_in_btn = QPushButton("+")
        zoom_in_btn.setFixedWidth(28)
        zoom_in_btn.setToolTip("Increase image width")
        zoom_in_btn.clicked.connect(self._zoom_in)

        self._zoom_slider = QSlider(Qt.Horizontal)
        self._zoom_slider.setFixedWidth(100)
        self._zoom_slider.setMinimum(25)
        self._zoom_slider.setMaximum(100)
        self._zoom_slider.setValue(int(self._zoom * 100))
        self._zoom_slider.setToolTip("Image width")
        self._zoom_slider.valueChanged.connect(self._on_zoom_slider)

        self._zoom_label = QLabel(f"{int(self._zoom * 100)}%")
        self._zoom_label.setFixedWidth(36)
        self._zoom_label.setAlignment(Qt.AlignCenter)
        self._zoom_label.setStyleSheet("color: #aaa; font-size: 12px;")

        top_bar.addWidget(self.back_button)
        top_bar.addWidget(self.prev_button)
        top_bar.addWidget(self.next_button)
        top_bar.addWidget(self.chapter_selector)
        top_bar.addStretch()
        top_bar.addWidget(zoom_out_btn)
        top_bar.addWidget(self._zoom_slider)
        top_bar.addWidget(zoom_in_btn)
        top_bar.addWidget(self._zoom_label)
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

        self.auto_scroll = False
        self.auto_scroll_origin = QPoint()
        self.current_mouse_pos = QPoint()

        self.scroll_timer = QTimer()
        self.scroll_timer.timeout.connect(self.perform_auto_scroll)

        self._resize_timer = QTimer()
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(150)
        self._resize_timer.timeout.connect(self.rescale_images)

        self.scroll.viewport().installEventFilter(self)
        self.scroll.setMouseTracking(True)

        self.container = QWidget()
        self.container.installEventFilter(self)
        self.image_layout = QVBoxLayout(self.container)
        self.image_layout.setSpacing(0)
        self.image_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll.setWidget(self.container)

        self.preview.installEventFilter(self)

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
        self._did_immediate_first_paint = False

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
        total = len(self.image_labels)
        bar = self.scroll.verticalScrollBar()
        if bar.value() >= bar.maximum() and bar.maximum() > 0:
            packed = float(total)
        else:
            packed = self._current_packed_position()
        self.progress_store.save(self.webtoon.name, chapter, packed, total)

    def _unpack_restore(self, packed: float):
        if packed < 0.005:
            self._restore_image_index = None
            self._restore_image_offset = 0.0
        else:
            self._restore_image_index = int(packed)
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
        target_px = cumulative + int(self.image_labels[idx].height() * offset_frac)

        QApplication.processEvents()

        bar = self.scroll.verticalScrollBar()
        if target_px > bar.maximum():
            bar.setMaximum(target_px)
        bar.setValue(target_px)

        return not (bar.value() < target_px - 5)

    def _on_image_ready(self, index: int, pixmap: QPixmap):
        if index >= len(self.image_labels):
            return

        # Only do one immediate paint per chapter load.
        # Everything else should go through the batch path so restore logic runs.
        if not self._did_immediate_first_paint and not self._pending_batch:
            label = self.image_labels[index]
            label._source_pixmap = pixmap
            label._original_pixmap = pixmap
            self._apply_pixmap_to_label(label)

            self._did_immediate_first_paint = True

            self.preview.notify_image_loaded()
            self.check_visible_images()
            self._panel_warm_timer.start()

            # Restore might already be possible for very small saved positions.
            self._apply_restore()
            return

        self._pending_batch[index] = pixmap
        if not self._batch_timer.isActive():
            self._batch_timer.start()

    def _flush_batch(self):
        if not self._pending_batch:
            return

        self.container.setUpdatesEnabled(False)
        try:
            needs_restore_check = False
            restore_idx = self._restore_image_index

            for index, pixmap in self._pending_batch.items():
                if index >= len(self.image_labels):
                    continue
                label = self.image_labels[index]
                label._source_pixmap = pixmap
                label._original_pixmap = pixmap
                self._apply_pixmap_to_label(label)
                if restore_idx is not None and index <= restore_idx:
                    needs_restore_check = True
        finally:
            self._pending_batch.clear()
            self.container.setUpdatesEnabled(True)

        self.preview.notify_image_loaded()
        self._invalidate_panel_cache()
        self.check_visible_images()
        self._panel_warm_timer.start()

        if self._resize_packed is not None:
            idx = int(self._resize_packed)
            frac = self._resize_packed - idx
            if (
                self.image_labels
                and idx < len(self.image_labels)
                and getattr(self.image_labels[idx], '_source_pixmap', None) is not None
            ):
                self._jump_to_packed(idx, frac)
                self._resize_packed = None

        if needs_restore_check:
            self._apply_restore()

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
        self._panel_warm_timer.stop()
        self._pending_batch.clear()
        self._did_immediate_first_paint = False

        self.loader.cancel()
        self.loader.reset()
        self.scroll.takeWidget()

        for label in self.image_labels:
            label.deleteLater()
        self.image_labels = []

        self.container = QWidget()
        self.container.installEventFilter(self)
        self.image_layout = QVBoxLayout(self.container)
        self.image_layout.setSpacing(0)
        self.image_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll.setWidget(self.container)

        self.preview.set_image_labels([])

        self._panel_starts = []
        self._panel_starts_dirty = True
        self._panel_build_generation += 1
        self._panel_build_inflight = False

    def _load_chapter_images(self, chapter):
        self.clear_images()

        chapter_path = os.path.join(self.webtoon.path, chapter)
        image_files = sorted(
            f for f in os.listdir(chapter_path)
            if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        )

        for img_file in image_files:
            label = QLabel()
            label.img_path = os.path.join(chapter_path, img_file)
            label.setAlignment(Qt.AlignCenter)
            label.setMinimumHeight(400)
            self.image_layout.addWidget(label)
            self.image_labels.append(label)

        self.preview.set_image_labels(self.image_labels)

        self.check_visible_images()
        QTimer.singleShot(0, self.check_visible_images)

        if self._restore_image_index is not None:
            self._preload_restore_target()

        self.setFocus()

    def _preload_restore_target(self):
        idx = self._restore_image_index
        if idx is None or idx >= len(self.image_labels):
            return
        end = min(len(self.image_labels), idx + 3)
        for i in range(end):
            self.loader.load(i, self.image_labels[i].img_path, 0)

    def check_visible_images(self):
        viewport_top = self.scroll.verticalScrollBar().value()
        viewport_bottom = viewport_top + self.scroll.viewport().height()

        cumulative = 0
        for i, label in enumerate(self.image_labels):
            h = label.height()
            pos = cumulative
            cumulative += h

            if pos + h < viewport_top - LAZY_WINDOW:
                continue
            if pos > viewport_bottom + LAZY_WINDOW:
                continue
            if getattr(label, '_source_pixmap', None) is not None:
                continue

            self.loader.load(i, label.img_path, 0)

    def _zoom_out(self):
        self._set_zoom(self._zoom - 0.05)

    def _zoom_in(self):
        self._set_zoom(self._zoom + 0.05)

    def _on_zoom_slider(self, value: int):
        self._set_zoom(value / 100.0, update_slider=False)

    def _set_zoom(self, zoom: float, update_slider: bool = True):
        self._zoom = max(0.25, min(1.0, zoom))
        if update_slider:
            self._zoom_slider.blockSignals(True)
            self._zoom_slider.setValue(int(self._zoom * 100))
            self._zoom_slider.blockSignals(False)
        self._zoom_label.setText(f"{int(self._zoom * 100)}%")
        self.preview.set_zoom(self._zoom)
        self.rescale_images()

    def _image_width(self) -> int:
        return max(1, int(self.scroll.viewport().width() * self._zoom))

    def _apply_pixmap_to_label(self, label):
        src = getattr(label, '_source_pixmap', None)
        if src is None or src.isNull():
            return
        scaled = src.scaledToWidth(self._image_width(), Qt.SmoothTransformation)
        label._original_pixmap = scaled
        label.setPixmap(scaled)
        label.setFixedHeight(scaled.height())

    def rescale_images(self):
        packed = self._current_packed_position()

        self.container.setUpdatesEnabled(False)
        try:
            for label in self.image_labels:
                self._apply_pixmap_to_label(label)
        finally:
            self.container.setUpdatesEnabled(True)

        idx = int(packed)
        frac = packed - idx
        if self.image_labels and idx < len(self.image_labels):
            QApplication.processEvents()
            self._jump_to_packed(idx, frac)

        self.preview.update()
        self._invalidate_panel_cache()
        self._panel_warm_timer.start()

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

    def resizeEvent(self, event):
        self._resize_timer.start()
        super().resizeEvent(event)

    def _invalidate_panel_cache(self):
        self._panel_starts_dirty = True
        self._panel_build_generation += 1

    def _warm_panel_cache(self):
        if self._panel_build_inflight:
            return

        payload = []
        any_loaded = False

        for label in self.image_labels:
            src = getattr(label, '_source_pixmap', None)
            h = label.height()
            path = getattr(label, 'img_path', None)

            if src is None or src.isNull() or h <= 0 or not path:
                payload.append({"height": h, "path": None})
                continue

            any_loaded = True
            payload.append({"height": h, "path": path})

        if not any_loaded:
            return

        self._panel_build_inflight = True
        generation = self._panel_build_generation
        self.loader.build_panel_starts(generation, payload)

    def _on_panel_starts_ready(self, generation: int, starts: list):
        self._panel_build_inflight = False

        if generation != self._panel_build_generation:
            return

        self._panel_starts = starts
        self._panel_starts_dirty = False

    def _get_panel_starts(self) -> list[int]:
        if self._panel_starts_dirty:
            return []
        return self._panel_starts

    def _total_content_height(self) -> int:
        return sum(label.height() for label in self.image_labels)

    def _merge_close_targets(self, targets: list[int], min_gap: int) -> list[int]:
        if not targets:
            return []

        merged = [targets[0]]
        for t in targets[1:]:
            if t - merged[-1] < min_gap:
                continue
            merged.append(t)
        return merged

    def _get_skip_targets(self) -> list[int]:
        starts = self._get_panel_starts()
        if not starts:
            return []

        total_h = self._total_content_height()
        view_h = self.scroll.viewport().height()
        if total_h <= 0 or view_h <= 0:
            return []

        panels = []
        for i, start in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else total_h
            if end > start:
                panels.append((start, end))

        targets = []

        # Tunables
        SHORT_PANEL_MAX = int(view_h * 0.95)
        TALL_STEP = int(view_h * 0.78)
        FIRST_PAD = int(view_h * 0.16)      # don't land exactly on panel start
        LAST_PAD = int(view_h * 0.20)       # keep some of panel bottom in view
        MIN_TARGET_GAP = max(90, int(view_h * 0.18))

        for start, end in panels:
            panel_h = end - start

            if panel_h <= SHORT_PANEL_MAX:
                # For shorter panels, bias slightly downward so you don't land
                # with the new dialogue barely starting at the bottom/top edge.
                target = start - FIRST_PAD
                targets.append(max(0, target))
                continue

            # Tall panels get multiple stops.
            first_target = max(0, start - FIRST_PAD)
            last_target = max(first_target, end - int(view_h * 0.80))

            t = first_target
            while t < last_target - 8:
                targets.append(max(0, t))
                t += TALL_STEP

            targets.append(max(0, last_target))

        max_scroll = self.scroll.verticalScrollBar().maximum()
        targets = [max(0, min(t, max_scroll)) for t in sorted(set(targets))]
        targets = self._merge_close_targets(targets, MIN_TARGET_GAP)
        return targets

    def _jump_to_target(self, target_y: int):
        bar = self.scroll.verticalScrollBar()
        bar.setValue(max(0, min(target_y, bar.maximum())))

    def keyPressEvent(self, event):
        key = event.key()
        bar = self.scroll.verticalScrollBar()
        view_h = self.scroll.viewport().height()
        pos = bar.value()
        center = pos + view_h / 2

        if key in (Qt.Key_Down, Qt.Key_Up):
            targets = self._get_skip_targets()

            if not targets:
                if key == Qt.Key_Down:
                    bar.setValue(pos + int(view_h * 0.9))
                else:
                    bar.setValue(max(0, pos - int(view_h * 0.9)))
                return

            SNAP = max(32, int(view_h * 0.07))
            MIN_MOVE = max(80, int(view_h * 0.16))

            if key == Qt.Key_Down:
                next_target = next(
                    (t for t in targets if (t + view_h / 2) > center + SNAP),
                    None
                )

                if next_target is not None:
                    # If the next detected target is too close, skip it and move to
                    # the next meaningful one instead.
                    while next_target is not None and (next_target - pos) < MIN_MOVE:
                        next_target = next(
                            (t for t in targets if t > next_target + 1 and (t - pos) >= MIN_MOVE),
                            None
                        )

                if next_target is not None:
                    self._jump_to_target(next_target)
                else:
                    bar.setValue(pos + int(view_h * 0.9))

            else:  # Qt.Key_Up
                prev_target = next(
                    (t for t in reversed(targets) if (t + view_h / 2) < center - SNAP),
                    None
                )

                if prev_target is not None:
                    while prev_target is not None and (pos - prev_target) < MIN_MOVE:
                        prev_target = next(
                            (t for t in reversed(targets) if t < prev_target - 1 and (pos - t) >= MIN_MOVE),
                            None
                        )

                if prev_target is not None:
                    self._jump_to_target(prev_target)
                else:
                    bar.setValue(max(0, pos - int(view_h * 0.9)))

        elif key == Qt.Key_Right:
            if self.current_chapter_index < len(self.webtoon.chapters) - 1:
                self._progress_save_timer.stop()
                self._save_progress()
                self._restore_image_index = None
                self._load_chapter_with_prompt(self.current_chapter_index + 1)
                self.setFocus()

        elif key == Qt.Key_Left:
            if self.current_chapter_index > 0:
                self._progress_save_timer.stop()
                self._save_progress()
                self._restore_image_index = None
                self._load_chapter_with_prompt(self.current_chapter_index - 1)
                self.setFocus()

        else:
            super().keyPressEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self.setFocus()

    def eventFilter(self, obj, event):
        container = getattr(self, "container", None)
        preview = getattr(self, "preview", None)
        viewport = self.scroll.viewport() if hasattr(self, "scroll") else None

        watched = tuple(x for x in (viewport, container, preview) if x is not None)

        if obj in watched:
            if event.type() == QEvent.MouseButtonPress:
                self.setFocus()

            if viewport is not None and obj == viewport:
                if event.type() == QEvent.MouseButtonPress and event.button() == Qt.MiddleButton:
                    if self.auto_scroll:
                        self.auto_scroll = False
                        self.scroll_timer.stop()
                        self.scroll.viewport().unsetCursor()
                        self.scroll.viewport().update()
                    else:
                        self.auto_scroll = True
                        self.auto_scroll_origin = event.pos()
                        self.current_mouse_pos = event.pos()
                        self.scroll.viewport().setCursor(Qt.SizeAllCursor)
                        self.scroll_timer.start(16)
                    self.setFocus()
                    return True

                if event.type() == QEvent.MouseMove and self.auto_scroll:
                    self.current_mouse_pos = event.pos()
                    self.scroll.viewport().update()
                    return True

                if (
                    event.type() == QEvent.MouseButtonPress
                    and event.button() == Qt.LeftButton
                    and self.auto_scroll
                ):
                    self.auto_scroll = False
                    self.scroll_timer.stop()
                    self.scroll.viewport().unsetCursor()
                    self.scroll.viewport().update()
                    self.setFocus()

                if event.type() == QEvent.Paint and self.auto_scroll:
                    painter = QPainter(self.scroll.viewport())
                    painter.setRenderHint(QPainter.Antialiasing)
                    ox = self.auto_scroll_origin.x()
                    oy = self.auto_scroll_origin.y()
                    dy = self.current_mouse_pos.y() - oy

                    DEADZONE = 8
                    painter.setPen(QPen(QColor(255, 255, 255, 160), 1))
                    painter.setBrush(Qt.NoBrush)
                    painter.drawEllipse(QPoint(ox, oy), DEADZONE, DEADZONE)

                    if abs(dy) > DEADZONE:
                        arrow_y = oy + (DEADZONE if dy > 0 else -DEADZONE)
                        tip_y = oy + (DEADZONE + 6 if dy > 0 else -DEADZONE - 6)
                        painter.setPen(QPen(QColor(255, 255, 255, 200), 2))
                        painter.drawLine(ox, arrow_y, ox, tip_y)
                        if dy > 0:
                            painter.drawLine(ox, tip_y, ox - 4, tip_y - 5)
                            painter.drawLine(ox, tip_y, ox + 4, tip_y - 5)
                        else:
                            painter.drawLine(ox, tip_y, ox - 4, tip_y + 5)
                            painter.drawLine(ox, tip_y, ox + 4, tip_y + 5)
                    return False

        return super().eventFilter(obj, event)

    def perform_auto_scroll(self):
        dy = self.current_mouse_pos.y() - self.auto_scroll_origin.y()
        DEADZONE = 8
        if abs(dy) <= DEADZONE:
            return
        speed = ((abs(dy) - DEADZONE) ** 1.4) * (0.08 if dy > 0 else -0.08)
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.value() + int(speed))
        self.setFocus()