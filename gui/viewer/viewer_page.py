import os
import re
import time
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor

from core.app_logging import get_logger
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QScrollArea,
    QPushButton, QComboBox, QHBoxLayout, QDialog, QSlider, QMessageBox
)
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QImage, QImageReader
from PySide6.QtCore import Qt, QPoint, QEvent, QEventLoop, QTimer, Signal, QObject, QRect, QSize

from gui.common.styles import (
    VIEWER_RESUME_CONTINUE_BUTTON_STYLE,
    VIEWER_RESUME_DIALOG_STYLE,
    VIEWER_RESUME_RESTART_BUTTON_STYLE,
    VIEWER_ZOOM_BUTTON_STYLE,
    VIEWER_ZOOM_LABEL_STYLE,
)
from gui.downloader.download_widgets import SpinnerCircle
from stores.progress_store import get_instance as get_progress_store
from stores.webtoon_settings_store import get_instance as get_webtoon_settings
from gui.settings.settings_page import load_setting, save_setting

FILMSTRIP_W   = 40
IMAGE_STRIP_W = 50
PREVIEW_W     = FILMSTRIP_W + IMAGE_STRIP_W

# Matches chapter names that contain a decimal sub-number, e.g. "Chapter 1.5"
_SPECIAL_CHAPTER_RE = re.compile(r'\b\d+\.\d+\b')

TILE_GAP      = 2
TILE_PADDING  = 2
TILE_MIN_H    = 14
TILE_MAX_H    = 120

LAZY_WINDOW   = 2000
BATCH_MS      = 16
NUM_WORKERS   = 8
PREVIEW_WORKERS = 2
PANEL_WORKERS = 1
PREVIEW_EAGER_COUNT = 4
PREVIEW_BATCH_SIZE = 16
PREVIEW_BATCH_MS = 24
SUPPORTED_VIEWER_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".avif")
logger = get_logger(__name__)


class ContinueDialog(QDialog):

    def __init__(self, chapter: str, parent=None):
        super().__init__(parent)
        self.choice = "cancel"
        self.setWindowTitle("Resume reading?")
        self.setModal(True)
        self.setFixedWidth(360)
        self.setStyleSheet(VIEWER_RESUME_DIALOG_STYLE)
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
        restart_btn.setStyleSheet(VIEWER_RESUME_RESTART_BUTTON_STYLE)
        restart_btn.clicked.connect(self._start_over)
        continue_btn = QPushButton("Continue")
        continue_btn.setStyleSheet(VIEWER_RESUME_CONTINUE_BUTTON_STYLE)
        continue_btn.clicked.connect(self._continue)
        btn_layout.addWidget(restart_btn)
        btn_layout.addWidget(continue_btn)
        layout.addLayout(btn_layout)

    def _start_over(self):
        self.choice = "restart"
        self.accept()

    def _continue(self):
        self.choice = "continue"
        self.accept()


def _format_bytes(size: int) -> str:
    units = ("B", "KB", "MB", "GB")
    value = float(max(0, int(size)))
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} GB"


class ImageLoader(QObject):
    image_ready = Signal(int, QPixmap)
    preview_ready = Signal(int, QPixmap, int, int)  # index, thumb, natural_width, natural_height
    panel_starts_ready = Signal(int, list)

    def __init__(self):
        super().__init__()
        self.executor = ThreadPoolExecutor(max_workers=NUM_WORKERS)
        self.preview_executor = ThreadPoolExecutor(max_workers=PREVIEW_WORKERS)
        self.panel_executor = ThreadPoolExecutor(max_workers=PANEL_WORKERS)
        self._cancelled = False
        self._queued = set()
        self._preview_queued = set()
        self._panel_break_cache: dict[str, list[float]] = {}

    def cancel(self):
        self._cancelled = True
        self._queued.clear()
        self._preview_queued.clear()

    def reset(self):
        self._cancelled = False

    def shutdown(self):
        self.cancel()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.preview_executor.shutdown(wait=False, cancel_futures=True)
        self.panel_executor.shutdown(wait=False, cancel_futures=True)

    def load(self, index: int, path: str, width: int):
        if index in self._queued:
            return
        self._queued.add(index)
        self.executor.submit(self._load_task, index, path)

    def load_preview(self, index: int, path: str, max_w: int = 50):
        """Load a small thumbnail for the preview strip only."""
        if index in self._preview_queued or index in self._queued:
            return
        self._preview_queued.add(index)
        self.preview_executor.submit(self._preview_task, index, path, max_w)

    def _load_task(self, index: int, path: str):
        if self._cancelled:
            return
        started = time.perf_counter()
        reader = QImageReader(path)
        image = reader.read()
        pixmap = QPixmap.fromImage(image) if not image.isNull() else QPixmap()
        if pixmap.isNull():
            logger.warning("Viewer image load failed index=%d path=%s", index, path)
            return
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        try:
            file_size = os.path.getsize(path)
        except OSError:
            file_size = 0
        logger.info(
            "Viewer image loaded index=%d path=%s dims=%dx%d file_size=%s load_ms=%.2f",
            index,
            path,
            pixmap.width(),
            pixmap.height(),
            _format_bytes(file_size),
            elapsed_ms,
        )
        if not self._cancelled:
            self.image_ready.emit(index, pixmap)

    def _preview_task(self, index: int, path: str, max_w: int):
        if self._cancelled:
            return
        natural_w = 0
        natural_h = 0
        thumb = QPixmap()

        reader = QImageReader(path)
        size = reader.size()
        if size.isValid():
            natural_w = size.width()
            natural_h = size.height()
            if natural_w > max_w > 0 and natural_h > 0:
                scaled_h = max(1, int(max_w * (natural_h / natural_w)))
                reader.setScaledSize(QSize(max_w, scaled_h))

            image = reader.read()
            if not image.isNull():
                thumb = QPixmap.fromImage(image)

        if thumb.isNull():
            pixmap = QPixmap(path)
            if pixmap.isNull():
                return
            natural_w = pixmap.width()
            natural_h = pixmap.height()
            thumb = pixmap if natural_w <= max_w else pixmap.scaledToWidth(max_w, Qt.SmoothTransformation)

        if not self._cancelled:
            self.preview_ready.emit(index, thumb, natural_w, natural_h)

    def build_panel_starts(self, generation: int, payload: list):
        self.panel_executor.submit(self._panel_task, generation, payload)

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

            break_fractions = self._panel_break_cache.get(path)
            if break_fractions is None:
                break_fractions = self._compute_panel_break_fractions(img, MIN_BLANK=MIN_BLANK, ROW_STEP=ROW_STEP)
                self._panel_break_cache[path] = break_fractions

            for fraction in break_fractions:
                starts.append(cumulative + int(fraction * h))

            cumulative += h

        if not self._cancelled:
            self.panel_starts_ready.emit(generation, sorted(set(starts)))

    def _compute_panel_break_fractions(self, image: QImage, MIN_BLANK: int, ROW_STEP: int) -> list[float]:
        ih = image.height()
        if ih <= 0:
            return []

        starts = []
        in_blank = self._is_blank_row(image, 0)
        blank_run = 0

        for src_y in range(ROW_STEP, ih, ROW_STEP):
            is_blank = self._is_blank_row(image, src_y)

            if is_blank:
                blank_run += ROW_STEP
            else:
                if in_blank and blank_run >= MIN_BLANK:
                    starts.append(src_y / ih)
                blank_run = 0

            in_blank = is_blank

        return starts

    def _is_blank_row(self, image: QImage, y: int, sample_step: int = 12) -> bool:
        w = image.width()
        if w <= 0:
            return True

        step = max(sample_step, w // 160)
        total = 0
        total_sq = 0
        count = 0

        for x in range(0, w, step):
            rgb = image.pixel(x, y)
            red = (rgb >> 16) & 0xFF
            green = (rgb >> 8) & 0xFF
            blue = rgb & 0xFF
            lum = (299 * red + 587 * green + 114 * blue) // 255
            total += lum
            total_sq += lum * lum
            count += 1

        if count == 0:
            return True

        avg = total / count
        variance = (total_sq / count) - (avg * avg)

        is_extreme = avg < 120 or avg > 880
        is_uniform = variance < 3000
        return is_extreme and is_uniform


class ChapterPreview(QWidget):

    def __init__(self, scroll_area: QScrollArea, metrics_provider=None, parent=None):
        super().__init__(parent)
        self.scroll_area = scroll_area
        self.metrics_provider = metrics_provider
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

    def _scaled_label_height(self, label) -> int:
        if self.metrics_provider is not None:
            return self.metrics_provider.scaled_label_height(label)
        natural_w = getattr(label, '_natural_width', 0)
        natural_h = getattr(label, '_natural_height', 0)
        image_width = max(1, int(self.scroll_area.viewport().width() * self._zoom))
        if natural_w > 0 and natural_h > 0:
            return max(1, int(image_width * (natural_h / natural_w)))
        return max(1, label.height())

    def _total_content_height(self) -> int:
        if self.metrics_provider is not None:
            return self.metrics_provider.total_content_height()
        return sum(self._scaled_label_height(label) for label in self.image_labels)

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
        if self.metrics_provider is not None:
            return self.metrics_provider.image_index_at_offset(scroll_top)
        cumulative = 0
        for i, label in enumerate(self.image_labels):
            h = self._scaled_label_height(label)
            if cumulative + h > scroll_top:
                return i
            cumulative += h
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
            src = getattr(label, '_preview_pixmap', None) or getattr(label, '_source_pixmap', None)
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

    def _coverage(self, total_content_h: int, view_h: int) -> float:
        return 0.20

    def _window_fracs(self, total_content_h: int, view_h: int):
        """Return (coverage, window_top_frac) for the current scroll position."""
        bar = self.scroll_area.verticalScrollBar()
        scroll_max = max(1, bar.maximum())
        coverage = self._coverage(total_content_h, view_h)
        scroll_frac = bar.value() / scroll_max
        window_top_frac = scroll_frac * (1.0 - coverage)
        window_bot_frac = window_top_frac + coverage
        if window_bot_frac > 1.0:
            window_bot_frac = 1.0
            window_top_frac = 1.0 - coverage
        window_top_frac = max(0.0, window_top_frac)
        return coverage, window_top_frac

    def _paint_image_strip(self, painter: QPainter, current_idx: int):
        strip_x = FILMSTRIP_W
        strip_w = IMAGE_STRIP_W
        strip_h = self.height()
        strip_rect = QRect(strip_x, 0, strip_w, strip_h)

        total_content_h = self._total_content_height()
        if total_content_h == 0:
            painter.fillRect(strip_rect, QColor("#2a2a2a"))
            return

        scroll_top = self.scroll_area.verticalScrollBar().value()
        view_h = self.scroll_area.viewport().height()

        coverage, window_top_frac = self._window_fracs(total_content_h, view_h)
        window_bot_frac = window_top_frac + coverage

        content_top = window_top_frac * total_content_h
        content_bot = window_bot_frac * total_content_h

        painter.save()
        painter.setClipRect(strip_rect)

        cumulative = 0
        for lbl in self.image_labels:
            img_h = self._scaled_label_height(lbl)
            img_top = cumulative
            img_bot = cumulative + img_h
            cumulative += img_h

            if img_bot <= content_top or img_top >= content_bot:
                continue

            src = getattr(lbl, '_preview_pixmap', None) or getattr(lbl, '_source_pixmap', None)

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
        if self.metrics_provider is not None:
            cumulative = self.metrics_provider.cumulative_height_before(index)
        else:
            cumulative = sum(self._scaled_label_height(self.image_labels[i]) for i in range(index))
        bar = self.scroll_area.verticalScrollBar()
        bar.setValue(max(0, min(cumulative, bar.maximum())))

    def _scrub_strip_to_y(self, widget_y: int):
        if not self.image_labels:
            return
        total_content_h = self._total_content_height()
        if total_content_h == 0:
            return

        bar = self.scroll_area.verticalScrollBar()
        view_h = self.scroll_area.viewport().height()
        scroll_max = max(1, bar.maximum())

        coverage, window_top_frac = self._window_fracs(total_content_h, view_h)

        # Exact inverse of the indicator_top formula in _paint_image_strip.
        # Subtract view_h // 2 so the clicked position lands at viewport center.
        click_frac = max(0.0, min(1.0, widget_y / self.height()))
        target = int((click_frac * coverage + window_top_frac) * total_content_h) - view_h // 2
        bar.setValue(max(0, min(target, scroll_max)))

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
    chapter_loading_started = Signal(str, str)
    chapter_loading_finished = Signal(str, str)

    def __init__(self, main_window):
        super().__init__()
        self.setFocusPolicy(Qt.StrongFocus)

        self.main_window = main_window
        self.webtoon = None
        self.current_chapter_index = 0
        self.progress_store = get_progress_store()
        self.settings_store = get_webtoon_settings()

        self._restore_image_index = None
        self._restore_image_offset = 0.0
        self._resize_packed = None
        self._resize_anchor_px = 0

        self._pending_batch: dict[int, QPixmap] = {}
        self._chapter_image_cache: dict[str, tuple[int, list[str]]] = {}
        self._chapter_image_info_cache: dict[str, tuple[int, list[tuple[str, int, int, int]]]] = {}
        self._queued_preview_indexes: set[int] = set()
        self._pending_preview_queue: list[int] = []
        self._label_heights: list[int] = []
        self._prefix_heights: list[int] = [0]

        self.loader = ImageLoader()
        self.loader.image_ready.connect(self._on_image_ready)
        self.loader.preview_ready.connect(self._on_preview_ready)
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

        self._preview_timer = QTimer()
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(PREVIEW_BATCH_MS)
        self._preview_timer.timeout.connect(self._drain_preview_queue)

        self._zoom_persist_timer = QTimer()
        self._zoom_persist_timer.setSingleShot(True)
        self._zoom_persist_timer.setInterval(250)
        self._zoom_persist_timer.timeout.connect(self._persist_zoom_override_now)

        self._zoom = load_setting("viewer_zoom", 0.5)
        self.auto_skip_enabled = load_setting("viewer_auto_skip", True)
        self.skip_specials_enabled = False
        self._zoom_override_active = False  # True when this webtoon has a saved override
        # Maps selector combo index → real webtoon.chapters index (used when skip_specials is on)
        self._chapter_index_map: list[int] = []

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(6, 6, 6, 6)

        self.back_button = QPushButton("Back")
        self.back_button.setFocusPolicy(Qt.NoFocus)
        self.back_button.clicked.connect(self.go_back)

        self.prev_button = QPushButton("Previous Chapter")
        self.prev_button.setFocusPolicy(Qt.NoFocus)
        self.prev_button.clicked.connect(self.prev_chapter)

        self.next_button = QPushButton("Next Chapter")
        self.next_button.setFocusPolicy(Qt.NoFocus)
        self.next_button.clicked.connect(self.next_chapter)

        self.chapter_selector = QComboBox()
        self.chapter_selector.setFocusPolicy(Qt.NoFocus)
        self.chapter_selector.currentIndexChanged.connect(self.load_selected_chapter)

        self.nav_toggle = QPushButton("Auto Skip")
        self.nav_toggle.setCheckable(True)
        self.nav_toggle.setChecked(self.auto_skip_enabled)

        if not self.auto_skip_enabled:
            self.nav_toggle.setText("Standard")
        self.nav_toggle.setFocusPolicy(Qt.NoFocus)
        self.nav_toggle.clicked.connect(self._toggle_navigation_mode)

        zoom_out_btn = QPushButton("−")
        zoom_out_btn.setFixedWidth(28)
        zoom_out_btn.setFocusPolicy(Qt.NoFocus)
        zoom_out_btn.setToolTip("Decrease image width")
        zoom_out_btn.clicked.connect(self._zoom_out)

        zoom_in_btn = QPushButton("+")
        zoom_in_btn.setFixedWidth(28)
        zoom_in_btn.setFocusPolicy(Qt.NoFocus)
        zoom_in_btn.setToolTip("Increase image width")
        zoom_in_btn.clicked.connect(self._zoom_in)

        self._zoom_slider = QSlider(Qt.Horizontal)
        self._zoom_slider.setFixedWidth(100)
        self._zoom_slider.setMinimum(25)
        self._zoom_slider.setMaximum(100)
        self._zoom_slider.setValue(int(self._zoom * 100))
        self._zoom_slider.setFocusPolicy(Qt.NoFocus)
        self._zoom_slider.setToolTip("Image width")
        self._zoom_slider.valueChanged.connect(self._on_zoom_slider)

        self._zoom_label = QLabel(f"{int(self._zoom * 100)}%")
        self._zoom_label.setFixedWidth(36)
        self._zoom_label.setAlignment(Qt.AlignCenter)
        self._zoom_label.setStyleSheet(VIEWER_ZOOM_LABEL_STYLE)

        self._zoom_reset_btn = QPushButton("Reset zoom")
        self._zoom_reset_btn.setFocusPolicy(Qt.NoFocus)
        self._zoom_reset_btn.setToolTip("Remove webtoon zoom override and use global default")
        self._zoom_reset_btn.setStyleSheet(VIEWER_ZOOM_BUTTON_STYLE)
        self._zoom_reset_btn.setEnabled(False)  # enabled only when an override is active
        self._zoom_reset_btn.clicked.connect(self._clear_zoom_override)

        top_bar.addWidget(self.back_button)
        top_bar.addWidget(self.prev_button)
        top_bar.addWidget(self.next_button)
        top_bar.addWidget(self.chapter_selector)
        top_bar.addWidget(self.nav_toggle)
        top_bar.addStretch()
        top_bar.addWidget(zoom_out_btn)
        top_bar.addWidget(self._zoom_slider)
        top_bar.addWidget(zoom_in_btn)
        top_bar.addWidget(self._zoom_label)
        top_bar.addSpacing(8)
        top_bar.addWidget(self._zoom_reset_btn)
        main_layout.addLayout(top_bar)

        content_row = QHBoxLayout()
        content_row.setContentsMargins(0, 0, 0, 0)
        content_row.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.preview = ChapterPreview(self.scroll, metrics_provider=self)

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
        self._label_pool: list[QLabel] = []
        self._chapter_load_total = 0
        self._chapter_load_loaded = 0
        self._chapter_loading_active = False

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

        self.loading_overlay = QWidget(self.scroll.viewport())
        self.loading_overlay.setStyleSheet("background-color: rgba(0, 0, 0, 150);")
        self.loading_overlay.hide()
        overlay_layout = QVBoxLayout(self.loading_overlay)
        overlay_layout.setContentsMargins(24, 24, 24, 24)
        overlay_layout.setSpacing(10)
        overlay_layout.setAlignment(Qt.AlignCenter)

        self.loading_spinner = SpinnerCircle(self.loading_overlay)
        self.loading_spinner.set_spinning()
        self.loading_label = QLabel("Loading chapter...")
        self.loading_label.setAlignment(Qt.AlignCenter)
        self.loading_label.setStyleSheet("color: #f2f2f2; font-size: 16px; font-weight: 600;")
        self.loading_detail_label = QLabel("")
        self.loading_detail_label.setAlignment(Qt.AlignCenter)
        self.loading_detail_label.setStyleSheet("color: #bdbdbd; font-size: 12px;")

        overlay_layout.addWidget(self.loading_spinner, 0, Qt.AlignCenter)
        overlay_layout.addWidget(self.loading_label)
        overlay_layout.addWidget(self.loading_detail_label)

    def load_webtoon(self, webtoon, start_chapter: int = 0, start_scroll: float = 0.0):
        logger.info(
            "Viewer loading webtoon=%s chapter_index=%d start_scroll=%.3f",
            webtoon.name,
            start_chapter,
            start_scroll,
        )
        webtoon.path = os.path.abspath(webtoon.path)
        self.webtoon = webtoon
        self._unpack_restore(start_scroll)
        self._apply_webtoon_settings(webtoon, rescale_existing=False)
        self._repopulate_chapter_selector()
        self.current_chapter_index = start_chapter
        self._load_chapter_no_prompt(start_chapter)

    def _apply_webtoon_settings(self, webtoon, rescale_existing: bool = True):
        """Apply per-webtoon settings (zoom, hide filler). Called whenever a webtoon is opened."""
        logger.info("Applying viewer settings for %s", webtoon.name)
        # Hide filler
        self.skip_specials_enabled = self.settings_store.get_hide_filler(webtoon.name)

        # Zoom override
        override = self.settings_store.get_zoom_override(webtoon.name)
        if override is not None:
            self._zoom_override_active = True
            self._set_zoom(override, rescale_existing=rescale_existing)
        else:
            self._zoom_override_active = False
            self._set_zoom(load_setting("viewer_zoom", 0.5), rescale_existing=rescale_existing)
        self._zoom_reset_btn.setEnabled(self._zoom_override_active)

    def _current_packed_position(self) -> float:
        return self._packed_position_at(self.scroll.verticalScrollBar().value())

    def _scaled_label_height(self, label, zoom: float | None = None) -> int:
        natural_w = getattr(label, '_natural_width', 0)
        natural_h = getattr(label, '_natural_height', 0)
        zoom = self._zoom if zoom is None else zoom
        if natural_w > 0 and natural_h > 0:
            image_width = max(1, int(self.scroll.viewport().width() * zoom))
            return max(1, int(image_width * (natural_h / natural_w)))
        return max(1, label.height())

    def scaled_label_height(self, label) -> int:
        return self._scaled_label_height(label)

    def _reset_layout_metrics(self):
        self._label_heights = []
        self._prefix_heights = [0]

    def _set_label_height_cache(self, index: int, height: int):
        if index < 0 or index >= len(self.image_labels):
            return
        height = max(1, int(height))
        if index >= len(self._label_heights):
            self._label_heights.extend([0] * (index + 1 - len(self._label_heights)))
        if self._label_heights[index] == height:
            return
        self._label_heights[index] = height
        self._rebuild_prefix_heights()

    def _rebuild_prefix_heights(self):
        prefix = [0]
        running = 0
        for height in self._label_heights:
            running += max(1, int(height))
            prefix.append(running)
        self._prefix_heights = prefix

    def cumulative_height_before(self, index: int) -> int:
        if index <= 0:
            return 0
        if index < len(self._prefix_heights):
            return self._prefix_heights[index]
        return self._prefix_heights[-1]

    def total_content_height(self) -> int:
        return self._prefix_heights[-1] if self._prefix_heights else 0

    def image_index_at_offset(self, scroll_top: int) -> int:
        if not self.image_labels:
            return 0
        idx = bisect_right(self._prefix_heights, max(0, int(scroll_top))) - 1
        return max(0, min(len(self.image_labels) - 1, idx))

    def _packed_position_at(self, scroll_top: int, zoom: float | None = None) -> float:
        if not self.image_labels:
            return 0.0
        if zoom is None or abs(zoom - self._zoom) < 0.0001:
            idx = self.image_index_at_offset(scroll_top)
            cumulative = self.cumulative_height_before(idx)
            h = self._label_heights[idx] if idx < len(self._label_heights) else self._scaled_label_height(self.image_labels[idx], zoom)
            offset_frac = ((scroll_top - cumulative) / h) if h > 0 else 0.0
            return idx + offset_frac
        scroll_top = max(0, scroll_top)
        cumulative = 0
        for i, label in enumerate(self.image_labels):
            h = self._scaled_label_height(label, zoom)
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
        logger.info(
            "Viewer saving progress for %s chapter=%s packed=%.3f total=%d",
            self.webtoon.name,
            chapter,
            packed,
            total,
        )
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

    def _jump_to_packed(self, idx: int, offset_frac: float, anchor_px: int = 0) -> bool:
        cumulative = self.cumulative_height_before(idx)
        height = self._label_heights[idx] if idx < len(self._label_heights) else self._scaled_label_height(self.image_labels[idx])
        target_px = cumulative + int(height * offset_frac) - max(0, anchor_px)

        bar = self.scroll.verticalScrollBar()
        bar.setValue(max(0, min(target_px, bar.maximum())))

        return not (bar.value() < target_px - 5)

    def _on_image_ready(self, index: int, pixmap: QPixmap):
        if index >= len(self.image_labels):
            return
        self._chapter_load_loaded += 1
        self._update_loading_overlay()

        # Only do one immediate paint per chapter load.
        # Everything else should go through the batch path so restore logic runs.
        if not self._did_immediate_first_paint and not self._pending_batch:
            label = self.image_labels[index]
            label._source_pixmap = pixmap
            label._natural_width = pixmap.width()
            label._natural_height = pixmap.height()
            self._apply_pixmap_to_label(label)
            self._set_label_height_cache(index, label.height())

            self._did_immediate_first_paint = True

            self.preview.notify_image_loaded()
            self.check_visible_images()
            self._panel_warm_timer.start()
            self._hide_loading_overlay()

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
                label._natural_width = pixmap.width()
                label._natural_height = pixmap.height()
                self._apply_pixmap_to_label(label)
                self._set_label_height_cache(index, label.height())
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
                self._jump_to_packed(idx, frac, self._resize_anchor_px)
                self._resize_packed = None

        if needs_restore_check:
            self._apply_restore()

    def load_selected_chapter(self, index):
        # If skip_specials is on, the combo index must be translated to a real chapter index
        if self._chapter_index_map and index < len(self._chapter_index_map):
            real_index = self._chapter_index_map[index]
        else:
            real_index = index
        self._load_chapter_with_prompt(real_index)

    def _load_chapter_with_prompt(self, index):
        if not self.webtoon:
            return False
        chapter = self.webtoon.chapters[index]
        logger.info("Viewer loading chapter with prompt: %s / %s", self.webtoon.name, chapter)

        saved_scroll = self.progress_store.get_for_chapter(self.webtoon.name, chapter)
        packed = 0.0
        if saved_scroll > 0.005:
            dlg = ContinueDialog(chapter, parent=self)
            if dlg.exec() != QDialog.Accepted:
                logger.info("Resume dialog cancelled for %s / %s", self.webtoon.name, chapter)
                return False
            if dlg.choice == "continue":
                packed = saved_scroll
                logger.info("Resume dialog chose continue for %s / %s", self.webtoon.name, chapter)
            elif dlg.choice != "restart":
                return False
            else:
                logger.info("Resume dialog chose restart for %s / %s", self.webtoon.name, chapter)

        self._unpack_restore(packed)
        self._load_chapter_no_prompt(index)
        return True

    def _load_chapter_no_prompt(self, index):
        if not self.webtoon:
            return
        self._progress_save_timer.stop()
        self.current_chapter_index = index
        chapter = self.webtoon.chapters[index]
        logger.info("Viewer loading chapter without prompt: %s / %s", self.webtoon.name, chapter)

        self.chapter_selector.blockSignals(True)
        if self._chapter_index_map:
            # Find the selector position for this real index
            selector_pos = next(
                (i for i, real in enumerate(self._chapter_index_map) if real == index),
                None
            )
            if selector_pos is not None:
                self.chapter_selector.setCurrentIndex(selector_pos)
        else:
            self.chapter_selector.setCurrentIndex(index)
        self.chapter_selector.blockSignals(False)

        self._load_chapter_images(chapter)
        self.update_nav_buttons()

        if self._restore_image_index is None:
            self.scroll.verticalScrollBar().setValue(0)

    def clear_images(self):
        self._batch_timer.stop()
        self._panel_warm_timer.stop()
        self._preview_timer.stop()
        self._pending_batch.clear()
        self._pending_preview_queue.clear()
        self._queued_preview_indexes.clear()
        self._did_immediate_first_paint = False

        self.loader.cancel()
        self.loader.reset()
        while self.image_layout.count():
            item = self.image_layout.takeAt(0)
            widget = item.widget()
            if widget is None:
                continue
            widget.clear()
            widget.hide()
            widget._source_pixmap = None
            widget._preview_pixmap = None
            widget._natural_width = 0
            widget._natural_height = 0
            widget._file_size = 0
            widget.img_path = ""
            self._label_pool.append(widget)
        self.image_labels = []
        self._reset_layout_metrics()

        self.preview.set_image_labels([])

        self._panel_starts = []
        self._panel_starts_dirty = True
        self._panel_build_generation += 1
        self._panel_build_inflight = False

    def _show_loading_overlay(self, chapter: str, total_images: int = 0):
        self._chapter_load_total = max(0, int(total_images))
        self._chapter_load_loaded = 0
        self._chapter_loading_active = True
        if self.webtoon is not None:
            self.chapter_loading_started.emit(self.webtoon.name, chapter)
        self.loading_spinner.set_spinning()
        self.loading_label.setText(f"Loading {chapter}...")
        if self._chapter_load_total > 0:
            self.loading_detail_label.setText(f"0 / {self._chapter_load_total} images decoded")
        else:
            self.loading_detail_label.setText("Preparing images...")
        self._position_loading_overlay()
        self.loading_overlay.show()
        self.loading_overlay.raise_()
        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)

    def _update_loading_overlay(self):
        if not self._chapter_loading_active:
            return
        if self._chapter_load_total > 0:
            self.loading_detail_label.setText(
                f"{self._chapter_load_loaded} / {self._chapter_load_total} images decoded"
            )
        else:
            self.loading_detail_label.setText(f"{self._chapter_load_loaded} images decoded")

    def _hide_loading_overlay(self):
        chapter = None
        if self.webtoon is not None and 0 <= self.current_chapter_index < len(self.webtoon.chapters):
            chapter = self.webtoon.chapters[self.current_chapter_index]
        self._chapter_loading_active = False
        self.loading_overlay.hide()
        if self.webtoon is not None and chapter is not None:
            self.chapter_loading_finished.emit(self.webtoon.name, chapter)

    def _position_loading_overlay(self):
        if not hasattr(self, "loading_overlay"):
            return
        self.loading_overlay.setGeometry(self.scroll.viewport().rect())

    def _acquire_image_label(self) -> QLabel:
        if self._label_pool:
            label = self._label_pool.pop()
        else:
            label = QLabel(self.container)
            label.setAlignment(Qt.AlignCenter)
            label.setMinimumHeight(400)
        label.show()
        return label

    def shutdown(self):
        logger.info("Shutting down viewer background workers")
        self._batch_timer.stop()
        self._panel_warm_timer.stop()
        self._preview_timer.stop()
        self._zoom_persist_timer.stop()
        self._hide_loading_overlay()
        self.loader.shutdown()

    def _load_chapter_images(self, chapter):
        self.clear_images()
        self._show_loading_overlay(chapter)

        chapter_path = os.path.join(self.webtoon.path, chapter)
        if not os.path.isdir(chapter_path):
            logger.warning("Viewer chapter path missing: %s", chapter_path)
            self._hide_loading_overlay()
            QMessageBox.information(
                self,
                "Chapter missing",
                f"'{chapter}' no longer exists on disk.",
            )
            return

        image_infos = self._get_chapter_image_infos(chapter)

        if not image_infos:
            logger.warning("Viewer chapter has no readable images: %s", chapter_path)
            self._hide_loading_overlay()
            QMessageBox.information(
                self,
                "Chapter empty",
                f"'{chapter}' has no readable images.",
            )
            return

        self._show_loading_overlay(chapter, total_images=len(image_infos))
        target_width = self._image_width()

        for img_path, natural_w, natural_h, file_size in image_infos:
            label = self._acquire_image_label()
            label.img_path = img_path
            label._source_pixmap = None
            label._preview_pixmap = None
            label._natural_width = natural_w
            label._natural_height = natural_h
            label._file_size = file_size
            if natural_w > 0 and natural_h > 0:
                placeholder_height = max(100, int(target_width * (natural_h / natural_w)))
            else:
                placeholder_height = 400
            label.setFixedHeight(placeholder_height)
            self.image_layout.addWidget(label)
            self.image_labels.append(label)
        self._label_heights = [label.height() for label in self.image_labels]
        self._rebuild_prefix_heights()
        logger.info("Viewer queued %d images for %s / %s", len(self.image_labels), self.webtoon.name, chapter)

        self.preview.set_image_labels(self.image_labels)

        self.check_visible_images()
        QTimer.singleShot(0, self.check_visible_images)

        self._queue_initial_previews()

        if self._restore_image_index is not None:
            self._preload_restore_target()

        self.setFocus()

    def _chapter_cache_entry(self, chapter: str) -> tuple[str, int]:
        chapter_path = os.path.join(self.webtoon.path, chapter)
        try:
            mtime_ns = os.stat(chapter_path).st_mtime_ns
        except OSError:
            mtime_ns = -1
        return chapter_path, mtime_ns

    def _get_chapter_image_paths(self, chapter: str) -> list[str]:
        chapter_path, mtime_ns = self._chapter_cache_entry(chapter)
        cached = self._chapter_image_cache.get(chapter_path)
        if cached is not None and cached[0] == mtime_ns:
            return list(cached[1])

        image_paths = sorted(
            entry.path
            for entry in os.scandir(chapter_path)
            if entry.is_file() and entry.name.lower().endswith(SUPPORTED_VIEWER_EXTENSIONS)
        )
        self._chapter_image_cache[chapter_path] = (mtime_ns, image_paths)
        return list(image_paths)

    def _get_chapter_image_infos(self, chapter: str) -> list[tuple[str, int, int, int]]:
        chapter_path, mtime_ns = self._chapter_cache_entry(chapter)
        cached = self._chapter_image_info_cache.get(chapter_path)
        if cached is not None and cached[0] == mtime_ns:
            return list(cached[1])

        infos = []
        for index, path in enumerate(self._get_chapter_image_paths(chapter), start=1):
            reader = QImageReader(path)
            size = reader.size()
            if not size.isValid():
                size = QSize(0, 0)
            try:
                file_size = os.path.getsize(path)
            except OSError:
                file_size = 0
            infos.append((path, max(0, size.width()), max(0, size.height()), file_size))
            if index % 8 == 0 and self._chapter_loading_active:
                QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)

        self._chapter_image_info_cache[chapter_path] = (mtime_ns, infos)
        return list(infos)

    def _queue_preview_index(self, index: int):
        if index < 0 or index >= len(self.image_labels) or index in self._queued_preview_indexes:
            return
        self._queued_preview_indexes.add(index)
        self.loader.load_preview(index, self.image_labels[index].img_path)

    def _queue_initial_previews(self):
        eager_count = min(len(self.image_labels), PREVIEW_EAGER_COUNT)
        for index in range(eager_count):
            self._queue_preview_index(index)

        self._pending_preview_queue = list(range(eager_count, len(self.image_labels)))
        if self._pending_preview_queue:
            self._preview_timer.start()

    def _drain_preview_queue(self):
        if not self._pending_preview_queue:
            return

        batch = self._pending_preview_queue[:PREVIEW_BATCH_SIZE]
        del self._pending_preview_queue[:PREVIEW_BATCH_SIZE]

        for index in batch:
            self._queue_preview_index(index)

        if self._pending_preview_queue:
            self._preview_timer.start()

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
        if not self.image_labels:
            return

        start_index = self.image_index_at_offset(max(0, viewport_top - LAZY_WINDOW))
        end_index = min(
            len(self.image_labels) - 1,
            self.image_index_at_offset(viewport_bottom + LAZY_WINDOW),
        )

        for i in range(start_index, end_index + 1):
            label = self.image_labels[i]
            if getattr(label, '_source_pixmap', None) is not None:
                self._queue_preview_index(i)
                continue

            self._queue_preview_index(i)
            self.loader.load(i, label.img_path, 0)

    def _zoom_out(self):
        self._set_zoom(self._zoom - 0.05)
        self._schedule_zoom_override_persist()

    def _zoom_in(self):
        self._set_zoom(self._zoom + 0.05)
        self._schedule_zoom_override_persist()

    def _on_zoom_slider(self, value: int):
        self._set_zoom(value / 100.0, update_slider=False)
        self._schedule_zoom_override_persist()

    def _schedule_zoom_override_persist(self):
        if not self.webtoon:
            return
        self._zoom_override_active = True
        self._zoom_reset_btn.setEnabled(True)
        self._zoom_persist_timer.start()

    def _persist_zoom_override_now(self):
        """Save current zoom as a per-webtoon override after user interaction settles."""
        if not self.webtoon:
            return
        logger.info("Persisting viewer zoom override for %s to %.2f", self.webtoon.name, self._zoom)
        self.settings_store.set_zoom_override(self.webtoon.name, self._zoom)
        self._zoom_override_active = True
        self._zoom_reset_btn.setEnabled(True)

    def _set_zoom(self, zoom: float, update_slider: bool = True, rescale_existing: bool = True):
        previous_zoom = self._zoom
        next_zoom = max(0.25, min(1.0, zoom))
        changed = abs(next_zoom - previous_zoom) > 0.0001
        self._zoom = next_zoom

        self._zoom_slider.blockSignals(True)
        self._zoom_slider.setValue(int(self._zoom * 100))
        self._zoom_slider.blockSignals(False)

        self._zoom_label.setText(f"{int(self._zoom * 100)}%")

        self.preview.set_zoom(self._zoom)
        if rescale_existing and changed and self.image_labels:
            self.rescale_images(previous_zoom)

    def _image_width(self) -> int:
        return max(1, int(self.scroll.viewport().width() * self._zoom))

    def _apply_pixmap_to_label(self, label):
        src = getattr(label, '_source_pixmap', None)
        if src is None or src.isNull():
            return
        scaled = src.scaledToWidth(self._image_width(), Qt.SmoothTransformation)
        label.setPixmap(scaled)
        label.setFixedHeight(scaled.height())

    def rescale_images(self, previous_zoom: float | None = None):
        # Capture position as a fraction of total content height — this is
        # invariant across rescales, unlike packed position which depends on
        # individual label heights that are about to change.
        bar = self.scroll.verticalScrollBar()
        self._resize_anchor_px = 0
        self._resize_packed = self._packed_position_at(bar.value(), previous_zoom)

        self.container.setUpdatesEnabled(False)
        try:
            for index, label in enumerate(self.image_labels):
                self._apply_pixmap_to_label(label)
                self._set_label_height_cache(index, label.height())
        finally:
            self.container.setUpdatesEnabled(True)

        # Defer the jump so Qt finishes reflowing label geometry first.
        def _restore():
            if self._resize_packed is None or not self.image_labels:
                return
            idx = int(self._resize_packed)
            frac = self._resize_packed - idx
            if idx < len(self.image_labels):
                self._jump_to_packed(idx, frac, self._resize_anchor_px)

        QTimer.singleShot(0, _restore)

        self.preview.update()
        self._invalidate_panel_cache()
        self._panel_warm_timer.start()

    def next_chapter(self):
        next_idx = self._next_chapter_index(self.current_chapter_index)
        if next_idx is not None:
            logger.info("Viewer moving to next chapter for %s", self.webtoon.name if self.webtoon else "<none>")
            self._progress_save_timer.stop()
            self._save_progress()
            self._restore_image_index = None
            self._load_chapter_with_prompt(next_idx)

    def prev_chapter(self):
        prev_idx = self._prev_chapter_index(self.current_chapter_index)
        if prev_idx is not None:
            logger.info("Viewer moving to previous chapter for %s", self.webtoon.name if self.webtoon else "<none>")
            self._progress_save_timer.stop()
            self._save_progress()
            self._restore_image_index = None
            self._load_chapter_with_prompt(prev_idx)

    def _next_chapter_index(self, from_index: int) -> int | None:
        """Return the next chapter index, skipping specials if the toggle is on."""
        chapters = self.webtoon.chapters
        candidates = range(from_index + 1, len(chapters))
        for i in candidates:
            if not self.skip_specials_enabled or not _SPECIAL_CHAPTER_RE.search(chapters[i]):
                return i
        return None

    def _prev_chapter_index(self, from_index: int) -> int | None:
        """Return the previous chapter index, skipping specials if the toggle is on."""
        chapters = self.webtoon.chapters
        candidates = range(from_index - 1, -1, -1)
        for i in candidates:
            if not self.skip_specials_enabled or not _SPECIAL_CHAPTER_RE.search(chapters[i]):
                return i
        return None

    def _repopulate_chapter_selector(self):
        """Fill the chapter selector, hiding special chapters when skip_specials is on."""
        if not self.webtoon:
            return
        self.chapter_selector.blockSignals(True)
        self.chapter_selector.clear()
        if self.skip_specials_enabled:
            self._chapter_index_map = [
                i for i, c in enumerate(self.webtoon.chapters)
                if not _SPECIAL_CHAPTER_RE.search(c)
            ]
            self.chapter_selector.addItems(
                [self.webtoon.chapters[i] for i in self._chapter_index_map]
            )
        else:
            self._chapter_index_map = []
            self.chapter_selector.addItems(self.webtoon.chapters)
        self.chapter_selector.blockSignals(False)

    def update_nav_buttons(self):
        self.prev_button.setEnabled(self._prev_chapter_index(self.current_chapter_index) is not None)
        self.next_button.setEnabled(self._next_chapter_index(self.current_chapter_index) is not None)

    def _clear_zoom_override(self):
        if not self.webtoon:
            return
        self._zoom_persist_timer.stop()
        logger.info("Clearing viewer zoom override for %s", self.webtoon.name)
        self.settings_store.clear_zoom_override(self.webtoon.name)
        self._zoom_override_active = False
        self._zoom_reset_btn.setEnabled(False)
        # Snap back to the global default without saving it as global
        self._set_zoom(load_setting("viewer_zoom", 0.5))
        self.setFocus()

    def go_back(self):
        logger.info("Leaving viewer for detail page: %s", self.webtoon.name if self.webtoon else "<none>")
        self._save_progress()
        self.main_window.library.refresh_progress()
        self.main_window.open_detail(self.webtoon, force=True)

    def resizeEvent(self, event):
        self._resize_timer.start()
        self._position_loading_overlay()
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
            h = self._scaled_label_height(label)
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

    def _on_preview_ready(self, index: int, pixmap: QPixmap, natural_w: int, natural_h: int):
        """Thumbnail arrived — store it and set correct label height if not yet loaded."""
        if index >= len(self.image_labels):
            return
        label = self.image_labels[index]
        label._preview_pixmap = pixmap
        label._natural_width = natural_w
        label._natural_height = natural_h
        if getattr(label, '_source_pixmap', None) is None and natural_w > 0 and natural_h > 0:
            aspect = natural_h / natural_w
            scaled_h = max(100, int(self._image_width() * aspect))
            label.setFixedHeight(scaled_h)
            self._set_label_height_cache(index, scaled_h)
        self.preview.notify_image_loaded()

    def _get_panel_starts(self) -> list[int]:
        if self._panel_starts_dirty:
            return []
        return self._panel_starts

    def _total_content_height(self) -> int:
        return self.total_content_height()

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

            if not self.auto_skip_enabled:
                if key == Qt.Key_Down:
                    bar.setValue(pos + int(view_h * 0.9))
                else:
                    bar.setValue(max(0, pos - int(view_h * 0.9)))
                return

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
            next_idx = self._next_chapter_index(self.current_chapter_index)
            if next_idx is not None:
                self._progress_save_timer.stop()
                self._save_progress()
                self._restore_image_index = None
                if self._load_chapter_with_prompt(next_idx):
                    self.setFocus()

        elif key == Qt.Key_Left:
            prev_idx = self._prev_chapter_index(self.current_chapter_index)
            if prev_idx is not None:
                self._progress_save_timer.stop()
                self._save_progress()
                self._restore_image_index = None
                if self._load_chapter_with_prompt(prev_idx):
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

    def _toggle_navigation_mode(self):
        self.auto_skip_enabled = self.nav_toggle.isChecked()

        if self.auto_skip_enabled:
            self.nav_toggle.setText("Auto Skip")
        else:
            self.nav_toggle.setText("Standard")

        save_setting("viewer_auto_skip", self.auto_skip_enabled)
        logger.info("Viewer navigation mode changed auto_skip=%s", self.auto_skip_enabled)

        self.setFocus()
