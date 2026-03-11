import os
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QScrollArea,
    QPushButton, QComboBox, QHBoxLayout, QDialog
)
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt, QPoint, QEvent, QTimer, Signal, QObject

from progress_store import get_instance as get_progress_store


class ContinueDialog(QDialog):

    def __init__(self, chapter: str, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QPushButton
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
        from PySide6.QtWidgets import QLabel as _L
        msg = _L(
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


class ViewerPage(QWidget):

    def __init__(self, main_window):
        super().__init__()

        self.main_window           = main_window
        self.webtoon               = None
        self.current_chapter_index = 0
        self.progress_store        = get_progress_store()

        # Restore state: target image index + fraction within that image
        self._restore_image_index  = None
        self._restore_image_offset = 0.0

        self.loader = ImageLoader()
        self.loader.image_ready.connect(self._on_image_ready)

        main_layout = QVBoxLayout(self)

        top_bar = QHBoxLayout()
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

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)

        self.auto_scroll        = False
        self.auto_scroll_origin = QPoint()
        self.current_mouse_pos  = QPoint()

        self.scroll_timer = QTimer()
        self.scroll_timer.timeout.connect(self.perform_auto_scroll)

        # Debounce resize so rescale doesn't fire on every pixel change
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
        main_layout.addWidget(self.scroll)

        self.image_labels = []
        self.scroll.verticalScrollBar().valueChanged.connect(self.check_visible_images)

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
        """
        Snapshot the current scroll position as a packed float
        (image_index + fraction_within_image).
        Returns 0.0 if nothing is loaded yet.
        """
        if not self.image_labels:
            return 0.0

        scroll_top = self.scroll.verticalScrollBar().value()
        cumulative = 0
        for i, label in enumerate(self.image_labels):
            h = label.height()
            if cumulative + h > scroll_top:
                offset_px   = scroll_top - cumulative
                offset_frac = (offset_px / h) if h > 0 else 0.0
                return i + offset_frac
            cumulative += h

        # Scrolled past all images — clamp to last image
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

        # Require all images from 0 to idx (inclusive) to have real heights
        for i in range(idx + 1):
            lbl = self.image_labels[i]
            if lbl.pixmap() is None or lbl.pixmap().isNull():
                return  # still waiting — called again when next image loads

        self._jump_to_packed(idx, self._restore_image_offset)
        self._restore_image_index = None

    def _jump_to_packed(self, idx: int, offset_frac: float):
        """
        Scroll to image[idx] + offset_frac, forcing the scrollbar max
        high enough so the value isn't clamped by unloaded placeholder heights.
        """
        cumulative = sum(self.image_labels[i].height() for i in range(idx))
        target_px  = cumulative + int(self.image_labels[idx].height() * offset_frac)

        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

        bar = self.scroll.verticalScrollBar()
        if target_px > bar.maximum():
            bar.setMaximum(target_px)

        bar.setValue(target_px)

        # If still clamped, retry when more images load
        if bar.value() < target_px - 5:
            # Restore index is already cleared by caller only on success;
            # keep it alive so _on_image_ready retries.
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

        packed = 0.0
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

        # Only reset to top if we're NOT restoring a saved position
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

        QTimer.singleShot(50, self.check_visible_images)

        # Eagerly preload images around restore target
        if self._restore_image_index is not None:
            QTimer.singleShot(50, self._preload_restore_target)

    def _preload_restore_target(self):
        idx = self._restore_image_index
        if idx is None or idx >= len(self.image_labels):
            return
        vw  = self.scroll.viewport().width() // 2
        end = min(len(self.image_labels), idx + 3)
        for i in range(0, end):
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

        # Only attempt restore when an image at or before target finishes
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
        # Snapshot position before heights change
        packed = self._current_packed_position()
        idx    = int(packed)
        frac   = packed - idx

        for label in self.image_labels:
            self._apply_pixmap_to_label(label)

        # Re-apply position after rescale so the view stays on the same
        # content even if pixel heights changed due to the new viewport width.
        # Only do this if we have valid loaded images to jump to.
        if (self.image_labels
                and idx < len(self.image_labels)
                and hasattr(self.image_labels[idx], '_original_pixmap')):
            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()
            self._jump_to_packed(idx, frac)

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
    #  Resize — debounced                                                  #
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