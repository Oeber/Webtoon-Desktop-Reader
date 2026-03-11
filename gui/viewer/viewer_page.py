import os
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QScrollArea,
    QPushButton, QComboBox, QHBoxLayout, QDialog, QApplication
)
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QBrush
from PySide6.QtCore import Qt, QPoint, QEvent, QTimer, Signal, QObject, QRect

from progress_store import get_instance as get_progress_store

PREVIEW_WIDTH  = 80     # total width of the filmstrip panel
SCRUBBER_WIDTH = 6      # width of the position line column on the right
TILE_AREA_W    = PREVIEW_WIDTH - SCRUBBER_WIDTH  # width available for tiles
TILE_HEIGHT    = 100    # fixed height of each tile
TILE_GAP       = 3      # gap between tiles
TILE_PADDING   = 3      # horizontal inset for tiles within the tile area


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
        self.executor   = ThreadPoolExecutor(max_workers=4)
        self._cancelled = False

    def cancel(self): self._cancelled = True
    def reset(self):  self._cancelled = False

    def load(self, index, path, width):
        self.executor.submit(self._load_task, index, path, width)

    def _load_task(self, index, path, width):
        if self._cancelled:
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return
        scaled = pixmap.scaledToWidth(width, Qt.SmoothTransformation)
        if not self._cancelled:
            self.image_ready.emit(index, scaled)


class ChapterPreview(QWidget):
    """
    Filmstrip panel on the right side of the viewer.

    Left column (TILE_AREA_W px): one fixed-height tile per image.
      - Loaded images show a center-cropped thumbnail.
      - Unloaded images show a dark placeholder.
      - The tile containing the viewport top is highlighted.

    Right column (SCRUBBER_WIDTH px): thin position line.
      - Shows exact proportional scroll position within the chapter.
      - Small handle indicates current position.
      - Click or drag to scrub.

    Clicking a tile jumps to that image.
    """

    def __init__(self, scroll_area: QScrollArea, parent=None):
        super().__init__(parent)
        self.scroll_area  = scroll_area
        self.image_labels = []
        self.setFixedWidth(PREVIEW_WIDTH)
        self.setCursor(Qt.PointingHandCursor)
        self._dragging_scrubber = False

    def set_image_labels(self, labels: list):
        self.image_labels = labels
        self.update()

    def notify_image_loaded(self):
        self.update()

    # ------------------------------------------------------------------ #
    #  Geometry helpers                                                    #
    # ------------------------------------------------------------------ #

    def _tile_rect(self, index: int) -> QRect:
        """Returns the rect for tile[index] in widget coordinates."""
        y = index * (TILE_HEIGHT + TILE_GAP)
        return QRect(TILE_PADDING, y, TILE_AREA_W - TILE_PADDING * 2, TILE_HEIGHT)

    def _total_tile_height(self) -> int:
        n = len(self.image_labels)
        return n * TILE_HEIGHT + max(0, n - 1) * TILE_GAP

    def _current_image_index(self) -> int:
        """Which image contains the viewport top."""
        if not self.image_labels:
            return 0
        bar        = self.scroll_area.verticalScrollBar()
        scroll_top = bar.value()
        cumulative = 0
        for i, label in enumerate(self.image_labels):
            h = label.height()
            if cumulative + h > scroll_top:
                return i
            cumulative += h
        return len(self.image_labels) - 1

    def _scroll_fraction(self) -> float:
        """Current scroll position as 0.0–1.0 fraction of total content."""
        bar = self.scroll_area.verticalScrollBar()
        max_val = bar.maximum()
        return bar.value() / max_val if max_val > 0 else 0.0

    def _scrubber_rect(self) -> QRect:
        """The thin column on the right used for the position line."""
        return QRect(TILE_AREA_W, 0, SCRUBBER_WIDTH, self.height())

    def _handle_y(self) -> int:
        frac = self._scroll_fraction()
        return int(frac * self.height())

    # ------------------------------------------------------------------ #
    #  Paint                                                               #
    # ------------------------------------------------------------------ #

    def paintEvent(self, event):
        if not self.image_labels:
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor("#1a1a1a"))
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.Antialiasing)

        # Background
        painter.fillRect(self.rect(), QColor("#1a1a1a"))

        current_idx = self._current_image_index()
        tile_w      = TILE_AREA_W - TILE_PADDING * 2

        for i, label in enumerate(self.image_labels):
            rect = self._tile_rect(i)

            # Skip tiles entirely outside the visible widget area
            if rect.bottom() < 0 or rect.top() > self.height():
                continue

            src = getattr(label, '_original_pixmap', None)
            if src and not src.isNull():
                # Center-crop the source pixmap to fill the tile
                src_w, src_h = src.width(), src.height()
                scale        = max(tile_w / src_w, TILE_HEIGHT / src_h)
                scaled_w     = int(src_w * scale)
                scaled_h     = int(src_h * scale)
                crop_x       = (scaled_w - tile_w) // 2
                crop_y       = (scaled_h - TILE_HEIGHT) // 2
                src_crop     = QRect(
                    int(crop_x / scale), int(crop_y / scale),
                    int(tile_w / scale), int(TILE_HEIGHT / scale)
                )
                painter.drawPixmap(rect, src, src_crop)
            else:
                painter.fillRect(rect, QColor("#2a2a2a"))
                # Subtle image number hint
                painter.setPen(QColor("#444"))
                painter.drawText(rect, Qt.AlignCenter, str(i + 1))

            # Highlight border + tint for current tile
            if i == current_idx:
                painter.fillRect(rect, QColor(41, 121, 255, 45))
                pen = QPen(QColor(41, 121, 255, 230))
                pen.setWidth(2)
                painter.setPen(pen)
                painter.drawRect(rect.adjusted(1, 1, -1, -1))
            else:
                # Subtle separator
                painter.setPen(QColor("#111"))
                painter.drawLine(rect.left(), rect.bottom() + 1,
                                 rect.right(), rect.bottom() + 1)

        # ---- Scrubber column ----
        scrub = self._scrubber_rect()
        painter.fillRect(scrub, QColor("#111"))

        # Track line
        track_x = scrub.left() + scrub.width() // 2
        painter.setPen(QPen(QColor("#333"), 1))
        painter.drawLine(track_x, 0, track_x, self.height())

        # Handle
        hy = self._handle_y()
        handle_rect = QRect(scrub.left() + 1, hy - 5, scrub.width() - 2, 10)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(41, 121, 255, 220)))
        painter.drawRoundedRect(handle_rect, 2, 2)

    # ------------------------------------------------------------------ #
    #  Scroll helpers                                                      #
    # ------------------------------------------------------------------ #

    def _jump_to_image(self, index: int):
        """Scroll the main view to the top of image[index]."""
        if not self.image_labels or index >= len(self.image_labels):
            return
        cumulative = sum(self.image_labels[i].height() for i in range(index))
        bar = self.scroll_area.verticalScrollBar()
        bar.setValue(max(0, min(cumulative, bar.maximum())))

    def _scrub_to_y(self, widget_y: int):
        """Set scroll position proportional to widget_y within the scrubber."""
        frac = max(0.0, min(1.0, widget_y / self.height()))
        bar  = self.scroll_area.verticalScrollBar()
        bar.setValue(int(frac * bar.maximum()))

    def _tile_index_at(self, pos: QPoint) -> int | None:
        """Return the tile index at widget position pos, or None."""
        x, y = pos.x(), pos.y()
        if x >= TILE_AREA_W:
            return None
        index = y // (TILE_HEIGHT + TILE_GAP)
        if index < 0 or index >= len(self.image_labels):
            return None
        # Make sure click lands inside the tile rect, not in the gap
        tile_top = index * (TILE_HEIGHT + TILE_GAP)
        if y > tile_top + TILE_HEIGHT:
            return None
        return index

    # ------------------------------------------------------------------ #
    #  Mouse events                                                        #
    # ------------------------------------------------------------------ #

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        pos = event.pos()
        if pos.x() >= TILE_AREA_W:
            self._dragging_scrubber = True
            self._scrub_to_y(pos.y())
        else:
            idx = self._tile_index_at(pos)
            if idx is not None:
                self._jump_to_image(idx)

    def mouseMoveEvent(self, event):
        if self._dragging_scrubber:
            self._scrub_to_y(event.pos().y())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging_scrubber = False


class ViewerPage(QWidget):

    def __init__(self, main_window):
        super().__init__()

        self.main_window           = main_window
        self.webtoon               = None
        self.current_chapter_index = 0
        self.progress_store        = get_progress_store()

        self._restore_image_index  = None
        self._restore_image_offset = 0.0

        self.loader = ImageLoader()
        self.loader.image_ready.connect(self._on_image_ready)

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
        self.progress_store.save(self.webtoon.name, chapter, packed)

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
    #  Chapter loading                                                     #
    # ------------------------------------------------------------------ #

    def load_selected_chapter(self, index):
        self._load_chapter_with_prompt(index)

    def _load_chapter_with_prompt(self, index):
        if not self.webtoon:
            return
        chapter  = self.webtoon.chapters[index]
        progress = self.progress_store.get(self.webtoon.name)
        packed   = 0.0
        if progress and progress.get("chapter") == chapter:
            saved = progress.get("scroll", 0.0)
            if saved > 0.005:
                dlg = ContinueDialog(chapter, parent=self)
                if dlg.exec() == QDialog.Accepted:
                    packed = saved
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

            if pos + h < viewport_top - 800:
                continue
            if pos > viewport_bottom + 800:
                continue
            if label.pixmap() is not None and not label.pixmap().isNull():
                continue

            self.loader.load(i, label.img_path, viewport_width)

    def _on_image_ready(self, index: int, pixmap: QPixmap):
        if index >= len(self.image_labels):
            return
        label = self.image_labels[index]
        label._original_pixmap = pixmap
        self._apply_pixmap_to_label(label)
        self.preview.notify_image_loaded()

        if (self._restore_image_index is not None
                and index <= self._restore_image_index):
            self._apply_restore()

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

        for label in self.image_labels:
            self._apply_pixmap_to_label(label)

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