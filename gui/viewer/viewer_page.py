import os
import re
from concurrent.futures import ThreadPoolExecutor

from app_logging import get_logger
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QScrollArea,
    QPushButton, QComboBox, QHBoxLayout, QDialog, QApplication, QSlider, QMessageBox
)
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QImage
from PySide6.QtCore import Qt, QPoint, QEvent, QTimer, Signal, QObject, QRect

from progress_store import get_instance as get_progress_store
from webtoon_settings_store import get_instance as get_webtoon_settings
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
logger = get_logger(__name__)


class ContinueDialog(QDialog):

    def __init__(self, chapter: str, parent=None):
        super().__init__(parent)
        self.choice = "cancel"
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
        restart_btn.clicked.connect(self._start_over)
        continue_btn = QPushButton("Continue")
        continue_btn.setStyleSheet("QPushButton{background:#2979ff;color:#fff;} QPushButton:hover{background:#448aff;}")
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


class ImageLoader(QObject):
    image_ready = Signal(int, QPixmap)
    preview_ready = Signal(int, QPixmap, int, int)  # index, thumb, natural_width, natural_height
    panel_starts_ready = Signal(int, list)

    def __init__(self):
        super().__init__()
        self.executor = ThreadPoolExecutor(max_workers=NUM_WORKERS)
        self._cancelled = False
        self._queued = set()
        self._preview_queued = set()

    def cancel(self):
        self._cancelled = True
        self._queued.clear()
        self._preview_queued.clear()

    def reset(self):
        self._cancelled = False

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
        self.executor.submit(self._preview_task, index, path, max_w)

    def _load_task(self, index: int, path: str):
        if self._cancelled:
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return
        if not self._cancelled:
            self.image_ready.emit(index, pixmap)

    def _preview_task(self, index: int, path: str, max_w: int):
        if self._cancelled:
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return
        if not self._cancelled:
            thumb = pixmap.scaledToWidth(max_w, Qt.SmoothTransformation)
            self.preview_ready.emit(index, thumb, pixmap.width(), pixmap.height())

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

    def _scaled_label_height(self, label) -> int:
        natural_w = getattr(label, '_natural_width', 0)
        natural_h = getattr(label, '_natural_height', 0)
        image_width = max(1, int(self.scroll_area.viewport().width() * self._zoom))
        if natural_w > 0 and natural_h > 0:
            return max(1, int(image_width * (natural_h / natural_w)))
        return max(1, label.height())

    def _total_content_height(self) -> int:
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
        self._zoom_label.setStyleSheet("color: #aaa; font-size: 12px;")

        _zoom_btn_style = """
            QPushButton {
                background: transparent;
                color: #888;
                border: 1px solid #333;
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 11px;
            }
            QPushButton:hover { background: #2a2a2a; color: #fff; }
            QPushButton:disabled { color: #444; border-color: #222; }
        """
        self._zoom_reset_btn = QPushButton("Reset zoom")
        self._zoom_reset_btn.setFocusPolicy(Qt.NoFocus)
        self._zoom_reset_btn.setToolTip("Remove webtoon zoom override and use global default")
        self._zoom_reset_btn.setStyleSheet(_zoom_btn_style)
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
        logger.info(
            "Viewer loading webtoon=%s chapter_index=%d start_scroll=%.3f",
            webtoon.name,
            start_chapter,
            start_scroll,
        )
        webtoon.path = os.path.abspath(webtoon.path)
        self.webtoon = webtoon
        self._unpack_restore(start_scroll)
        self._apply_webtoon_settings(webtoon)
        self._repopulate_chapter_selector()
        self.current_chapter_index = start_chapter
        self._load_chapter_no_prompt(start_chapter)

    def _apply_webtoon_settings(self, webtoon):
        """Apply per-webtoon settings (zoom, hide filler). Called whenever a webtoon is opened."""
        logger.info("Applying viewer settings for %s", webtoon.name)
        # Hide filler
        self.skip_specials_enabled = self.settings_store.get_hide_filler(webtoon.name)

        # Zoom override
        override = self.settings_store.get_zoom_override(webtoon.name)
        if override is not None:
            self._zoom_override_active = True
            self._set_zoom(override)
        else:
            self._zoom_override_active = False
            self._set_zoom(load_setting("viewer_zoom", 0.5))
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

    def _packed_position_at(self, scroll_top: int, zoom: float | None = None) -> float:
        if not self.image_labels:
            return 0.0
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
        cumulative = sum(self._scaled_label_height(self.image_labels[i]) for i in range(idx))
        target_px = cumulative + int(self._scaled_label_height(self.image_labels[idx]) * offset_frac) - max(0, anchor_px)

        QApplication.processEvents()

        bar = self.scroll.verticalScrollBar()
        bar.setValue(max(0, min(target_px, bar.maximum())))

        return not (bar.value() < target_px - 5)

    def _on_image_ready(self, index: int, pixmap: QPixmap):
        if index >= len(self.image_labels):
            return

        # Only do one immediate paint per chapter load.
        # Everything else should go through the batch path so restore logic runs.
        if not self._did_immediate_first_paint and not self._pending_batch:
            label = self.image_labels[index]
            label._source_pixmap = pixmap
            label._natural_width = pixmap.width()
            label._natural_height = pixmap.height()
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
                label._natural_width = pixmap.width()
                label._natural_height = pixmap.height()
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
        if not os.path.isdir(chapter_path):
            logger.warning("Viewer chapter path missing: %s", chapter_path)
            QMessageBox.information(
                self,
                "Chapter missing",
                f"'{chapter}' no longer exists on disk.",
            )
            return

        image_files = sorted(
            f for f in os.listdir(chapter_path)
            if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        )

        if not image_files:
            logger.warning("Viewer chapter has no readable images: %s", chapter_path)
            QMessageBox.information(
                self,
                "Chapter empty",
                f"'{chapter}' has no readable images.",
            )
            return

        for img_file in image_files:
            label = QLabel()
            label.img_path = os.path.join(chapter_path, img_file)
            label._source_pixmap = None
            label._preview_pixmap = None
            label._natural_width = 0
            label._natural_height = 0
            label.setAlignment(Qt.AlignCenter)
            label.setMinimumHeight(400)
            self.image_layout.addWidget(label)
            self.image_labels.append(label)
        logger.info("Viewer queued %d images for %s / %s", len(self.image_labels), self.webtoon.name, chapter)

        self.preview.set_image_labels(self.image_labels)

        self.check_visible_images()
        QTimer.singleShot(0, self.check_visible_images)

        # Immediately queue small thumbnails for all images so the preview
        # strip populates quickly regardless of scroll position.
        for idx, label in enumerate(self.image_labels):
            self.loader.load_preview(idx, label.img_path)

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
            h = self._scaled_label_height(label)
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
        self._persist_zoom_override()

    def _zoom_in(self):
        self._set_zoom(self._zoom + 0.05)
        self._persist_zoom_override()

    def _on_zoom_slider(self, value: int):
        self._set_zoom(value / 100.0, update_slider=False)
        self._persist_zoom_override()

    def _persist_zoom_override(self):
        """Save current zoom as a per-webtoon override. Called only on user interaction."""
        if not self.webtoon:
            return
        logger.info("Persisting viewer zoom override for %s to %.2f", self.webtoon.name, self._zoom)
        self.settings_store.set_zoom_override(self.webtoon.name, self._zoom)
        self._zoom_override_active = True
        self._zoom_reset_btn.setEnabled(True)

    def _set_zoom(self, zoom: float, update_slider: bool = True):
        previous_zoom = self._zoom
        self._zoom = max(0.25, min(1.0, zoom))

        self._zoom_slider.blockSignals(True)
        self._zoom_slider.setValue(int(self._zoom * 100))
        self._zoom_slider.blockSignals(False)

        self._zoom_label.setText(f"{int(self._zoom * 100)}%")

        self.preview.set_zoom(self._zoom)
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
            for label in self.image_labels:
                self._apply_pixmap_to_label(label)
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
            self._save_progress()
            self._restore_image_index = None
            self._load_chapter_no_prompt(next_idx)

    def prev_chapter(self):
        prev_idx = self._prev_chapter_index(self.current_chapter_index)
        if prev_idx is not None:
            logger.info("Viewer moving to previous chapter for %s", self.webtoon.name if self.webtoon else "<none>")
            self._save_progress()
            self._restore_image_index = None
            self._load_chapter_no_prompt(prev_idx)

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
        self.preview.notify_image_loaded()

    def _get_panel_starts(self) -> list[int]:
        if self._panel_starts_dirty:
            return []
        return self._panel_starts

    def _total_content_height(self) -> int:
        return sum(self._scaled_label_height(label) for label in self.image_labels)

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
                self._load_chapter_with_prompt(next_idx)
                self.setFocus()

        elif key == Qt.Key_Left:
            prev_idx = self._prev_chapter_index(self.current_chapter_index)
            if prev_idx is not None:
                self._progress_save_timer.stop()
                self._save_progress()
                self._restore_image_index = None
                self._load_chapter_with_prompt(prev_idx)
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
