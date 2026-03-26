import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QImage, QImageReader, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from core.app_logging import get_logger
from gui.common.styles import (
    VIEWER_RESUME_CONTINUE_BUTTON_STYLE,
    VIEWER_RESUME_DIALOG_STYLE,
    VIEWER_RESUME_RESTART_BUTTON_STYLE,
)


FILMSTRIP_W = 40
IMAGE_STRIP_W = 50
PREVIEW_W = FILMSTRIP_W + IMAGE_STRIP_W
HORIZONTAL_PREVIEW_H = 96
PAGE_COLUMN_W = 56

SPECIAL_CHAPTER_RE = re.compile(r"\b\d+\.\d+\b")

TILE_GAP = 2
TILE_PADDING = 2
TILE_MIN_H = 14
TILE_MAX_H = 120
QT_WIDGET_MAX = 16777215

NUM_WORKERS = 8
PREVIEW_WORKERS = 2
PANEL_WORKERS = 1
VIEWER_AUTO_SCROLL_CURSOR_SIZE = 32
VIEWER_AUTO_SCROLL_LINE = "#fff0ec"

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


def format_bytes(size: int) -> str:
    units = ("B", "KB", "MB", "GB")
    value = float(max(0, int(size)))
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} GB"


class ImageLoader(QObject):
    image_ready = Signal(int, QPixmap)
    preview_ready = Signal(int, QPixmap, int, int)
    panel_ranges_ready = Signal(int, list)

    def __init__(self):
        super().__init__()
        self.executor = ThreadPoolExecutor(max_workers=NUM_WORKERS)
        self.preview_executor = ThreadPoolExecutor(max_workers=PREVIEW_WORKERS)
        self.panel_executor = ThreadPoolExecutor(max_workers=PANEL_WORKERS)
        self._cancelled = False
        self._queued = set()
        self._preview_queued = set()
        self._panel_range_cache: dict[str, list[tuple[float, float]]] = {}

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
            format_bytes(file_size),
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

    def build_panel_ranges(self, generation: int, payload: list):
        self.panel_executor.submit(self._panel_task, generation, payload)

    def _panel_task(self, generation: int, payload: list):
        min_blank = 18
        row_step = 4
        ranges = []
        cumulative = 0

        for item in payload:
            height = item["height"]
            path = item["path"]

            if not path or height <= 0:
                cumulative += max(0, height)
                continue

            image = QImage(path)
            if image.isNull():
                cumulative += height
                continue

            image_height = image.height()
            if image_height <= 0:
                cumulative += height
                continue

            range_fractions = self._panel_range_cache.get(path)
            if range_fractions is None:
                range_fractions = self._compute_panel_ranges(image, min_blank=min_blank, row_step=row_step)
                self._panel_range_cache[path] = range_fractions
                sample_rows = []
                for sample_y in {0, max(0, image_height // 4), max(0, image_height // 2), max(0, (image_height * 3) // 4), max(0, image_height - 1)}:
                    avg, variance, chroma, occupied, is_blank = self._blank_row_metrics(image, sample_y)
                    sample_rows.append(
                        f"y={sample_y}:avg={avg:.1f},var={variance:.1f},chroma={chroma:.1f},occupied={occupied:.3f},blank={is_blank}"
                    )
                logger.info(
                    "Viewer panel analysis path=%s image_h=%d scaled_h=%d ranges=%s samples=[%s]",
                    path,
                    image_height,
                    height,
                    range_fractions,
                    "; ".join(sample_rows),
                )

            for start_fraction, end_fraction in range_fractions:
                start_y = cumulative + int(start_fraction * height)
                end_y = cumulative + int(end_fraction * height)
                if end_y > start_y:
                    ranges.append((start_y, end_y))

            cumulative += height

        if not self._cancelled:
            self.panel_ranges_ready.emit(generation, sorted(set(ranges)))

    def _compute_panel_ranges(self, image: QImage, min_blank: int, row_step: int) -> list[tuple[float, float]]:
        image_height = image.height()
        if image_height <= 0:
            return []

        ranges = []
        content_start = None
        blank_run = 0
        blank_start = None

        for src_y in range(0, image_height, row_step):
            is_blank = self._is_blank_row(image, src_y)

            if is_blank:
                if blank_start is None:
                    blank_start = src_y
                blank_run += row_step
                if content_start is not None and blank_run >= min_blank:
                    content_end = max(content_start + row_step, blank_start)
                    ranges.append((content_start / image_height, min(1.0, content_end / image_height)))
                    content_start = None
            else:
                if content_start is None:
                    content_start = src_y
                blank_run = 0
                blank_start = None

        if content_start is not None:
            ranges.append((content_start / image_height, 1.0))

        if not ranges:
            return [(0.0, 1.0)]

        return self._merge_panel_ranges(ranges, image_height, row_step)

    def _merge_panel_ranges(
        self,
        ranges: list[tuple[float, float]],
        image_height: int,
        row_step: int,
    ) -> list[tuple[float, float]]:
        if not ranges:
            return [(0.0, 1.0)]

        merged = [ranges[0]]
        min_dialogue_gap = max(row_step * 3, 28) / image_height
        short_dialogue_gap = max(84, int(image_height * 0.028)) / image_height
        bridgeable_gap = max(148, int(image_height * 0.048)) / image_height
        tiny_band = max(112, int(image_height * 0.036)) / image_height

        for start, end in ranges[1:]:
            prev_start, prev_end = merged[-1]
            gap = max(0.0, start - prev_end)
            prev_h = max(0.0, prev_end - prev_start)
            curr_h = max(0.0, end - start)
            neighbor_h = min(prev_h, curr_h) if prev_h and curr_h else max(prev_h, curr_h)

            merge_gap = False
            if gap <= min_dialogue_gap:
                merge_gap = True
            elif gap <= short_dialogue_gap and (prev_h <= tiny_band or curr_h <= tiny_band):
                merge_gap = True
            elif gap <= bridgeable_gap and neighbor_h > 0 and gap <= (neighbor_h * 0.22):
                merge_gap = True

            if merge_gap:
                merged[-1] = (prev_start, max(prev_end, end))
            else:
                merged.append((start, end))

        return merged or [(0.0, 1.0)]

    def _blank_row_metrics(self, image: QImage, y: int, sample_step: int = 12) -> tuple[float, float, float, float, bool]:
        width = image.width()
        if width <= 0:
            return (0.0, 0.0, 0.0, 0.0, True)

        step = max(sample_step, width // 160)
        total = 0
        total_sq = 0
        total_chroma = 0
        occupied = 0
        count = 0

        for x in range(0, width, step):
            rgb = image.pixel(x, y)
            red = (rgb >> 16) & 0xFF
            green = (rgb >> 8) & 0xFF
            blue = rgb & 0xFF
            lum = (299 * red + 587 * green + 114 * blue) // 255
            chroma = max(red, green, blue) - min(red, green, blue)
            total += lum
            total_sq += lum * lum
            total_chroma += chroma
            if lum < 760 or chroma > 40:
                occupied += 1
            count += 1

        if count == 0:
            return (0.0, 0.0, 0.0, 0.0, True)

        avg = total / count
        variance = (total_sq / count) - (avg * avg)
        avg_chroma = total_chroma / count
        occupied_ratio = occupied / count

        is_extreme = avg < 120 or avg > 880
        is_uniform = variance < 3000
        is_soft_fade = variance < 900 and avg_chroma < 28
        is_sparse = occupied_ratio <= 0.012 and variance < 2200
        is_blank = ((is_extreme and is_uniform) or is_soft_fade) and is_sparse
        return (avg, variance, avg_chroma, occupied_ratio, is_blank)

    def _is_blank_row(self, image: QImage, y: int, sample_step: int = 12) -> bool:
        return self._blank_row_metrics(image, y, sample_step)[4]


class ChapterPreview(QWidget):

    def __init__(self, scroll_area: QScrollArea, metrics_provider=None, scene_jump_callback=None, parent=None):
        super().__init__(parent)
        self.scroll_area = scroll_area
        self.metrics_provider = metrics_provider
        self.scene_jump_callback = scene_jump_callback
        self.image_labels = []
        self._display_mode = "vertical"
        self.setFixedWidth(PREVIEW_W)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)
        self._dragging = False
        self._zoom = 1.0
        self._scene_marks: list[dict] = []
        self._scene_marks_visible = True

    def set_display_mode(self, mode: str):
        requested = str(mode).strip().casefold()
        if requested == "horizontal":
            normalized = "horizontal"
        elif requested in {"pages_only", "pages"}:
            normalized = "pages_only"
        else:
            normalized = "vertical"
        if normalized == self._display_mode:
            return
        self._display_mode = normalized
        if normalized == "horizontal":
            self.setFixedHeight(HORIZONTAL_PREVIEW_H)
            self.setMinimumHeight(HORIZONTAL_PREVIEW_H)
            self.setMaximumHeight(HORIZONTAL_PREVIEW_H)
            self.setMinimumWidth(0)
            self.setMaximumWidth(QT_WIDGET_MAX)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        elif normalized == "pages_only":
            self.setMinimumWidth(0)
            self.setMaximumWidth(PAGE_COLUMN_W)
            self.setMinimumHeight(0)
            self.setMaximumHeight(QT_WIDGET_MAX)
            self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        else:
            self.setFixedWidth(PREVIEW_W)
            self.setMinimumWidth(PREVIEW_W)
            self.setMaximumWidth(PREVIEW_W)
            self.setMinimumHeight(0)
            self.setMaximumHeight(QT_WIDGET_MAX)
            self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.updateGeometry()
        self.update()

    def set_zoom(self, zoom: float):
        self._zoom = zoom
        self.update()

    def set_image_labels(self, labels: list):
        self.image_labels = labels
        self.update()

    def set_scene_marks(self, marks: list[dict]):
        self._scene_marks = list(marks or [])
        self.update()

    def set_scene_marks_visible(self, visible: bool):
        self._scene_marks_visible = bool(visible)
        self.update()

    def notify_image_loaded(self):
        self.update()

    def _scaled_label_height(self, label) -> int:
        if self.metrics_provider is not None:
            return self.metrics_provider.scaled_label_height(label)
        natural_w = getattr(label, "_natural_width", 0)
        natural_h = getattr(label, "_natural_height", 0)
        image_width = max(1, int(self.scroll_area.viewport().width() * self._zoom))
        if natural_w > 0 and natural_h > 0:
            return max(1, int(image_width * (natural_h / natural_w)))
        return max(1, label.height())

    def _total_content_height(self) -> int:
        if self.metrics_provider is not None:
            return self.metrics_provider.total_content_height()
        return sum(self._scaled_label_height(label) for label in self.image_labels)

    def _tile_height(self) -> int:
        count = len(self.image_labels)
        if count == 0:
            return TILE_MAX_H
        available = self.height() - (count - 1) * TILE_GAP
        return int(max(TILE_MIN_H, min(TILE_MAX_H, available / count)))

    def _tile_rect(self, index: int, tile_h: int) -> QRect:
        y = index * (tile_h + TILE_GAP)
        tile_w = (self.width() if self._display_mode == "pages_only" else FILMSTRIP_W) - TILE_PADDING * 2
        return QRect(TILE_PADDING, y, max(1, tile_w), tile_h)

    def _tile_index_at(self, pos: QPoint) -> int | None:
        strip_w = self.width() if self._display_mode == "pages_only" else FILMSTRIP_W
        if pos.x() >= strip_w:
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
        if self.metrics_provider is not None and hasattr(self.metrics_provider, "current_preview_image_index"):
            return int(self.metrics_provider.current_preview_image_index())
        scroll_top = self.scroll_area.verticalScrollBar().value()
        if self.metrics_provider is not None:
            return self.metrics_provider.image_index_at_offset(scroll_top)
        cumulative = 0
        for index, label in enumerate(self.image_labels):
            height = self._scaled_label_height(label)
            if cumulative + height > scroll_top:
                return index
            cumulative += height
        return len(self.image_labels) - 1

    def _current_image_indexes(self) -> list[int]:
        if not self.image_labels:
            return []
        if self.metrics_provider is not None and hasattr(self.metrics_provider, "current_preview_image_indexes"):
            indexes = [int(idx) for idx in self.metrics_provider.current_preview_image_indexes()]
            return [idx for idx in indexes if 0 <= idx < len(self.image_labels)]
        return [self._current_image_index()]

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.Antialiasing)
        if self._display_mode == "horizontal":
            painter.fillRect(self.rect(), QColor("#141414"))
            if not self.image_labels:
                return
            current_idx = self._current_image_index()
            self._paint_horizontal_strip(painter, current_idx)
            return
        if self._display_mode == "pages_only":
            painter.fillRect(self.rect(), QColor("#1a1a1a"))
            if not self.image_labels:
                return
            current_idx = self._current_image_index()
            self._paint_pages_only_strip(painter, current_idx)
            return
        painter.fillRect(QRect(0, 0, FILMSTRIP_W, self.height()), QColor("#1a1a1a"))
        painter.fillRect(QRect(FILMSTRIP_W, 0, IMAGE_STRIP_W, self.height()), QColor("#141414"))
        if not self.image_labels:
            return
        current_idx = self._current_image_index()
        self._paint_filmstrip(painter, current_idx)
        self._paint_image_strip(painter, current_idx)

    def _visible_page_window(self, current_idx: int) -> tuple[int, int]:
        count = len(self.image_labels)
        if count <= 0:
            return (0, 0)
        slot_w = 10
        gap = 1
        usable_w = max(60, self.width() - 28)
        max_slots = max(7, usable_w // (slot_w + gap))
        max_slots = min(max_slots, 18)
        max_slots = min(count, max_slots)
        start = max(0, current_idx - (max_slots // 2))
        end = min(count, start + max_slots)
        start = max(0, end - max_slots)
        return start, end

    def _horizontal_strip_layout(self, current_idx: int) -> tuple[QRect, int, int, int, int]:
        rect = self.rect().adjusted(10, 10, -10, -10)
        start, end = self._visible_page_window(current_idx)
        visible_count = max(1, end - start)
        gap = 0
        return rect, start, end, visible_count, gap

    def _horizontal_page_width(self, label, track_h: int) -> int:
        natural_w = max(0, int(getattr(label, "_natural_width", 0) or 0))
        natural_h = max(0, int(getattr(label, "_natural_height", 0) or 0))
        if natural_w > 0 and natural_h > 0:
            return max(1, int(track_h * (natural_w / natural_h)))
        src = getattr(label, "_source_pixmap", None) or label.pixmap() or getattr(label, "_preview_pixmap", None)
        if src and not src.isNull() and src.height() > 0:
            return max(1, int(track_h * (src.width() / src.height())))
        return max(1, track_h)

    def _page_aspect_ratio(self, label) -> float:
        natural_w = max(0, int(getattr(label, "_natural_width", 0) or 0))
        natural_h = max(0, int(getattr(label, "_natural_height", 0) or 0))
        if natural_w > 0 and natural_h > 0:
            return natural_w / natural_h
        src = getattr(label, "_source_pixmap", None) or label.pixmap() or getattr(label, "_preview_pixmap", None)
        if src and not src.isNull() and src.height() > 0:
            return src.width() / src.height()
        return 0.7

    def _pages_only_page_height(self, index: int, tile_w: int) -> int:
        if index < 0 or index >= len(self.image_labels):
            return max(TILE_MIN_H, tile_w)
        ratio = max(0.05, float(self._page_aspect_ratio(self.image_labels[index]) or 0.7))
        return max(TILE_MIN_H, int(tile_w / ratio))

    def _horizontal_visible_indexes(self, current_idx: int, track_h: int, max_width: int) -> list[int]:
        count = len(self.image_labels)
        if count <= 0:
            return []
        current_idx = max(0, min(count - 1, int(current_idx)))
        indexes = [current_idx]
        total_width = self._horizontal_page_width(self.image_labels[current_idx], track_h)
        left = current_idx - 1
        right = current_idx + 1

        while left >= 0 or right < count:
            if right < count:
                width = self._horizontal_page_width(self.image_labels[right], track_h)
                if total_width + width <= max_width or len(indexes) == 1:
                    indexes.append(right)
                    total_width += width
                    right += 1
                else:
                    right = count
            if left >= 0:
                width = self._horizontal_page_width(self.image_labels[left], track_h)
                if total_width + width <= max_width or len(indexes) == 1:
                    indexes.insert(0, left)
                    total_width += width
                    left -= 1
                else:
                    left = -1

        return indexes

    def _horizontal_page_rects(self, current_idx: int) -> tuple[QRect, list[tuple[int, QRect]]]:
        rect, _start, _end, _visible_count, _gap = self._horizontal_strip_layout(current_idx)
        if rect.width() <= 0 or rect.height() <= 0:
            return rect, []

        track_h = max(14, rect.height())
        visible_indexes = self._horizontal_visible_indexes(current_idx, track_h, rect.width())
        if not visible_indexes:
            return rect, []

        base_widths = [self._horizontal_page_width(self.image_labels[index], track_h) for index in visible_indexes]
        total_width = max(1, sum(base_widths))
        scale = min(1.0, rect.width() / total_width)
        scaled_widths = [max(1, width * scale) for width in base_widths]

        layouts: list[tuple[int, QRect]] = []
        cursor = float(rect.left())
        for offset, index in enumerate(visible_indexes):
            width = scaled_widths[offset]
            next_cursor = cursor + width
            block_left = int(round(cursor))
            block_right = int(round(next_cursor))
            if offset == len(scaled_widths) - 1:
                block_right = min(rect.right() + 1, max(block_left + 1, block_right))
            layouts.append((index, QRect(block_left, rect.top(), max(1, block_right - block_left), track_h)))
            cursor = next_cursor
        return rect, layouts

    def _horizontal_block_rect(
        self,
        rect: QRect,
        visible_count: int,
        gap: int,
        offset: int,
        y: int,
        track_h: int,
    ) -> QRect:
        if visible_count <= 0:
            return QRect()
        step = rect.width() / visible_count
        left = rect.left() + round(offset * step)
        right = rect.left() + round((offset + 1) * step)
        if offset < visible_count - 1:
            right -= gap
        return QRect(left, y, max(1, right - left), track_h)

    def _paint_horizontal_strip(self, painter: QPainter, current_idx: int):
        count = len(self.image_labels)
        rect, layouts = self._horizontal_page_rects(current_idx)
        if count <= 0 or rect.width() <= 0 or rect.height() <= 0:
            return

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#202020"))
        painter.drawRoundedRect(rect, 8, 8)

        for index, block in layouts:
            label = self.image_labels[index]
            src = getattr(label, "_source_pixmap", None) or label.pixmap() or getattr(label, "_preview_pixmap", None)
            page_rect = QRect(block)

            if src and not src.isNull():
                painter.drawPixmap(page_rect, src, src.rect())
            else:
                painter.fillRect(page_rect, QColor("#2a2a2a"))
            if index == current_idx:
                painter.fillRect(page_rect, QColor(41, 121, 255, 64))

    def _paint_filmstrip(self, painter: QPainter, current_idx: int):
        tile_h = self._tile_height()
        tile_w = FILMSTRIP_W - TILE_PADDING * 2
        for index, label in enumerate(self.image_labels):
            rect = self._tile_rect(index, tile_h)
            src = getattr(label, "_preview_pixmap", None) or getattr(label, "_source_pixmap", None)
            if src and not src.isNull():
                source_w, source_h = src.width(), src.height()
                scale = max(tile_w / source_w, tile_h / source_h)
                draw_w, draw_h = int(source_w * scale), int(source_h * scale)
                crop_x, crop_y = (draw_w - tile_w) // 2, (draw_h - tile_h) // 2
                src_crop = QRect(
                    int(crop_x / scale),
                    int(crop_y / scale),
                    int(tile_w / scale),
                    int(tile_h / scale),
                )
                painter.drawPixmap(rect, src, src_crop)
            else:
                painter.fillRect(rect, QColor("#2a2a2a"))
            if index == current_idx:
                painter.fillRect(rect, QColor(41, 121, 255, 50))
                pen = QPen(QColor(41, 121, 255, 220))
                pen.setWidth(1)
                painter.setPen(pen)
                painter.drawRect(rect.adjusted(0, 0, -1, -1))
            else:
                painter.setPen(QColor("#0e0e0e"))
                painter.drawLine(rect.left(), rect.bottom() + 1, rect.right(), rect.bottom() + 1)

    def _paint_pages_only_strip(self, painter: QPainter, current_idx: int):
        layouts = self._pages_only_layout(current_idx)
        active_indexes = set(self._current_image_indexes())
        for index, rect, full_h in layouts:
            src = getattr(self.image_labels[index], "_source_pixmap", None) or getattr(self.image_labels[index], "_preview_pixmap", None)
            if src and not src.isNull():
                painter.fillRect(rect, QColor("#202020"))
                scaled = src.scaled(
                    max(1, rect.width()),
                    max(1, full_h),
                    Qt.IgnoreAspectRatio,
                    Qt.SmoothTransformation,
                )
                painter.save()
                painter.setClipRect(rect)
                painter.drawPixmap(rect.left(), rect.top(), scaled)
                painter.restore()
            else:
                painter.fillRect(rect, QColor("#2a2a2a"))
            if index in active_indexes:
                painter.fillRect(rect, QColor(41, 121, 255, 64))
                pen = QPen(QColor(41, 121, 255, 220))
                pen.setWidth(1)
                painter.setPen(pen)
                painter.drawRect(rect.adjusted(0, 0, -1, -1))
            else:
                painter.setPen(QColor("#0e0e0e"))
                painter.drawLine(rect.left(), rect.bottom() + 1, rect.right(), rect.bottom() + 1)

    def _pages_only_layout(self, current_idx: int) -> list[tuple[int, QRect, int]]:
        count = len(self.image_labels)
        if count <= 0:
            return []
        current_idx = max(0, min(count - 1, int(current_idx)))
        available_h = max(40, self.height() - 2 * TILE_PADDING)
        tile_w = max(1, self.width() - TILE_PADDING * 2)
        entries: list[tuple[int, int, int]] = []

        def consumed_height(items: list[tuple[int, int, int]]) -> int:
            if not items:
                return 0
            return sum(height for _idx, height, _full_h in items) + (len(items) - 1) * TILE_GAP

        for index in range(current_idx, count):
            full_h = self._pages_only_page_height(index, tile_w)
            remaining = available_h - consumed_height(entries)
            if remaining <= 0:
                break
            visible_h = min(full_h, remaining)
            if visible_h <= 0:
                break
            entries.append((index, visible_h, full_h))
            if visible_h < full_h:
                break

        prev_index = current_idx - 1
        while prev_index >= 0:
            remaining = available_h - consumed_height(entries)
            if remaining <= 0:
                break
            full_h = self._pages_only_page_height(prev_index, tile_w)
            visible_h = min(full_h, remaining)
            if visible_h <= 0:
                break
            entries.insert(0, (prev_index, visible_h, full_h))
            if visible_h < full_h:
                break
            prev_index -= 1

        layouts: list[tuple[int, QRect, int]] = []
        y = TILE_PADDING
        for index, visible_h, full_h in entries:
            layouts.append((index, QRect(TILE_PADDING, y, tile_w, visible_h), full_h))
            y += visible_h + TILE_GAP
        return layouts

    def _coverage(self, total_content_h: int, view_h: int) -> float:
        return 0.20

    def _window_fracs(self, total_content_h: int, view_h: int):
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

        view_h = self.scroll_area.viewport().height()
        coverage, window_top_frac = self._window_fracs(total_content_h, view_h)
        window_bot_frac = window_top_frac + coverage
        content_top = window_top_frac * total_content_h
        content_bot = window_bot_frac * total_content_h

        painter.save()
        painter.setClipRect(strip_rect)

        cumulative = 0
        for label in self.image_labels:
            img_h = self._scaled_label_height(label)
            img_top = cumulative
            img_bot = cumulative + img_h
            cumulative += img_h

            if img_bot <= content_top or img_top >= content_bot:
                continue

            src = getattr(label, "_preview_pixmap", None) or getattr(label, "_source_pixmap", None)
            src_frac_top = max(0.0, (content_top - img_top) / img_h) if img_h else 0.0
            src_frac_bot = min(1.0, (content_bot - img_top) / img_h) if img_h else 1.0

            dst_top = int((img_top - content_top) / (content_bot - content_top) * strip_h)
            dst_bot = int((img_bot - content_top) / (content_bot - content_top) * strip_h)
            dst_top = max(0, dst_top)
            dst_bot = min(strip_h, dst_bot)
            dst_rect = QRect(strip_x, dst_top, strip_w, dst_bot - dst_top)

            if src and not src.isNull():
                src_w, src_h = src.width(), src.height()
                crop_top = int(src_frac_top * src_h)
                crop_bot = int(src_frac_bot * src_h)
                crop_h = max(1, crop_bot - crop_top)
                src_rect = QRect(0, crop_top, src_w, crop_h)
                painter.drawPixmap(dst_rect, src, src_rect)
            else:
                painter.fillRect(dst_rect, QColor("#2a2a2a"))

        painter.restore()

        scroll_top = self.scroll_area.verticalScrollBar().value()
        indicator_top = int(((scroll_top / total_content_h) - window_top_frac) / coverage * strip_h)
        indicator_h = max(3, int((view_h / total_content_h) / coverage * strip_h))
        indicator_top = max(0, min(strip_h - indicator_h, indicator_top))

        vp_rect = QRect(strip_x, indicator_top, strip_w, indicator_h)
        painter.fillRect(vp_rect, QColor(41, 121, 255, 55))
        pen = QPen(QColor(41, 121, 255, 230))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawRect(vp_rect.adjusted(0, 0, -1, -1))
        self._paint_scene_marks(painter, strip_rect, content_top, content_bot)

    def _paint_scene_marks(self, painter: QPainter, strip_rect: QRect, content_top: float, content_bot: float):
        if not self._scene_marks_visible or not self._scene_marks or not self.image_labels:
            return

        left = strip_rect.left() + 4
        right = strip_rect.right() - 4

        painter.save()
        for _mark, y in self._scene_mark_positions(strip_rect, content_top, content_bot):
            painter.setPen(QPen(QColor("#ff5a5f"), 2))
            painter.drawLine(left, y, right, y)
            painter.setPen(QPen(QColor("#ffc2c4"), 1))
            painter.drawLine(left, y - 2, left + 5, y - 2)
        painter.restore()

    def _scene_mark_positions(self, strip_rect: QRect, content_top: float, content_bot: float) -> list[tuple[dict, int]]:
        if not self._scene_marks_visible or not self._scene_marks or not self.image_labels:
            return []
        total_content_h = self._total_content_height()
        visible_h = max(1.0, content_bot - content_top)
        bottom = max(strip_rect.top(), strip_rect.bottom() - 1)
        positions = []
        for mark in self._scene_marks:
            packed = max(0.0, float(mark.get("packed") or 0.0))
            if self.metrics_provider is not None and hasattr(self.metrics_provider, "packed_to_content_offset"):
                content_y = float(self.metrics_provider.packed_to_content_offset(packed))
            else:
                total = max(1, len(self.image_labels))
                content_y = max(0.0, min(float(total_content_h), (packed / total) * total_content_h))
            if content_y < content_top or content_y > content_bot:
                continue
            frac = (content_y - content_top) / visible_h
            y = strip_rect.top() + int(frac * strip_rect.height())
            y = max(strip_rect.top() + 1, min(bottom, y))
            positions.append((mark, y))
        return positions

    def _scene_mark_at(self, pos: QPoint) -> dict | None:
        if pos.x() < FILMSTRIP_W or not self._scene_marks:
            return None
        strip_rect = QRect(FILMSTRIP_W, 0, IMAGE_STRIP_W, self.height())
        total_content_h = self._total_content_height()
        if total_content_h <= 0:
            return None
        view_h = self.scroll_area.viewport().height()
        coverage, window_top_frac = self._window_fracs(total_content_h, view_h)
        window_bot_frac = window_top_frac + coverage
        content_top = window_top_frac * total_content_h
        content_bot = window_bot_frac * total_content_h
        tolerance = 7
        closest = None
        closest_distance = None
        for mark, y in self._scene_mark_positions(strip_rect, content_top, content_bot):
            distance = abs(pos.y() - y)
            if distance > tolerance:
                continue
            if closest_distance is None or distance < closest_distance:
                closest = mark
                closest_distance = distance
        return closest

    def _jump_to_image(self, index: int):
        if not self.image_labels or index >= len(self.image_labels):
            return
        if self.metrics_provider is not None and hasattr(self.metrics_provider, "jump_to_image_index"):
            self.metrics_provider.jump_to_image_index(index)
            return
        if self.metrics_provider is not None:
            cumulative = self.metrics_provider.cumulative_height_before(index)
        else:
            cumulative = sum(self._scaled_label_height(self.image_labels[i]) for i in range(index))
        bar = self.scroll_area.verticalScrollBar()
        bar.setValue(max(0, min(cumulative, bar.maximum())))

    def _scrub_strip_to_y(self, widget_y: int):
        if self._display_mode == "horizontal":
            return
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
        if self._display_mode == "horizontal":
            index = self._horizontal_index_at(pos)
            if index is not None:
                self._jump_to_image(index)
            return
        if self._display_mode == "pages_only":
            index = self._pages_only_index_at(pos)
            if index is not None:
                self._jump_to_image(index)
            return
        strip_w = self.width() if self._display_mode == "pages_only" else FILMSTRIP_W
        if pos.x() < strip_w:
            idx = self._tile_index_at(pos)
            if idx is not None:
                self._jump_to_image(idx)
            return

    def _pages_only_index_at(self, pos: QPoint) -> int | None:
        if pos.x() < 0 or pos.x() >= self.width():
            return None
        current_idx = self._current_image_index()
        for index, rect, _full_h in self._pages_only_layout(current_idx):
            if rect.contains(pos):
                return index
        return None

        scene_mark = self._scene_mark_at(pos)
        if scene_mark is not None and callable(self.scene_jump_callback):
            self.scene_jump_callback(float(scene_mark.get("packed") or 0.0))
            return

        self._scrub_strip_to_y(pos.y())

    def _horizontal_index_at(self, pos: QPoint) -> int | None:
        if not self.image_labels:
            return None
        current_idx = self._current_image_index()
        rect, layouts = self._horizontal_page_rects(current_idx)
        if pos.y() < rect.top() or pos.y() > rect.bottom():
            return None
        for index, block in layouts:
            if block.contains(pos):
                return index
        return None

    def resizeEvent(self, event):
        self.update()
        super().resizeEvent(event)

    def sizeHint(self) -> QSize:
        if self._display_mode == "horizontal":
            return QSize(0, HORIZONTAL_PREVIEW_H)
        if self._display_mode == "pages_only":
            return QSize(PAGE_COLUMN_W, 320)
        return QSize(PREVIEW_W, 320)

    def minimumSizeHint(self) -> QSize:
        if self._display_mode == "horizontal":
            return QSize(0, HORIZONTAL_PREVIEW_H)
        if self._display_mode == "pages_only":
            return QSize(PAGE_COLUMN_W, 160)
        return QSize(PREVIEW_W, 160)
