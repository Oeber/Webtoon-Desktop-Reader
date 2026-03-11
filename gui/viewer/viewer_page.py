import os
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QScrollArea,
    QPushButton, QComboBox, QHBoxLayout, QDialog
)
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt, QPoint, QEvent, QTimer, Signal, QObject

from progress_store import ProgressStore


class ContinueDialog(QDialog):

    def __init__(self, chapter: str, scroll_pct: float, parent=None):
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

        pct_str = f"{int(scroll_pct * 100)}%"
        msg = QLabel(
            f"You were <b>{pct_str}</b> through <b>{chapter}</b>.<br>"
            "Would you like to continue from where you left off?"
        )
        msg.setWordWrap(True)
        msg.setTextFormat(Qt.RichText)
        layout.addWidget(msg)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        restart_btn = QPushButton("Start over")
        restart_btn.setStyleSheet("""
            QPushButton { background: #2e2e2e; color: #ccc; }
            QPushButton:hover { background: #3a3a3a; }
        """)
        restart_btn.clicked.connect(self.reject)

        continue_btn = QPushButton("Continue")
        continue_btn.setStyleSheet("""
            QPushButton { background: #2979ff; color: #fff; }
            QPushButton:hover { background: #448aff; }
        """)
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
        self.progress_store        = ProgressStore()

        self._pending_scroll_pct = 0.0
        self._scroll_restored    = True

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

    def load_webtoon(self, webtoon, start_chapter: int = 0, start_scroll: float = 0.0):
        webtoon.path = os.path.abspath(webtoon.path)
        self.webtoon = webtoon
        self._set_pending_scroll(start_scroll)

        self.chapter_selector.blockSignals(True)
        self.chapter_selector.clear()
        self.chapter_selector.addItems(webtoon.chapters)
        self.chapter_selector.blockSignals(False)

        self.current_chapter_index = start_chapter
        self._load_chapter_no_prompt(start_chapter)

    def _set_pending_scroll(self, pct: float):
        self._pending_scroll_pct = pct
        self._scroll_restored    = (pct < 0.005)

    def load_selected_chapter(self, index):
        self._load_chapter_with_prompt(index)

    def _load_chapter_with_prompt(self, index):
        if not self.webtoon:
            return

        chapter  = self.webtoon.chapters[index]
        progress = self.progress_store.get(self.webtoon.name)

        scroll_pct = 0.0
        if progress and progress.get("chapter") == chapter:
            saved_pct = progress.get("scroll", 0.0)
            if saved_pct > 0.01:
                dlg = ContinueDialog(chapter, saved_pct, parent=self)
                if dlg.exec() == QDialog.Accepted:
                    scroll_pct = saved_pct

        self._set_pending_scroll(scroll_pct)
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
        images = sorted(
            f for f in os.listdir(chapter_path)
            if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        )

        for img in images:
            img_path = os.path.join(chapter_path, img)
            label    = QLabel()
            label.img_path  = img_path
            label.loaded    = False
            label.setAlignment(Qt.AlignCenter)
            label.setMinimumHeight(400)
            self.image_layout.addWidget(label)
            self.image_labels.append(label)

        # Kick off initial visible load + preload images near the target position
        QTimer.singleShot(50, self._initial_load)

    def _initial_load(self):
        """Load images around both the top AND the target scroll position."""
        self.check_visible_images()

        if not self._scroll_restored and self._pending_scroll_pct > 0.005:
            self._preload_around_target()

    def _preload_around_target(self):
        """
        Estimate which image index corresponds to the saved percentage,
        then eagerly load a window of images around that index so the
        container grows tall enough to scroll there.
        """
        n = len(self.image_labels)
        if n == 0:
            return

        target_idx = int(self._pending_scroll_pct * n)
        start = max(0, target_idx - 5)
        end   = min(n, target_idx + 10)

        viewport_width = self.scroll.viewport().width() // 2

        for i in range(start, end):
            label = self.image_labels[i]
            if label.pixmap() is not None and not label.pixmap().isNull():
                continue
            self.loader.load(i, label.img_path, viewport_width)

    def check_visible_images(self):
        viewport_top    = self.scroll.verticalScrollBar().value()
        viewport_bottom = viewport_top + self.scroll.viewport().height()
        viewport_width  = self.scroll.viewport().width() // 2

        for i, label in enumerate(self.image_labels):
            pos    = label.mapTo(self.container, QPoint(0, 0)).y()
            height = label.height()

            if pos + height < viewport_top - 800:
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
        label.setPixmap(pixmap)
        label.setMinimumHeight(0)
        label.loaded = True

        if not self._scroll_restored:
            self._try_restore_scroll()

    def _try_restore_scroll(self):
        """
        Attempt to jump to the saved percentage. We need enough images
        loaded around the target so the container is tall enough.
        Strategy: check how many images near the target index are loaded.
        Once we have a critical mass, apply the scroll.
        """
        n = len(self.image_labels)
        if n == 0:
            return

        target_idx = int(self._pending_scroll_pct * n)
        start = max(0, target_idx - 3)
        end   = min(n, target_idx + 5)

        loaded_near_target = sum(
            1 for i in range(start, end)
            if self.image_labels[i].loaded
        )

        needed = min(4, end - start)
        if loaded_near_target < needed:
            return

        bar     = self.scroll.verticalScrollBar()
        max_val = bar.maximum()
        if max_val < 100:
            return

        bar.setValue(int(max_val * self._pending_scroll_pct))
        self._scroll_restored = True

    def _scroll_percentage(self) -> float:
        bar = self.scroll.verticalScrollBar()
        if bar.maximum() == 0:
            return 0.0
        return bar.value() / bar.maximum()

    def _save_progress(self):
        if not self.webtoon:
            return
        chapter = self.webtoon.chapters[self.current_chapter_index]
        self.progress_store.save(self.webtoon.name, chapter, self._scroll_percentage())

    def next_chapter(self):
        if self.current_chapter_index < len(self.webtoon.chapters) - 1:
            self._save_progress()
            self._set_pending_scroll(0.0)
            self._load_chapter_no_prompt(self.current_chapter_index + 1)

    def prev_chapter(self):
        if self.current_chapter_index > 0:
            self._save_progress()
            self._set_pending_scroll(0.0)
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
        self.rescale_images()
        super().resizeEvent(event)

    def rescale_images(self):
        viewport_width = self.scroll.viewport().width() // 2
        for label in self.image_labels:
            pixmap = label.pixmap()
            if pixmap is None or pixmap.isNull():
                continue
            label.setPixmap(pixmap.scaledToWidth(viewport_width, Qt.SmoothTransformation))

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