import os
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QScrollArea,
    QPushButton,
    QComboBox,
    QHBoxLayout
)

from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt, QPoint, QEvent, QTimer, Signal, QObject


class ImageLoader(QObject):
    """Loads images in a background thread and emits when ready."""
    image_ready = Signal(int, QPixmap)

    def __init__(self):
        super().__init__()
        self.executor = ThreadPoolExecutor(max_workers=4)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def reset(self):
        self._cancelled = False

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

        self.main_window = main_window
        self.webtoon = None
        self.current_chapter_index = 0

        # Reusable background loader
        self.loader = ImageLoader()
        self.loader.image_ready.connect(self._on_image_ready)

        main_layout = QVBoxLayout(self)

        # ---- Top Bar ----
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

        # ---- Scroll Area ----
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.last_mouse_pos = QPoint()

        self.auto_scroll = False
        self.auto_scroll_origin = QPoint()
        self.current_mouse_pos = QPoint()

        self.scroll_timer = QTimer()
        self.scroll_timer.timeout.connect(self.perform_auto_scroll)
        self.scroll.viewport().installEventFilter(self)
        self.scroll.setMouseTracking(True)

        # Use a single container that we swap out on chapter change
        self.container = QWidget()
        self.image_layout = QVBoxLayout(self.container)
        self.image_layout.setSpacing(0)
        self.image_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll.setWidget(self.container)

        main_layout.addWidget(self.scroll)

        self.image_labels = []
        self.scroll.verticalScrollBar().valueChanged.connect(self.check_visible_images)

    # ------------------------------------------------------------------ #
    #  Loading
    # ------------------------------------------------------------------ #

    def load_webtoon(self, webtoon):
        webtoon.path = os.path.abspath(webtoon.path)
        self.webtoon = webtoon

        self.chapter_selector.blockSignals(True)
        self.chapter_selector.clear()
        self.chapter_selector.addItems(webtoon.chapters)
        self.chapter_selector.blockSignals(False)

        self.current_chapter_index = 0
        self.load_chapter_by_index(0)

    def load_selected_chapter(self, index):
        self.load_chapter_by_index(index)

    def load_chapter_by_index(self, index):
        if not self.webtoon:
            return

        self.current_chapter_index = index
        chapter = self.webtoon.chapters[index]

        self.chapter_selector.setCurrentIndex(index)
        self.load_chapter(chapter)
        self.update_nav_buttons()
        self.scroll.verticalScrollBar().setValue(0)

    def clear_images(self):
        """Fast clear: hide container, bulk-remove widgets, show again."""
        # Cancel any in-flight background loads
        self.loader.cancel()
        self.loader.reset()

        # Detach the container from the scroll area to avoid per-widget
        # layout recalculations while we delete children
        self.scroll.takeWidget()

        # Delete all child labels at once
        for label in self.image_labels:
            label.deleteLater()
        self.image_labels = []

        # Re-create a fresh container (faster than clearing layout)
        self.container = QWidget()
        self.image_layout = QVBoxLayout(self.container)
        self.image_layout.setSpacing(0)
        self.image_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll.setWidget(self.container)

    def load_chapter(self, chapter):
        self.clear_images()

        chapter_path = os.path.join(self.webtoon.path, chapter)
        images = sorted(
            f for f in os.listdir(chapter_path)
            if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        )

        # Build all placeholder labels in one pass — no pixmap loading yet
        for img in images:
            img_path = os.path.join(chapter_path, img)
            label = QLabel()
            label.img_path = img_path
            label.setAlignment(Qt.AlignCenter)
            label.setMinimumHeight(400)
            self.image_layout.addWidget(label)
            self.image_labels.append(label)

        # Let Qt do one layout pass, then start lazy loading
        QTimer.singleShot(50, self.check_visible_images)

    # ------------------------------------------------------------------ #
    #  Lazy / background image loading
    # ------------------------------------------------------------------ #

    def check_visible_images(self):
        viewport_top = self.scroll.verticalScrollBar().value()
        viewport_bottom = viewport_top + self.scroll.viewport().height()
        viewport_width = self.scroll.viewport().width() // 2

        for i, label in enumerate(self.image_labels):
            pos = label.mapTo(self.container, QPoint(0, 0)).y()
            height = label.height()

            # Outside the preload window — skip
            if pos + height < viewport_top - 800:
                continue
            if pos > viewport_bottom + 800:
                continue

            # Already loaded
            if label.pixmap() is not None and not label.pixmap().isNull():
                continue

            # Kick off background load
            self.loader.load(i, label.img_path, viewport_width)

    def _on_image_ready(self, index: int, pixmap: QPixmap):
        """Called on the main thread when a background load completes."""
        if index >= len(self.image_labels):
            return

        label = self.image_labels[index]
        label.setPixmap(pixmap)
        label.setMinimumHeight(0)

    # ------------------------------------------------------------------ #
    #  Navigation
    # ------------------------------------------------------------------ #

    def next_chapter(self):
        if self.current_chapter_index < len(self.webtoon.chapters) - 1:
            self.load_chapter_by_index(self.current_chapter_index + 1)

    def prev_chapter(self):
        if self.current_chapter_index > 0:
            self.load_chapter_by_index(self.current_chapter_index - 1)

    def update_nav_buttons(self):
        self.prev_button.setEnabled(self.current_chapter_index > 0)
        self.next_button.setEnabled(
            self.current_chapter_index < len(self.webtoon.chapters) - 1
        )

    def go_back(self):
        self.main_window.stack.setCurrentWidget(self.main_window.library)

    # ------------------------------------------------------------------ #
    #  Resize / rescale
    # ------------------------------------------------------------------ #

    def resizeEvent(self, event):
        self.rescale_images()
        super().resizeEvent(event)

    def rescale_images(self):
        viewport_width = self.scroll.viewport().width() // 2

        for label in self.image_labels:
            pixmap = label.pixmap()
            if pixmap is None or pixmap.isNull():
                continue
            scaled = pixmap.scaledToWidth(viewport_width, Qt.SmoothTransformation)
            label.setPixmap(scaled)

    # ------------------------------------------------------------------ #
    #  Middle-click auto scroll
    # ------------------------------------------------------------------ #

    def eventFilter(self, obj, event):
        if obj == self.scroll.viewport():

            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.MiddleButton:
                self.auto_scroll = not self.auto_scroll
                if self.auto_scroll:
                    self.auto_scroll_origin = event.pos()
                    self.current_mouse_pos = event.pos()
                    self.scroll_timer.start(16)
                else:
                    self.scroll_timer.stop()
                return True

            if event.type() == QEvent.MouseMove and self.auto_scroll:
                self.current_mouse_pos = event.pos()
                return True

            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton and self.auto_scroll:
                self.auto_scroll = False
                self.scroll_timer.stop()

        return super().eventFilter(obj, event)

    def perform_auto_scroll(self):
        delta = self.current_mouse_pos - self.auto_scroll_origin
        speed = delta.y() * 0.5
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.value() + int(speed))