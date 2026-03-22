import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QImage, QImageReader, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from core.app_logging import get_logger
from gui.common.styles import (
    VIEWER_RESUME_CONTINUE_BUTTON_STYLE,
    VIEWER_RESUME_DIALOG_STYLE,
    VIEWER_RESUME_RESTART_BUTTON_STYLE,
)


FILMSTRIP_W = 40
IMAGE_STRIP_W = 50
PREVIEW_W = FILMSTRIP_W + IMAGE_STRIP_W

SPECIAL_CHAPTER_RE = re.compile(r"\b\d+\.\d+\b")

TILE_GAP = 2
TILE_PADDING = 2
TILE_MIN_H = 14
TILE_MAX_H = 120

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
                    avg, variance, chroma, is_blank = self._blank_row_metrics(image, sample_y)
                    sample_rows.append(
                        f"y={sample_y}:avg={avg:.1f},var={variance:.1f},chroma={chroma:.1f},blank={is_blank}"
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

        return ranges

    def _blank_row_metrics(self, image: QImage, y: int, sample_step: int = 12) -> tuple[float, float, float, bool]:
        width = image.width()
        if width <= 0:
            return (0.0, 0.0, 0.0, True)

        step = max(sample_step, width // 160)
        total = 0
        total_sq = 0
        total_chroma = 0
        count = 0

        for x in range(0, width, step):
            rgb = image.pixel(x, y)
            red = (rgb >> 16) & 0xFF
            green = (rgb >> 8) & 0xFF
            blue = rgb & 0xFF
            lum = (299 * red + 587 * green + 114 * blue) // 255
            total += lum
            total_sq += lum * lum
            total_chroma += max(red, green, blue) - min(red, green, blue)
            count += 1

        if count == 0:
            return (0.0, 0.0, 0.0, True)

        avg = total / count
        variance = (total_sq / count) - (avg * avg)
        avg_chroma = total_chroma / count

        is_extreme = avg < 120 or avg > 880
        is_uniform = variance < 3000
        is_soft_fade = variance < 900 and avg_chroma < 28
        return (avg, variance, avg_chroma, (is_extreme and is_uniform) or is_soft_fade)

    def _is_blank_row(self, image: QImage, y: int, sample_step: int = 12) -> bool:
        return self._blank_row_metrics(image, y, sample_step)[3]


class ChapterPreview(QWidget):

    def __init__(self, scroll_area: QScrollArea, metrics_provider=None, scene_jump_callback=None, parent=None):
        super().__init__(parent)
        self.scroll_area = scroll_area
        self.metrics_provider = metrics_provider
        self.scene_jump_callback = scene_jump_callback
        self.image_labels = []
        self.setFixedWidth(PREVIEW_W)
        self.setCursor(Qt.PointingHandCursor)
        self._dragging = False
        self._zoom = 1.0
        self._scene_marks: list[dict] = []
        self._scene_marks_visible = True

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
        for index, label in enumerate(self.image_labels):
            height = self._scaled_label_height(label)
            if cumulative + height > scroll_top:
                return index
            cumulative += height
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
            return

        scene_mark = self._scene_mark_at(pos)
        if scene_mark is not None and callable(self.scene_jump_callback):
            self.scene_jump_callback(float(scene_mark.get("packed") or 0.0))
            return

        self._scrub_strip_to_y(pos.y())

    def resizeEvent(self, event):
        self.update()
        super().resizeEvent(event)
