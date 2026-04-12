import os
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from core.app_logging import get_logger
from core.hybrid_models import ViewerChapterSource
from core.chapter_identity import build_remote_chapter_key
from core.chapter_storage import (
    chapter_is_editable,
    chapter_storage_path,
    chapter_has_text_payload,
    count_chapter_images,
    list_chapter_image_paths,
    list_series_chapters,
)
from requests.exceptions import RequestException
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QToolButton, QMessageBox, QGridLayout, QFrame, QSizePolicy
)
from PySide6.QtGui import QIcon, QPixmap, QPainter, QPainterPath, QFont, QPen, QColor, QImageReader, QImage
from PySide6.QtCore import Qt, QPoint, QSize, QTimer, QObject, Signal, QCoreApplication

import qtawesome as qta

from gui.common.chapter_selection import (
    apply_select_icon,
    refresh_selector_visibility,
    selector_buttons,
    set_selector_visibility,
    sync_selector_checked_state,
)
from gui.common.chapter_utils import SPECIAL_CHAPTER_RE, chapter_sort_key
from gui.common.scene_bookmark_dialog import AllSceneBookmarksDialog, SceneBookmarksDialog
from gui.common.detail_shared import ACTION_BTN_H, ACTION_BTN_W, BATCH_ACTION_BTN_H, RADIUS, THUMB_H, THUMB_W
from gui.common.strings import t
from gui.common.styles import (
    BG,
    BORDER,
    ACCENT,
    TEXT_SOFT,
    TEXT_MUTED,
    sized_button_style,
    BATCH_BAR_STYLE,
    BATCH_LABEL_STYLE,
    CHAPTER_LIST_WIDGET_STYLE,
    CHAPTER_ROW_STYLE,
    CHAPTER_SCROLL_AREA_STYLE,
    CHAPTER_SELECT_SLOT_STYLE,
    CHAPTER_TOOL_BUTTON_STYLE,
    DELETE_BUTTON_STYLE,
    DETAIL_TITLE_STYLE,
    HERO_PANEL_STYLE,
    LAST_READ_ICON_STYLE,
    MINIMAL_FILTER_BUTTON_BLUE_CHECKED_STYLE,
    MINIMAL_FILTER_BUTTON_GOLD_CHECKED_STYLE,
    MINIMAL_FILTER_BUTTON_STYLE,
    NEW_CHIP_STYLE,
    PAGE_BG_STYLE,
    PRIMARY_ACTION_BUTTON_STYLE,
    SECONDARY_ACTION_BUTTON_STYLE,
    SECTION_CAPTION_STYLE,
    SECTION_HEADER_PANEL_STYLE,
    SUBTLE_META_LABEL_STYLE,
    TOOLBAR_TEXT_BUTTON_STYLE,
    TRANSPARENT_BG_STYLE,
    WARNING_META_LABEL_STYLE,
    detail_thumb_style,
    chapter_name_style,
    TOP_BAR_STYLE,
    SECONDARY_META_LABEL_STYLE,
    STATUS_LABEL_STYLE,
    SURFACE,
    TEXT,
    TEXT_MUTED_BODY_STYLE,
)
from gui.downloader.download_widgets import SpinnerCircle
from gui.downloader.helpers import sanitize_webtoon_name
from core.update_utils import cooldown_remaining
from scrapers.base import ScraperError
from scrapers.registry import get_scraper, is_scraper_enabled_for_url
from stores.chapter_ref_store import get_instance as get_chapter_ref_store
from stores.scene_bookmark_store import get_instance as get_scene_bookmark_store
from stores.tracked_titles_store import get_instance as get_tracked_titles_store
from stores.webtoon_settings_store import get_instance as get_webtoon_settings
from gui.viewer.remote_read_service import RemoteReadService
from gui.library.edit_webtoon_dialog import EditWebtoonDialog
from gui.library.chapter_editor_dialog import ChapterEditorDialog
from stores.scraper_settings_store import load_scraper_default_config
from stores.settings_store import load_library_path

logger = get_logger(__name__)


MANGA_PREVIEW_PIXMAP_CACHE_LIMIT = 192
MANGA_PREVIEW_TILE_BATCH_SIZE = 24
MANGA_PREVIEW_INFLIGHT_LIMIT = 4


# Small circular progress indicator
class ProgressCircle(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._percent = 0
        self.setFixedSize(32, 32)

    def set_percent(self, percent: int):
        self._percent = max(0, min(100, int(percent)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = self.rect().adjusted(2, 2, -2, -2)

        # Background ring
        painter.setPen(QPen(QColor(BORDER), 3))
        painter.drawEllipse(rect)

        # Progress arc (green)
        if self._percent > 0:
            pen = QPen(QColor(ACCENT), 3)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            start_angle = -90 * 16
            span_angle = int(self._percent / 100.0 * 360 * 16)
            painter.drawArc(rect, start_angle, span_angle)

        # Center text
        font = painter.font()
        font.setPixelSize(9)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(TEXT))
        painter.drawText(rect, Qt.AlignCenter, f"{self._percent}%")


class RemoteSeriesLoader(QObject):
    loaded = Signal(int, object, str)

    @staticmethod
    def _format_request_error(exc: Exception) -> str:
        message = str(exc).strip()
        if "read timed out" in message.casefold():
            return t("library.detail.request_timed_out")
        return message or t("library.detail.request_failed")

    def _is_shutting_down(self) -> bool:
        app = QCoreApplication.instance()
        return bool(app is None or QCoreApplication.closingDown())

    def _emit_loaded(self, request_id: int, series, error: str) -> None:
        if self._is_shutting_down():
            return
        try:
            self.loaded.emit(request_id, series, error)
        except RuntimeError:
            logger.info("Skipping remote series result delivery during detail-page shutdown")

    def load(self, request_id: int, source_url: str, source_config: dict | None = None):
        def worker():
            if self._is_shutting_down():
                return
            try:
                scraper = get_scraper(source_url)
                if scraper is None:
                    raise ScraperError("This series source does not support chapter checks.")
                series_url = source_url if not scraper.is_chapter_url(source_url) else scraper.series_url_from_chapter_url(source_url)
                scraper.apply_source_config(source_config)
                series = scraper.get_series_info(series_url)
                self._emit_loaded(request_id, series, "")
            except ScraperError as e:
                self._emit_loaded(request_id, None, str(e))
            except RequestException as e:
                logger.warning("Remote chapter lookup request failed for %s: %s", source_url, e)
                self._emit_loaded(request_id, None, self._format_request_error(e))
            except Exception as e:
                if self._is_shutting_down() and "interpreter shutdown" in str(e).casefold():
                    logger.info("Skipping remote chapter lookup failure because the app is shutting down")
                    return
                logger.exception("Unexpected remote chapter lookup failure")
                self._emit_loaded(request_id, None, str(e))

        threading.Thread(target=worker, daemon=True).start()


class RemoteChapterReadLoader(QObject):
    loaded = Signal(int, object, float, str)

    def __init__(self, progress_store, parent=None):
        super().__init__(parent)
        self._service = RemoteReadService(progress_store)

    def load(self, request_id: int, source_url: str, series, chapter):
        def worker():
            try:
                url = str(source_url or getattr(series, "url", "") or getattr(chapter, "url", "") or "").strip()
                if not url:
                    raise ScraperError("This title does not expose a readable source URL.")
                scraper = get_scraper(url)
                if scraper is None:
                    raise ScraperError("This title does not support remote reading.")
                source_site = getattr(scraper, "site_name", "") or ""
                scraper.apply_source_config(load_scraper_default_config(source_site))
                owner, chapter_sources, start_chapter_key, start_scroll = self._service.prepare_hybrid_title(
                    scraper,
                    series,
                    chapter,
                )
                self.loaded.emit(
                    request_id,
                    (owner, chapter_sources, start_chapter_key),
                    float(start_scroll or 0.0),
                    "",
                )
            except ScraperError as e:
                self.loaded.emit(request_id, None, 0.0, str(e))
            except Exception as e:
                logger.exception("Unexpected library remote-read preparation failure")
                self.loaded.emit(request_id, None, 0.0, str(e))

        threading.Thread(target=worker, daemon=True).start()


class MangaPreviewImageLoader(QObject):
    loaded = Signal(int, int, QImage)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._queued: set[tuple[int, int]] = set()

    def load(self, generation: int, index: int, path: str, width: int, height: int, radius: int = 12) -> None:
        key = (int(generation), int(index))
        if key in self._queued:
            return
        self._queued.add(key)

        def worker():
            try:
                image = _scaled_preview_image(path, width, height, radius)
                self.loaded.emit(int(generation), int(index), image)
            finally:
                self._queued.discard(key)

        self._executor.submit(worker)


def _page_sort_key(name: str):
    match = re.search(r"(\d+(?:\.\d+)?)", name)
    if match:
        try:
            return (0, float(match.group(1)), name.lower())
        except Exception:
            pass
    return (1, float("inf"), name.lower())


def _scaled_preview_image(path: str, width: int, height: int, radius: int = 12) -> QImage:
    image = QImage()
    reader = QImageReader(path)
    size = reader.size()
    if size.isValid() and size.width() > 0 and size.height() > 0:
        src_w = size.width()
        src_h = size.height()
        scale = max(width / src_w, height / src_h)
        target_w = max(width, int(src_w * scale))
        target_h = max(height, int(src_h * scale))
        reader.setScaledSize(QSize(target_w, target_h))
        image = reader.read()

    if image.isNull():
        image = QImage(path)
    if image.isNull():
        placeholder = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
        placeholder.fill(QColor(SURFACE))
        painter = QPainter(placeholder)
        painter.setPen(QColor(TEXT_MUTED))
        painter.drawText(placeholder.rect(), Qt.AlignCenter, t("library.detail.preview_unavailable"))
        painter.end()
        return placeholder

    pixmap = QPixmap.fromImage(image)
    scaled = pixmap.scaled(width, height, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    x = max(0, (scaled.width() - width) // 2)
    y = max(0, (scaled.height() - height) // 2)
    cropped = scaled.copy(x, y, width, height)
    rounded = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    rounded.fill(Qt.transparent)
    painter = QPainter(rounded)
    painter.setRenderHint(QPainter.Antialiasing)
    path_shape = QPainterPath()
    path_shape.addRoundedRect(0, 0, width, height, radius, radius)
    painter.setClipPath(path_shape)
    painter.drawPixmap(0, 0, cropped)
    painter.end()
    return rounded


def _scaled_preview_pixmap(path: str, width: int, height: int, radius: int = 12) -> QPixmap:
    return QPixmap.fromImage(_scaled_preview_image(path, width, height, radius))


def _preview_placeholder_pixmap(width: int, height: int, radius: int = 12) -> QPixmap:
    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    path_shape = QPainterPath()
    path_shape.addRoundedRect(0, 0, width, height, radius, radius)
    painter.fillPath(path_shape, QColor(SURFACE))
    painter.setPen(QPen(QColor(BORDER), 1))
    painter.drawPath(path_shape)
    painter.end()
    return pixmap


class MangaPageTile(QFrame):
    clicked = Signal(int)

    PREVIEW_WIDTH = 108
    PREVIEW_HEIGHT = 152

    def __init__(self, image_path: str, page_index: int, parent=None):
        super().__init__(parent)
        self._page_index = int(page_index)
        self.image_path = str(image_path or "")
        self._preview_loaded = False
        self._scene_count = 0
        self.setCursor(Qt.PointingHandCursor)
        self.setFrameShape(QFrame.NoFrame)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFixedSize(self.PREVIEW_WIDTH, self.PREVIEW_HEIGHT)
        self.setStyleSheet("QFrame { background: transparent; border: none; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setFixedSize(self.PREVIEW_WIDTH, self.PREVIEW_HEIGHT)
        self.preview_label.setPixmap(_preview_placeholder_pixmap(self.PREVIEW_WIDTH, self.PREVIEW_HEIGHT))
        self._apply_preview_frame()
        layout.addWidget(self.preview_label, 0, Qt.AlignCenter)

    def set_preview_pixmap(self, pixmap: QPixmap) -> None:
        if pixmap.isNull():
            return
        self.preview_label.setPixmap(pixmap)
        self._preview_loaded = True

    @property
    def preview_loaded(self) -> bool:
        return self._preview_loaded

    def set_scene_count(self, count: int) -> None:
        self._scene_count = max(0, int(count))
        self._apply_preview_frame()

    def _apply_preview_frame(self) -> None:
        border = f"2px solid {ACCENT}" if self._scene_count > 0 else "none"
        self.preview_label.setStyleSheet(
            f"""
            QLabel {{
                background: transparent;
                color: {TEXT};
                border: {border};
                border-radius: 12px;
            }}
            """
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._page_index)
        super().mousePressEvent(event)


class DetailPage(QWidget):

    def __init__(self, main_window):
        super().__init__()
        self.main_window    = main_window
        self.webtoon        = None
        self.progress_store = None
        self.progress_map   = {}
        self.hide_specials  = False
        self.show_only_bookmarked = False
        self.show_only_scene_marks = False
        self.bookmarked_chapters = set()
        self.scene_bookmark_counts = {}
        self.selected_chapters = set()
        self.selected_remote_chapter_urls = set()
        self.latest_new_chapter = None
        self.webtoon_bookmarked = False
        self.settings_store = get_webtoon_settings()
        self.chapter_ref_store = get_chapter_ref_store()
        self.scene_bookmark_store = get_scene_bookmark_store()
        self.tracked_titles_store = get_tracked_titles_store()
        self._update_service = None
        self._manual_download_service = None
        self._chapter_display_order = []
        self._chapter_dir_cache: tuple[str, int, list[str]] | None = None
        self._update_progress_current = 0
        self._update_progress_total = 0
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self._sync_update_button)
        self._update_timer.start(250)
        self._pending_disk_refresh = False
        self._disk_refresh_timer = QTimer(self)
        self._disk_refresh_timer.setSingleShot(True)
        self._disk_refresh_timer.timeout.connect(self._flush_disk_refresh)
        self._remote_series_loader = RemoteSeriesLoader(self)
        self._remote_series_loader.loaded.connect(self._on_remote_series_loaded)
        self._remote_read_loader = RemoteChapterReadLoader(getattr(main_window.library, "progress_store", None), self)
        self._remote_read_loader.loaded.connect(self._on_remote_chapter_loaded)
        self._remote_request_id = 0
        self._remote_read_request_id = 0
        self._remote_series = None
        self._remote_status = ""
        self._new_remote_chapters = []
        self._pending_remote_chapter_urls = set()
        self._remote_selection_label = None
        self._remote_download_selected_btn = None
        self._remote_clear_selection_btn = None
        self._manga_preview_active = False
        self._manga_preview_chapter = ""
        self._manga_preview_index = -1
        self._manga_preview_columns = 1
        self._manga_preview_tiles: list[MangaPageTile] = []
        self._manga_preview_pending_tiles: list[tuple[int, str, int, int, int]] = []
        self._manga_preview_queue: list[int] = []
        self._manga_preview_queued: set[int] = set()
        self._manga_preview_loading: set[int] = set()
        self._manga_preview_generation = 0
        self._manga_preview_pixmap_cache: OrderedDict[tuple[str, int, int, int, int, int], QPixmap] = OrderedDict()
        self._manga_preview_loader_timer = QTimer(self)
        self._manga_preview_loader_timer.setSingleShot(True)
        self._manga_preview_loader_timer.timeout.connect(self._drain_manga_preview_queue)
        self._manga_preview_image_loader = MangaPreviewImageLoader(self)
        self._manga_preview_image_loader.loaded.connect(self._on_manga_preview_image_loaded)
        self._manga_preview_build_timer = QTimer(self)
        self._manga_preview_build_timer.setSingleShot(True)
        self._manga_preview_build_timer.timeout.connect(self._drain_manga_preview_tile_build)

        self.setStyleSheet(PAGE_BG_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Top bar
        top_bar = QWidget()
        top_bar.setFixedHeight(52)
        top_bar.setStyleSheet(TOP_BAR_STYLE)
        tb_layout = QHBoxLayout(top_bar)
        tb_layout.setContentsMargins(16, 0, 16, 0)

        self.back_btn = QPushButton(t("library.detail.back"))
        self.back_btn.setIcon(qta.icon("fa5s.arrow-left", color=TEXT_MUTED))
        self.back_btn.setIconSize(QSize(14, 14))
        self.back_btn.setCursor(Qt.PointingHandCursor)
        self.back_btn.setStyleSheet(TOOLBAR_TEXT_BUTTON_STYLE)
        self.back_btn.clicked.connect(self._go_back)

        self.edit_btn = QPushButton(t("library.detail.edit"))
        self.edit_btn.setIcon(qta.icon("fa5s.edit", color=TEXT_MUTED))
        self.edit_btn.setIconSize(QSize(14, 14))
        self.edit_btn.setCursor(Qt.PointingHandCursor)
        self.edit_btn.setStyleSheet(TOOLBAR_TEXT_BUTTON_STYLE)
        self.edit_btn.clicked.connect(self._open_edit_dialog)

        self.bookmark_btn = QPushButton(t("library.detail.bookmark"))
        self.bookmark_btn.setIconSize(QSize(14, 14))
        self.bookmark_btn.setCursor(Qt.PointingHandCursor)
        self.bookmark_btn.setStyleSheet(TOOLBAR_TEXT_BUTTON_STYLE)
        self.bookmark_btn.clicked.connect(self._toggle_webtoon_bookmark)

        self.saved_marks_btn = QPushButton(t("library.detail.saved"))
        self.saved_marks_btn.setIcon(qta.icon("fa5s.bookmark", color=TEXT_MUTED))
        self.saved_marks_btn.setIconSize(QSize(14, 14))
        self.saved_marks_btn.setCursor(Qt.PointingHandCursor)
        self.saved_marks_btn.setStyleSheet(TOOLBAR_TEXT_BUTTON_STYLE)
        self.saved_marks_btn.clicked.connect(self._open_all_scene_bookmarks)

        tb_layout.addWidget(self.back_btn)
        tb_layout.addStretch()
        tb_layout.addWidget(self.saved_marks_btn)
        tb_layout.addWidget(self.bookmark_btn)
        tb_layout.addWidget(self.edit_btn)
        root.addWidget(top_bar)

        # Hero
        self.hero_panel = QWidget()
        self.hero_panel.setStyleSheet(HERO_PANEL_STYLE)
        hero_layout = QHBoxLayout(self.hero_panel)
        hero_layout.setContentsMargins(32, 28, 32, 28)
        hero_layout.setSpacing(28)

        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(THUMB_W, THUMB_H)
        self.thumb_label.setStyleSheet(detail_thumb_style(RADIUS))

        info_widget = QWidget()
        info_widget.setStyleSheet(TRANSPARENT_BG_STYLE)
        info_layout = QVBoxLayout(info_widget)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(10)
        info_layout.setAlignment(Qt.AlignTop)

        self.title_label = QLabel()
        self.title_label.setWordWrap(False)
        self.title_label.setStyleSheet(DETAIL_TITLE_STYLE)
        self.title_label.setFont(QFont("Segoe UI", 24, QFont.Bold))
        self.title_label.setMinimumHeight(36)

        self.last_read_label = QLabel()
        self.last_read_label.setStyleSheet(SUBTLE_META_LABEL_STYLE)

        self.chapter_count_label = QLabel()
        self.chapter_count_label.setStyleSheet(SECONDARY_META_LABEL_STYLE)

        self.remote_status_label = QLabel("")
        self.remote_status_label.setStyleSheet(WARNING_META_LABEL_STYLE)
        self.remote_status_label.hide()

        self.update_progress_label = QLabel("")
        self.update_progress_label.setStyleSheet(WARNING_META_LABEL_STYLE)
        self.update_progress_label.hide()

        self.update_progress_circle = ProgressCircle()
        self.update_progress_circle.hide()

        self.continue_btn = QPushButton(t("library.detail.continue"))
        self.continue_btn.setIcon(qta.icon("fa5s.play", color=BG))
        self.continue_btn.setIconSize(QSize(12, 12))
        self.continue_btn.setCursor(Qt.PointingHandCursor)
        self.continue_btn.setFixedSize(ACTION_BTN_W, ACTION_BTN_H)
        self.continue_btn.setStyleSheet(PRIMARY_ACTION_BUTTON_STYLE)
        self.continue_btn.clicked.connect(self._continue_reading)
        self.continue_btn.hide()

        self.start_btn = QPushButton(t("library.detail.start"))
        self.start_btn.setIcon(qta.icon("fa5s.step-backward", color=BG))
        self.start_btn.setIconSize(QSize(12, 12))
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.setFixedSize(ACTION_BTN_W, ACTION_BTN_H)
        self.start_btn.setStyleSheet(PRIMARY_ACTION_BUTTON_STYLE)
        self.start_btn.clicked.connect(self._start_from_beginning)

        self.update_btn = QPushButton(t("library.detail.update"))
        self.update_btn.setIcon(qta.icon("fa5s.sync", color=TEXT))
        self.update_btn.setIconSize(QSize(12, 12))
        self.update_btn.setCursor(Qt.PointingHandCursor)
        self.update_btn.setFixedSize(ACTION_BTN_W, ACTION_BTN_H)
        self.update_btn.setStyleSheet(SECONDARY_ACTION_BUTTON_STYLE)
        self.update_btn.clicked.connect(self._start_update)
        self.update_btn.hide()

        self.download_new_btn = QPushButton(t("library.detail.download_new"))
        self.download_new_btn.setIcon(qta.icon("fa5s.download", color=TEXT))
        self.download_new_btn.setIconSize(QSize(12, 12))
        self.download_new_btn.setCursor(Qt.PointingHandCursor)
        self.download_new_btn.setFixedSize(ACTION_BTN_W, ACTION_BTN_H)
        self.download_new_btn.setStyleSheet(SECONDARY_ACTION_BUTTON_STYLE)
        self.download_new_btn.clicked.connect(self._download_all_new_chapters)
        self.download_new_btn.hide()

        info_layout.addWidget(self.title_label)
        info_layout.addWidget(self.last_read_label)
        info_layout.addWidget(self.chapter_count_label)
        info_layout.addWidget(self.remote_status_label)
        progress_row = QHBoxLayout()
        progress_row.setContentsMargins(0, 0, 0, 0)
        progress_row.setSpacing(8)
        progress_row.addWidget(self.update_progress_circle, 0, Qt.AlignVCenter)
        progress_row.addWidget(self.update_progress_label, 0, Qt.AlignVCenter)
        progress_row.addStretch()
        info_layout.addLayout(progress_row)
        info_layout.addSpacing(12)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addWidget(self.continue_btn)
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.download_new_btn)
        btn_row.addWidget(self.update_btn)
        btn_row.addStretch()
        info_layout.addLayout(btn_row)

        hero_layout.addWidget(self.thumb_label)
        hero_layout.addWidget(info_widget, 1)
        root.addWidget(self.hero_panel)

        # Section header
        self.section_header = QWidget()
        self.section_header.setStyleSheet(SECTION_HEADER_PANEL_STYLE)

        sh_layout = QHBoxLayout(self.section_header)
        sh_layout.setContentsMargins(32, 20, 32, 8)

        self.section_caption_label = QLabel(t("library.detail.chapters"))
        self.section_caption_label.setStyleSheet(SECTION_CAPTION_STYLE)

        self.sort_btn = QPushButton(t("library.detail.sort_latest"))
        self.sort_btn.setIcon(qta.icon("fa5s.sort-amount-down", color=TEXT_MUTED))
        self.sort_btn.setIconSize(QSize(12, 12))
        self.sort_btn.setCursor(Qt.PointingHandCursor)
        self.sort_btn.setFixedHeight(24)
        self.sort_btn.setStyleSheet(MINIMAL_FILTER_BUTTON_STYLE)
        self.sort_latest_first = True
        self.sort_btn.clicked.connect(self._toggle_sort)

        self.hide_specials_btn = QPushButton(t("library.detail.hide_filler"))
        self.hide_specials_btn.setIcon(qta.icon("fa5s.eye-slash", color=TEXT_MUTED))
        self.hide_specials_btn.setIconSize(QSize(12, 12))
        self.hide_specials_btn.setCursor(Qt.PointingHandCursor)
        self.hide_specials_btn.setCheckable(True)
        self.hide_specials_btn.setFixedHeight(24)
        self.hide_specials_btn.setStyleSheet(MINIMAL_FILTER_BUTTON_BLUE_CHECKED_STYLE)
        self.hide_specials_btn.clicked.connect(self._toggle_hide_specials)

        self.bookmarks_filter_btn = QPushButton(t("library.detail.bookmarked"))
        self.bookmarks_filter_btn.setIcon(qta.icon("fa5s.star", color=TEXT_MUTED))
        self.bookmarks_filter_btn.setIconSize(QSize(12, 12))
        self.bookmarks_filter_btn.setCursor(Qt.PointingHandCursor)
        self.bookmarks_filter_btn.setCheckable(True)
        self.bookmarks_filter_btn.setFixedHeight(24)
        self.bookmarks_filter_btn.setStyleSheet(MINIMAL_FILTER_BUTTON_GOLD_CHECKED_STYLE)
        self.bookmarks_filter_btn.clicked.connect(self._toggle_bookmarks_filter)

        self.scene_marks_filter_btn = QPushButton(t("library.detail.scenes"))
        self.scene_marks_filter_btn.setIcon(qta.icon("fa5s.map-marker-alt", color=TEXT_MUTED))
        self.scene_marks_filter_btn.setIconSize(QSize(12, 12))
        self.scene_marks_filter_btn.setCursor(Qt.PointingHandCursor)
        self.scene_marks_filter_btn.setCheckable(True)
        self.scene_marks_filter_btn.setFixedHeight(24)
        self.scene_marks_filter_btn.setStyleSheet(MINIMAL_FILTER_BUTTON_BLUE_CHECKED_STYLE)
        self.scene_marks_filter_btn.clicked.connect(self._toggle_scene_marks_filter)

        sh_layout.addWidget(self.section_caption_label)
        sh_layout.addStretch()
        sh_layout.addWidget(self.scene_marks_filter_btn)
        sh_layout.addSpacing(6)
        sh_layout.addWidget(self.bookmarks_filter_btn)
        sh_layout.addSpacing(6)
        sh_layout.addWidget(self.hide_specials_btn)
        sh_layout.addSpacing(6)
        sh_layout.addWidget(self.sort_btn)
        root.addWidget(self.section_header)

        self.chapter_batch_bar = QWidget()
        self.chapter_batch_bar.setStyleSheet(BATCH_BAR_STYLE)
        batch_layout = QHBoxLayout(self.chapter_batch_bar)
        batch_layout.setContentsMargins(32, 10, 32, 10)
        batch_layout.setSpacing(10)

        self.chapter_batch_label = QLabel("")
        self.chapter_batch_label.setStyleSheet(BATCH_LABEL_STYLE)
        batch_layout.addWidget(self.chapter_batch_label)

        chapter_batch_btn_style = sized_button_style(SECONDARY_ACTION_BUTTON_STYLE, BATCH_ACTION_BTN_H)
        chapter_delete_btn_style = sized_button_style(DELETE_BUTTON_STYLE, BATCH_ACTION_BTN_H)
        self.select_all_chapters_btn = QPushButton(t("library.detail.select_all"))
        self.select_all_chapters_btn.setStyleSheet(chapter_batch_btn_style)
        self.select_all_chapters_btn.clicked.connect(self._select_all_chapters)
        batch_layout.addWidget(self.select_all_chapters_btn)

        self.mark_read_btn = QPushButton(t("library.detail.mark_read"))
        self.mark_read_btn.setStyleSheet(chapter_batch_btn_style)
        self.mark_read_btn.clicked.connect(self._mark_selected_chapters_read)
        batch_layout.addWidget(self.mark_read_btn)

        self.mark_unread_btn = QPushButton(t("library.detail.mark_unread"))
        self.mark_unread_btn.setStyleSheet(chapter_batch_btn_style)
        self.mark_unread_btn.clicked.connect(self._mark_selected_chapters_unread)
        batch_layout.addWidget(self.mark_unread_btn)

        self.delete_chapters_btn = QPushButton(t("library.detail.delete"))
        self.delete_chapters_btn.setStyleSheet(chapter_delete_btn_style)
        self.delete_chapters_btn.clicked.connect(self._delete_selected_chapters)
        batch_layout.addWidget(self.delete_chapters_btn)

        self.clear_chapter_selection_btn = QPushButton(t("library.detail.clear"))
        self.clear_chapter_selection_btn.setStyleSheet(chapter_batch_btn_style)
        self.clear_chapter_selection_btn.clicked.connect(self._clear_chapter_selection)
        batch_layout.addWidget(self.clear_chapter_selection_btn)
        batch_layout.addStretch()

        self.chapter_batch_bar.hide()

        # Chapter list
        self.chapter_scroll = QScrollArea()
        self.chapter_scroll.setWidgetResizable(True)
        self.chapter_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chapter_scroll.setStyleSheet(CHAPTER_SCROLL_AREA_STYLE)

        self.chapter_list_widget = QWidget()
        self.chapter_list_widget.setStyleSheet(CHAPTER_LIST_WIDGET_STYLE)
        self.chapter_list_layout = QVBoxLayout(self.chapter_list_widget)
        self.chapter_list_layout.setContentsMargins(32, 0, 32, 24)
        self.chapter_list_layout.setSpacing(0)
        self.chapter_list_layout.setAlignment(Qt.AlignTop)

        self.chapter_scroll.setWidget(self.chapter_list_widget)
        root.addWidget(self.chapter_scroll, 1)
        root.addWidget(self.chapter_batch_bar)

        self.manga_preview_panel = QWidget()
        self.manga_preview_panel.setStyleSheet(PAGE_BG_STYLE)
        preview_layout = QVBoxLayout(self.manga_preview_panel)
        preview_layout.setContentsMargins(32, 20, 32, 20)
        preview_layout.setSpacing(12)

        self.manga_preview_scroll = QScrollArea()
        self.manga_preview_scroll.setWidgetResizable(True)
        self.manga_preview_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.manga_preview_scroll.setStyleSheet(CHAPTER_SCROLL_AREA_STYLE)
        self.manga_preview_scroll.verticalScrollBar().valueChanged.connect(self._schedule_visible_manga_preview_tiles)
        preview_layout.addWidget(self.manga_preview_scroll, 1)

        self.manga_preview_content = QWidget()
        self.manga_preview_grid = QGridLayout(self.manga_preview_content)
        self.manga_preview_grid.setContentsMargins(0, 8, 0, 8)
        self.manga_preview_grid.setHorizontalSpacing(12)
        self.manga_preview_grid.setVerticalSpacing(12)
        self.manga_preview_grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.manga_preview_scroll.setWidget(self.manga_preview_content)
        self.manga_preview_panel.hide()
        root.addWidget(self.manga_preview_panel, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_manga_preview_layout()

    def _chapter_selection_visible(self) -> bool:
        return bool(self.selected_chapters)

    def _remote_chapter_selection_visible(self) -> bool:
        return bool(self.selected_remote_chapter_urls)

    def _is_remote_library_title(self) -> bool:
        if self.webtoon is None:
            return False
        return bool(getattr(self.webtoon, "_tracked_library_placeholder", False))

    def _detail_origin_target(self) -> str:
        return "library"

    def _tracked_library_row(self) -> dict:
        if not self._is_remote_library_title():
            return {}
        row = dict(getattr(self.webtoon, "_tracked_row", {}) or {})
        track_id = str(row.get("track_id") or "").strip()
        if track_id:
            current = self.tracked_titles_store.get(track_id)
            if current:
                row = dict(current)
                setattr(self.webtoon, "_tracked_row", row)
        return row

    def _hybrid_track_row(self) -> dict:
        if self.webtoon is None:
            return {}
        if self._is_remote_library_title():
            return self._tracked_library_row()
        return self.tracked_titles_store.find_matching_title(
            site_name=str(self.settings_store.get_source_site(self.webtoon.name) or "").strip(),
            series_id=str(self.settings_store.get_source_series_id(self.webtoon.name) or "").strip(),
            source_url=str(self.settings_store.get_source_url(self.webtoon.name) or "").strip(),
        ) or {}

    def _remote_source_url(self) -> str:
        if self.webtoon is None:
            return ""
        return str(
            self.settings_store.get_source_url(self.webtoon.name)
            or self._hybrid_track_row().get("source_url")
            or ""
        ).strip()

    def _remote_source_config(self) -> dict:
        if self.webtoon is None:
            return {}
        if self._is_remote_library_title():
            raw_config = self._tracked_library_row().get("source_config")
            if isinstance(raw_config, dict):
                return dict(raw_config)
            if isinstance(raw_config, str) and raw_config.strip():
                try:
                    import json
                    parsed = json.loads(raw_config)
                    if isinstance(parsed, dict):
                        return dict(parsed)
                except Exception:
                    pass
        return dict(self.settings_store.get_source_config(self.webtoon.name) or {})
    def _detail_progress(self) -> dict | None:
        if self.webtoon is None or self.progress_store is None:
            return None
        if not self._is_remote_library_title():
            return self.progress_store.get(self.webtoon.name)
        row = self._tracked_library_row()
        chapter_key = str(row.get("last_read_chapter_key") or "").strip()
        if not chapter_key:
            return None
        return self.progress_store.get_by_chapter_key(chapter_key)

    def _remote_series_entries(self) -> list[dict]:
        return list(self._filtered_new_remote_chapters()) if self._is_remote_library_title() else []

    def _remote_entry_for_progress(self, progress: dict | None) -> dict | None:
        if not progress:
            return None
        progress_key = str(progress.get("chapter_key") or "").strip()
        progress_chapter = str(progress.get("chapter") or "").strip()
        for entry in self._remote_series_entries():
            entry_key = str(entry.get("chapter_key") or "").strip()
            local_name = str(entry.get("local_name") or "").strip()
            display_name = str(entry.get("display_name") or "").strip()
            if progress_key and entry_key == progress_key:
                return entry
            if progress_chapter and progress_chapter in {local_name, display_name}:
                return entry
        return None

    def _progress_display_name(self, progress: dict | None) -> str:
        if not progress:
            return ""
        if self._is_remote_library_title():
            entry = self._remote_entry_for_progress(progress)
            if entry is not None:
                return str(entry.get("display_name") or entry.get("local_name") or "").strip()
            progress_key = str(progress.get("chapter_key") or "").strip()
            if progress_key:
                cached_ref = self.chapter_ref_store.get(progress_key) or {}
                cached_title = str(cached_ref.get("chapter_title") or cached_ref.get("local_chapter_name") or "").strip()
                if cached_title:
                    return cached_title
        return str(progress.get("chapter") or "").strip()

    def _refresh_last_read_label(self, progress: dict | None = None) -> None:
        if self.webtoon is None:
            self.last_read_label.clear()
            self.continue_btn.hide()
            return
        if progress is None:
            progress = self._detail_progress()

        single_manga_chapter = self._single_manga_chapter()
        if single_manga_chapter:
            total_pages = self._chapter_total_images(single_manga_chapter)
            self.last_read_label.setText(t("library.detail.pages", count=total_pages) if total_pages > 0 else "")
            self.continue_btn.setVisible(bool(progress))
            return

        if progress:
            ch = self._progress_display_name(progress)
            total_images = int(progress.get("total_images", 0) or 0)
            percent = self._calc_percent(float(progress.get("scroll") or 0.0), total_images)
            self.last_read_label.setText(t("library.detail.last_read_status", chapter=ch, percent=percent))
            self.continue_btn.show()
            return

        self.last_read_label.setText(t("library.detail.not_started"))
        self.continue_btn.hide()

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def load_webtoon(self, webtoon, progress_store):
        logger.info("Loading detail page for %s", webtoon.name)
        self._hide_manga_page_preview()
        self.webtoon        = webtoon
        self.webtoon.path = os.path.abspath(webtoon.path)
        local_refs = self.chapter_ref_store.list_for_owner("local", webtoon.name)
        self.webtoon.chapter_display_names = {
            str(ref.get("local_chapter_name") or "").strip(): str(ref.get("chapter_title") or "").strip()
            for ref in local_refs
            if str(ref.get("local_chapter_name") or "").strip() and str(ref.get("chapter_title") or "").strip()
        }
        self.progress_store = progress_store
        self.progress_map   = progress_store.get_progress_map(webtoon.name)
        self.bookmarked_chapters = self.settings_store.get_bookmarked_chapters(webtoon.name)
        self.selected_chapters = set()
        self.selected_remote_chapter_urls = set()
        self.latest_new_chapter = self.settings_store.get_latest_new_chapter(webtoon.name)
        self.webtoon_bookmarked = self.settings_store.get_bookmarked(webtoon.name)
        self._chapter_display_order = self._ordered_chapters_for_display(webtoon.chapters)
        self._chapter_dir_cache = None
        self._pending_remote_chapter_urls = set()
        self._update_progress_current = 0
        self._update_progress_total = 0
        self.show_only_bookmarked = False
        self.show_only_scene_marks = False
        self.bookmarks_filter_btn.setChecked(False)
        self.scene_marks_filter_btn.setChecked(False)
        self.scene_bookmark_counts = self.scene_bookmark_store.counts_for_webtoon(webtoon.name)
        self._sync_saved_marks_button()

        # Restore per-webtoon hide-filler setting
        self.hide_specials = self.settings_store.get_hide_filler(webtoon.name)
        self.hide_specials_btn.setChecked(self.hide_specials)
        icon_name = "fa5s.eye-slash" if self.hide_specials else "fa5s.eye"
        self.hide_specials_btn.setIcon(qta.icon(icon_name, color=TEXT_MUTED))
        self.hide_specials_btn.setIconSize(QSize(12, 12))

        self.title_label.setText(webtoon.name)
        self._sync_webtoon_bookmark_button()
        self._sync_detail_filter_visibility()
        self._update_chapter_count_label()
        self._sync_update_button()

        # Thumbnail
        pixmap = QPixmap(webtoon.thumbnail)
        if not pixmap.isNull():
            pixmap = pixmap.scaled(THUMB_W, THUMB_H,
                                   Qt.KeepAspectRatioByExpanding,
                                   Qt.SmoothTransformation)
            x = (pixmap.width()  - THUMB_W) // 2
            y = (pixmap.height() - THUMB_H) // 2
            pixmap = pixmap.copy(x, y, THUMB_W, THUMB_H)

            rounded = QPixmap(THUMB_W, THUMB_H)
            rounded.fill(Qt.transparent)
            p = QPainter(rounded)
            p.setRenderHint(QPainter.Antialiasing)
            path = QPainterPath()
            path.addRoundedRect(0, 0, THUMB_W, THUMB_H, RADIUS, RADIUS)
            p.setClipPath(path)
            p.drawPixmap(0, 0, pixmap)
            p.end()
            self.thumb_label.setPixmap(rounded)

        # Last read / page count label
        progress = self._detail_progress()
        self._refresh_last_read_label(progress)

        self._begin_remote_series_lookup()
        self._build_chapter_list(progress)
        if not self._is_remote_library_title():
            self._maybe_auto_open_single_manga_preview()
        self._sync_chapter_batch_actions()
        self._sync_update_button()

    def refresh_remote_state(self):
        if self.webtoon is None:
            return
        self._sync_update_button()
        self._begin_remote_series_lookup()

    def suspend_remote_state(self):
        self._remote_request_id += 1
        self._remote_status = ""
        self._sync_remote_chapter_state(rebuild_chapter_list=False)

    def _is_active_detail_page(self) -> bool:
        stack = getattr(self.main_window, "stack", None)
        if stack is None:
            return False
        return stack.currentWidget() is self and self.isVisible()

    def _calc_percent(self, scroll: float, total_images: int) -> int:
        if total_images <= 0:
            return 0
        # sentinel: viewer saves scroll == total_images when scrollbar is at max
        if scroll >= total_images:
            return 100
        return min(99, int((scroll / total_images) * 100))

    def _progress_for_chapter(self, chapter: str) -> tuple[float, int]:
        scroll, total = self.progress_map.get(chapter, (0.0, 0))
        if total <= 0 and scroll > 0:
            total = self._chapter_total_images(chapter)
            if total > 0:
                self.progress_map[chapter] = (scroll, total)
        return scroll, total

    def _build_chapter_list(self, progress):
        while self.chapter_list_layout.count():
            item = self.chapter_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._remote_selection_label = None
        self._remote_download_selected_btn = None
        self._remote_clear_selection_btn = None

        if self._is_remote_library_title():
            remote_chapters = self._filtered_new_remote_chapters()
            remote_urls = {entry.get("url", "") for entry in remote_chapters if entry.get("url")}
            self.selected_remote_chapter_urls &= remote_urls
            if remote_chapters:
                for entry in remote_chapters:
                    self.chapter_list_layout.addWidget(self._make_remote_chapter_row(entry))
            else:
                label = QLabel(self.remote_status_label.text() or t("library.detail.checking_new"))
                label.setStyleSheet(SUBTLE_META_LABEL_STYLE)
                self.chapter_list_layout.addWidget(label)
            return

        last_read_chapter = progress["chapter"] if progress else None
        chapters = self._filtered_chapters()

        for chapter in chapters:
            scroll, total = self._progress_for_chapter(chapter)
            is_last_read = (chapter == last_read_chapter)
            row = self._make_chapter_row(chapter, is_last_read, scroll, total)
            self.chapter_list_layout.addWidget(row)

        remote_chapters = self._filtered_new_remote_chapters()
        remote_urls = {entry.get("url", "") for entry in remote_chapters if entry.get("url")}
        self.selected_remote_chapter_urls &= remote_urls
        if remote_chapters:
            header = QLabel(t("library.detail.new_chapters_available"))
            header.setStyleSheet(SECTION_CAPTION_STYLE)
            self.chapter_list_layout.addWidget(header)
            self.chapter_list_layout.addWidget(self._make_remote_batch_row(remote_chapters))
            for entry in remote_chapters:
                self.chapter_list_layout.addWidget(self._make_remote_chapter_row(entry))

    def _make_chapter_row(self, chapter: str, is_last_read: bool, scroll: float, total: int) -> QWidget:
        row = QWidget()
        row.setCursor(Qt.PointingHandCursor)
        row.setStyleSheet(CHAPTER_ROW_STYLE)
        row.setFixedHeight(52)

        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(12)

        select_slot = QWidget()
        select_slot.setFixedWidth(22)
        select_slot.setStyleSheet(CHAPTER_SELECT_SLOT_STYLE)
        select_slot_layout = QHBoxLayout(select_slot)
        select_slot_layout.setContentsMargins(0, 0, 0, 0)
        select_slot_layout.setSpacing(0)

        select_btn = QToolButton(select_slot)
        select_btn.setCursor(Qt.PointingHandCursor)
        select_btn.setAutoRaise(True)
        select_btn.setCheckable(True)
        select_btn.setChecked(chapter in self.selected_chapters)
        select_btn.setIconSize(QSize(14, 14))
        select_btn.setStyleSheet(CHAPTER_TOOL_BUTTON_STYLE)
        select_btn.setProperty("chapter_name", chapter)
        apply_select_icon(select_btn, select_btn.isChecked())
        select_btn.clicked.connect(
            lambda checked, ch=chapter, btn=select_btn: self._toggle_chapter_selected(ch, btn, checked)
        )
        select_slot_layout.addWidget(select_btn, 0, Qt.AlignCenter)
        set_selector_visibility(row, select_btn, force=self._chapter_selection_visible())
        layout.addWidget(select_slot)

        color = ACCENT if is_last_read else TEXT_SOFT
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)

        display_name = (getattr(self.webtoon, "chapter_display_names", {}) or {}).get(chapter, chapter)
        name_lbl = QLabel(display_name)
        name_lbl.setStyleSheet(chapter_name_style(color))
        title_row.addWidget(name_lbl)

        if chapter == self.latest_new_chapter:
            new_chip = QLabel(t("library.detail.new_chip"))
            new_chip.setStyleSheet(NEW_CHIP_STYLE)
            new_chip.setAlignment(Qt.AlignCenter)
            new_chip.setFixedHeight(14)
            title_row.addWidget(new_chip)

        title_row.addStretch()
        layout.addLayout(title_row, 1)

        if self._can_edit_chapter_pages(chapter):
            edit_pages_btn = QToolButton(row)
            edit_pages_btn.setCursor(Qt.PointingHandCursor)
            edit_pages_btn.setAutoRaise(True)
            edit_pages_btn.setIcon(qta.icon("fa5s.th-large", color=TEXT_MUTED))
            edit_pages_btn.setIconSize(QSize(14, 14))
            edit_pages_btn.setToolTip(t("library.detail.edit_chapter"))
            edit_pages_btn.setStyleSheet(CHAPTER_TOOL_BUTTON_STYLE)
            edit_pages_btn.clicked.connect(lambda checked=False, ch=chapter: self._open_chapter_editor(ch))
            layout.addWidget(edit_pages_btn)

        scene_count = int(self.scene_bookmark_counts.get(chapter, 0) or 0)
        if scene_count > 0:
            scene_btn = QToolButton(row)
            scene_btn.setText(t("library.detail.scenes"))
            scene_btn.setCursor(Qt.PointingHandCursor)
            scene_btn.setStyleSheet(CHAPTER_TOOL_BUTTON_STYLE)
            scene_btn.clicked.connect(lambda checked=False, ch=chapter: self._open_scene_bookmarks_for_chapter(ch))
            layout.addWidget(scene_btn)

        bookmark_btn = QToolButton(row)
        bookmark_btn.setCursor(Qt.PointingHandCursor)
        bookmark_btn.setAutoRaise(True)
        bookmark_btn.setCheckable(True)
        bookmark_btn.setChecked(chapter in self.bookmarked_chapters)
        bookmark_btn.setIconSize(QSize(14, 14))
        bookmark_btn.setStyleSheet(CHAPTER_TOOL_BUTTON_STYLE)
        self._apply_bookmark_icon(bookmark_btn, bookmark_btn.isChecked())
        bookmark_btn.clicked.connect(
            lambda checked, ch=chapter, btn=bookmark_btn: self._toggle_chapter_bookmark(ch, btn)
        )

        # Last-read bookmark icon (new)

        # Progress circle
        percent = self._calc_percent(scroll, total)
        if percent > 0:
            circle = ProgressCircle()
            circle.set_percent(percent)
            layout.addWidget(circle)

        if is_last_read:
            last_read_icon = QLabel()
            last_read_icon.setPixmap(qta.icon("fa5s.bookmark", color=ACCENT).pixmap(QSize(14, 14)))
            last_read_icon.setStyleSheet(LAST_READ_ICON_STYLE)
            layout.addWidget(last_read_icon)

        layout.addWidget(bookmark_btn)

        row.enterEvent = lambda event, btn=select_btn, widget=row: self._on_chapter_row_hover(widget, btn, True, event)
        row.leaveEvent = lambda event, btn=select_btn, widget=row: self._on_chapter_row_hover(widget, btn, False, event)
        row.mousePressEvent = lambda e, ch=chapter: self._open_chapter(ch)
        return row

    def _make_remote_chapter_row(self, entry: dict) -> QWidget:
        row = QWidget()
        row.setCursor(Qt.PointingHandCursor)
        row.setStyleSheet(CHAPTER_ROW_STYLE)
        row.setFixedHeight(52)

        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(12)

        select_slot = QWidget()
        select_slot.setFixedWidth(22)
        select_slot.setStyleSheet(CHAPTER_SELECT_SLOT_STYLE)
        select_slot_layout = QHBoxLayout(select_slot)
        select_slot_layout.setContentsMargins(0, 0, 0, 0)
        select_slot_layout.setSpacing(0)

        chapter_url = entry.get("url", "")
        select_btn = QToolButton(select_slot)
        select_btn.setCursor(Qt.PointingHandCursor)
        select_btn.setAutoRaise(True)
        select_btn.setCheckable(True)
        select_btn.setChecked(chapter_url in self.selected_remote_chapter_urls)
        select_btn.setIconSize(QSize(14, 14))
        select_btn.setStyleSheet(CHAPTER_TOOL_BUTTON_STYLE)
        select_btn.setProperty("remote_chapter_url", chapter_url)
        apply_select_icon(select_btn, select_btn.isChecked())
        select_btn.clicked.connect(
            lambda checked, url=chapter_url, btn=select_btn: self._toggle_remote_chapter_selected(url, btn, checked)
        )
        select_slot_layout.addWidget(select_btn, 0, Qt.AlignCenter)
        set_selector_visibility(row, select_btn, force=self._remote_chapter_selection_visible())
        layout.addWidget(select_slot)

        progress = self._remote_entry_progress(entry)
        is_last_read = self._is_remote_entry_last_read(entry)
        color = ACCENT if is_last_read else TEXT_SOFT

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)

        display_name = entry.get("display_name") or entry.get("local_name") or t("library.detail.new_chapter")
        name_lbl = QLabel(display_name)
        name_lbl.setStyleSheet(chapter_name_style(color))
        title_row.addWidget(name_lbl)

        if not self._is_remote_library_title():
            new_chip = QLabel(t("library.detail.new_chip"))
            new_chip.setStyleSheet(NEW_CHIP_STYLE)
            new_chip.setAlignment(Qt.AlignCenter)
            new_chip.setFixedHeight(14)
            title_row.addWidget(new_chip)

        title_row.addStretch()
        layout.addLayout(title_row, 1)

        scene_count = self._remote_entry_scene_count(entry)
        if scene_count > 0:
            scene_btn = QToolButton(row)
            scene_btn.setText(t("library.detail.scenes"))
            scene_btn.setCursor(Qt.PointingHandCursor)
            scene_btn.setStyleSheet(CHAPTER_TOOL_BUTTON_STYLE)
            scene_btn.clicked.connect(lambda checked=False, payload=dict(entry): self._open_remote_scene_bookmarks(payload))
            layout.addWidget(scene_btn)

        bookmark_btn = QToolButton(row)
        bookmark_btn.setCursor(Qt.PointingHandCursor)
        bookmark_btn.setAutoRaise(True)
        bookmark_btn.setCheckable(True)
        bookmark_btn.setChecked(self._is_remote_entry_bookmarked(entry))
        bookmark_btn.setIconSize(QSize(14, 14))
        bookmark_btn.setStyleSheet(CHAPTER_TOOL_BUTTON_STYLE)
        self._apply_bookmark_icon(bookmark_btn, bookmark_btn.isChecked())
        bookmark_btn.clicked.connect(
            lambda checked, payload=dict(entry), btn=bookmark_btn: self._toggle_remote_chapter_bookmark(payload, btn)
        )

        percent = self._remote_entry_percent(progress)
        if percent > 0:
            circle = ProgressCircle()
            circle.set_percent(percent)
            layout.addWidget(circle)

        if is_last_read:
            last_read_icon = QLabel()
            last_read_icon.setPixmap(qta.icon("fa5s.bookmark", color=ACCENT).pixmap(QSize(14, 14)))
            last_read_icon.setStyleSheet(LAST_READ_ICON_STYLE)
            layout.addWidget(last_read_icon)

        download_btn = QToolButton(row)
        download_btn.setText(t("library.detail.download"))
        download_btn.setCursor(Qt.PointingHandCursor)
        download_btn.setStyleSheet(CHAPTER_TOOL_BUTTON_STYLE)
        download_btn.clicked.connect(
            lambda checked=False, url=chapter_url: self._download_remote_chapters([url])
        )
        layout.addWidget(download_btn)
        layout.addWidget(bookmark_btn)

        row.enterEvent = lambda event, btn=select_btn, widget=row: self._on_remote_chapter_row_hover(widget, btn, True, event)
        row.leaveEvent = lambda event, btn=select_btn, widget=row: self._on_remote_chapter_row_hover(widget, btn, False, event)
        row.mousePressEvent = lambda _event, payload=dict(entry): self._open_remote_chapter(payload)
        return row

    def _make_remote_batch_row(self, remote_chapters: list[dict]) -> QWidget:
        widget = QWidget()
        widget.setStyleSheet(BATCH_BAR_STYLE)
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        self._remote_selection_label = QLabel("")
        self._remote_selection_label.setStyleSheet(BATCH_LABEL_STYLE)
        layout.addWidget(self._remote_selection_label)

        button_style = sized_button_style(SECONDARY_ACTION_BUTTON_STYLE, BATCH_ACTION_BTN_H)
        primary_button_style = sized_button_style(PRIMARY_ACTION_BUTTON_STYLE, BATCH_ACTION_BTN_H)

        select_all_btn = QPushButton(t("library.detail.select_all_new"))
        select_all_btn.setStyleSheet(button_style)
        select_all_btn.clicked.connect(self._select_all_remote_chapters)
        layout.addWidget(select_all_btn)

        self._remote_download_selected_btn = QPushButton(t("library.detail.download_selected"))
        self._remote_download_selected_btn.setStyleSheet(primary_button_style)
        self._remote_download_selected_btn.clicked.connect(self._download_selected_remote_chapters)
        layout.addWidget(self._remote_download_selected_btn)

        self._remote_clear_selection_btn = QPushButton(t("library.detail.clear"))
        self._remote_clear_selection_btn.setStyleSheet(button_style)
        self._remote_clear_selection_btn.clicked.connect(self._clear_remote_chapter_selection)
        layout.addWidget(self._remote_clear_selection_btn)

        layout.addStretch()
        self._sync_remote_batch_actions()
        return widget

    # ------------------------------------------------------------------ #
    #  Actions                                                             #
    # ------------------------------------------------------------------ #

    def _visible_chapters_count(self, chapters: list) -> int:
        """Count chapters that are not special (.5-style) chapters."""
        return sum(1 for c in chapters if not SPECIAL_CHAPTER_RE.search(c))

    def _filtered_chapters(self) -> list[str]:
        if self.webtoon is None:
            return []

        chapters = list(self._chapter_display_order or self.webtoon.chapters)
        if self.hide_specials:
            chapters = [c for c in chapters if not SPECIAL_CHAPTER_RE.search(c)]
        if self.show_only_bookmarked:
            chapters = [c for c in chapters if c in self.bookmarked_chapters]
        if self.show_only_scene_marks:
            chapters = [c for c in chapters if int(self.scene_bookmark_counts.get(c, 0) or 0) > 0]
        return chapters

    def _single_manga_chapter(self) -> str | None:
        if not self._is_manga_webtoon() or self.webtoon is None:
            return None
        chapters = list(getattr(self.webtoon, "chapters", []) or [])
        return chapters[0] if len(chapters) == 1 else None

    def _maybe_auto_open_single_manga_preview(self) -> None:
        chapter = self._single_manga_chapter()
        if not chapter:
            return
        if self._manga_preview_active and self._manga_preview_chapter == chapter:
            return
        self._open_manga_chapter_preview(chapter, self.webtoon.chapters.index(chapter))

    def _filtered_new_remote_chapters(self) -> list[dict]:
        if (self.show_only_bookmarked or self.show_only_scene_marks) and not self._is_remote_library_title():
            return []

        chapters = list(self._new_remote_chapters)
        if self._pending_remote_chapter_urls:
            chapters = [
                entry for entry in chapters
                if entry.get("url", "") not in self._pending_remote_chapter_urls
            ]
        if self.hide_specials:
            chapters = [
                entry for entry in chapters
                if not SPECIAL_CHAPTER_RE.search(entry.get("display_name", "") or entry.get("local_name", ""))
            ]
        if self.show_only_bookmarked:
            chapters = [entry for entry in chapters if self._is_remote_entry_bookmarked(entry)]
        if self.show_only_scene_marks:
            chapters = [entry for entry in chapters if self._remote_entry_scene_count(entry) > 0]
        return chapters

    def _update_chapter_count_label(self):
        if self.webtoon is None:
            self.chapter_count_label.clear()
            return

        if self._is_remote_library_title():
            total_count = len(self._new_remote_chapters)
            visible_count = len(self._filtered_new_remote_chapters())
            hidden_specials = max(0, total_count - visible_count) if self.hide_specials else 0
            if self.show_only_bookmarked or self.show_only_scene_marks:
                parts = [t("library.detail.chapters_shown", count=visible_count)]
            elif self.hide_specials and hidden_specials > 0:
                parts = [t("library.detail.chapters_count", count=visible_count)]
            else:
                parts = [t("library.detail.chapters_count", count=total_count)]
            if self.hide_specials and hidden_specials > 0:
                parts.append(t("library.detail.special_hidden", count=hidden_specials))
            if self.show_only_bookmarked:
                parts.append(t("library.detail.bookmarked_count", count=self._remote_bookmarked_count()))
            self.chapter_count_label.setText(" | ".join(parts))
            return

        single_manga_chapter = self._single_manga_chapter()
        if single_manga_chapter:
            self.chapter_count_label.clear()
            return

        total_count = len(self.webtoon.chapters)
        visible_count = len(self._filtered_chapters())
        hidden_specials = total_count - self._visible_chapters_count(self.webtoon.chapters)

        if self.show_only_bookmarked or self.show_only_scene_marks:
            parts = [t("library.detail.chapters_shown", count=visible_count)]
        elif self.hide_specials and hidden_specials > 0:
            parts = [t("library.detail.chapters_count", count=visible_count)]
        else:
            parts = [t("library.detail.chapters_count", count=total_count)]
        if self.hide_specials and hidden_specials > 0:
            parts.append(t("library.detail.special_hidden", count=hidden_specials))
        if self.show_only_bookmarked:
            parts.append(t("library.detail.bookmarked_count", count=len(self.bookmarked_chapters)))
        remote_count = len(self._filtered_new_remote_chapters())
        if remote_count > 0:
            parts.append(t("library.detail.new_online", count=remote_count))

        self.chapter_count_label.setText(" | ".join(parts))

    def _remote_bookmarked_count(self) -> int:
        return sum(1 for entry in self._new_remote_chapters if self._is_remote_entry_bookmarked(entry))

    def _begin_remote_series_lookup(self):
        self._remote_request_id += 1
        self._remote_series = None
        self._new_remote_chapters = []
        source_url = self._remote_source_url() if self.webtoon else None
        if not source_url:
            self._remote_status = ""
            self._sync_remote_chapter_state(rebuild_chapter_list=False)
            return
        if not is_scraper_enabled_for_url(source_url):
            self._remote_status = t("library.detail.source_disabled")
            self._sync_remote_chapter_state(rebuild_chapter_list=False)
            return

        try:
            scraper = get_scraper(source_url)
        except Exception:
            scraper = None
        if scraper is None:
            self._remote_status = ""
            self._sync_remote_chapter_state(rebuild_chapter_list=False)
            return

        self._remote_status = t("library.detail.checking_new")
        self._sync_remote_chapter_state(rebuild_chapter_list=False)
        source_site = str(self._hybrid_track_row().get("site_name") or self.settings_store.get_source_site(self.webtoon.name) or getattr(scraper, "site_name", "") or "").strip()
        source_config = self._remote_source_config() or load_scraper_default_config(source_site)
        self._remote_series_loader.load(self._remote_request_id, source_url, source_config)

    def _on_remote_series_loaded(self, request_id: int, series, error: str):
        if request_id != self._remote_request_id or self.webtoon is None:
            return
        if not self._is_active_detail_page():
            logger.info("Ignoring stale remote lookup result because detail page is no longer active")
            return

        if error:
            source_url = self._remote_source_url()
            scraper = None
            if source_url:
                try:
                    scraper = get_scraper(source_url)
                except Exception:
                    scraper = None
            site_name = getattr(scraper, "site_name", "") if scraper is not None else ""
            if self._looks_like_access_block(error) and site_name:
                if self.main_window.open_site_authorization(site_name, url=source_url or ""):
                    logger.info(
                        "Detail-page remote lookup requested authorization for %s; retrying series load",
                        self.webtoon.name,
                    )
                    self._begin_remote_series_lookup()
                    return
                logger.info(
                    "Detail-page remote lookup requires authorization for %s but authorization flow was not opened",
                    self.webtoon.name,
                )
                self._remote_series = None
                self._remote_status = t("library.detail.check_failed_auth")
                self._sync_remote_chapter_state()
                return
            logger.warning("Remote chapter lookup failed for %s: %s", self.webtoon.name, error)
            self._remote_series = None
            self._remote_status = t("library.detail.check_failed")
            self._sync_remote_chapter_state()
            return

        self._remote_series = series
        self._remote_status = ""
        self._sync_remote_chapter_state()
        self._refresh_last_read_label()

    def _looks_like_access_block(self, error: str) -> bool:
        text = " ".join(str(error or "").casefold().split())
        markers = (
            "cloudflare",
            "anti-bot",
            "blocked the request",
            "blocked the chapter request",
            "blocked the catalog request",
        )
        return any(marker in text for marker in markers)

    def _format_remote_chapter_dir_name(self, chapter) -> str:
        number = getattr(chapter, "number", None)
        if number is not None:
            try:
                number_value = float(number)
                if number_value.is_integer():
                    return t("library.detail.chapter_number_int", number=int(number_value))
                return t("library.detail.chapter_number", number=format(number_value, "g"))
            except Exception:
                pass
        return sanitize_webtoon_name(getattr(chapter, "title", "") or "") or t("library.detail.chapter")

    def _sync_remote_chapter_state(self, *, rebuild_chapter_list: bool = True):
        previous_visible_count = len(self._filtered_new_remote_chapters())
        previous_status = self.remote_status_label.text()
        previous_status_visible = self.remote_status_label.isVisible()
        local_chapters = set(self.webtoon.chapters) if self.webtoon else set()
        new_remote = []
        seen = set()
        remote_mode = self._is_remote_library_title()
        tracked_row = self._hybrid_track_row()
        site_name = str(tracked_row.get("site_name") or getattr(self._remote_series, "site", "") or "").strip()
        series_id = str(tracked_row.get("series_id") or getattr(self._remote_series, "series_id", "") or getattr(self._remote_series, "url", "") or "").strip()
        track_id = str(tracked_row.get("track_id") or "").strip()
        cached_by_key = {}
        if track_id:
            for ref in self.chapter_ref_store.list_cached_for_owner("tracked", track_id):
                cache_path = str(ref.get("cache_path") or "").strip()
                if cache_path and os.path.exists(cache_path):
                    cached_by_key[str(ref.get("chapter_key") or "").strip()] = dict(ref)
        for chapter in getattr(self._remote_series, "chapters", []) or []:
            local_name = self._format_remote_chapter_dir_name(chapter)
            if not local_name:
                continue
            if not remote_mode and (local_name in local_chapters or local_name in seen):
                continue
            seen.add(local_name)
            display_name = getattr(chapter, "title", "") or local_name
            chapter_url = getattr(chapter, "url", "") or ""
            chapter_id = str(getattr(chapter, "id", "") or "").strip()
            chapter_key = build_remote_chapter_key(site_name, series_id, chapter_id, chapter_url)
            cached_ref = cached_by_key.get(chapter_key, {})
            new_remote.append(
                {
                    "url": chapter_url,
                    "local_name": local_name,
                    "display_name": display_name,
                    "chapter_key": chapter_key,
                    "chapter_obj": chapter,
                    "is_cached": bool(cached_ref),
                    "cache_path": str(cached_ref.get("cache_path") or "").strip(),
                }
            )
        new_remote.sort(key=lambda entry: chapter_sort_key(entry["local_name"]), reverse=self.sort_latest_first)
        remote_changed = new_remote != self._new_remote_chapters
        self._new_remote_chapters = new_remote
        self.remote_status_label.setText(self._remote_status)
        self.remote_status_label.setVisible(bool(self._remote_status))
        count = len(self._filtered_new_remote_chapters())
        if remote_mode:
            self.download_new_btn.hide()
        else:
            self.download_new_btn.setVisible(count > 0)
            self.download_new_btn.setText(t("library.detail.download_new_count", count=count) if count > 0 else t("library.detail.download_new"))
        self._update_chapter_count_label()
        status_changed = (
            previous_status != self.remote_status_label.text()
            or previous_status_visible != self.remote_status_label.isVisible()
        )
        if (not remote_mode) and remote_changed and count > 0:
            self._maybe_auto_download_remote_updates()
        if rebuild_chapter_list and (remote_changed or previous_visible_count != count or status_changed):
            progress = self._detail_progress()
            self._build_chapter_list(progress)
    def _sync_webtoon_bookmark_button(self):
        if self.webtoon_bookmarked:
            self.bookmark_btn.setText(t("library.detail.bookmarked_active"))
        else:
            self.bookmark_btn.setText(t("library.detail.bookmark"))
        color = ACCENT if self.webtoon_bookmarked else TEXT_MUTED
        self.bookmark_btn.setIcon(qta.icon("fa5s.star", color=color))

    def _toggle_webtoon_bookmark(self):
        if self.webtoon is None:
            return
        self.webtoon_bookmarked = self.settings_store.toggle_bookmarked(self.webtoon.name)
        self.webtoon.is_bookmarked = self.webtoon_bookmarked
        logger.info("Detail page toggled webtoon bookmark for %s to %s", self.webtoon.name, self.webtoon_bookmarked)
        self._sync_webtoon_bookmark_button()
        self.main_window.library.refresh_dynamic_state()

    def _apply_bookmark_icon(self, button: QToolButton, is_bookmarked: bool):
        color = ACCENT if is_bookmarked else TEXT_MUTED
        button.setIcon(qta.icon("fa5s.star", color=color))

    def _on_chapter_row_hover(self, row: QWidget, button: QToolButton, hovered: bool, event):
        set_selector_visibility(row, button, force=self._chapter_selection_visible() or hovered)
        QWidget.enterEvent(row, event) if hovered else QWidget.leaveEvent(row, event)

    def _refresh_chapter_selection_visibility(self):
        refresh_selector_visibility(
            self.chapter_list_widget,
            "chapter_name",
            force=self._chapter_selection_visible(),
        )

    def _toggle_chapter_selected(self, chapter: str, button: QToolButton, is_selected: bool):
        if is_selected:
            self.selected_chapters.add(chapter)
        else:
            self.selected_chapters.discard(chapter)
        apply_select_icon(button, is_selected)
        self._sync_chapter_batch_actions()
        self._refresh_chapter_selection_visibility()

    def _sync_chapter_batch_actions(self):
        if self._is_remote_library_title():
            self.chapter_batch_bar.hide()
            return
        count = len(self.selected_chapters)
        self.chapter_batch_bar.setVisible((count > 0) and not self._manga_preview_active)
        self._refresh_chapter_selection_visibility()
        if count <= 0:
            return
        self.chapter_batch_label.setText(t("library.detail.chapters_selected", count=count))

    def _select_all_chapters(self):
        self.selected_chapters = set(self._filtered_chapters())
        progress = self._detail_progress()
        self._build_chapter_list(progress)
        self._sync_chapter_batch_actions()

    def _toggle_chapter_bookmark(self, chapter: str, button: QToolButton):
        if self.webtoon is None:
            return

        is_bookmarked = self.settings_store.toggle_bookmarked_chapter(self.webtoon.name, chapter)
        logger.info(
            "Bookmark toggled for %s chapter=%s bookmarked=%s",
            self.webtoon.name,
            chapter,
            is_bookmarked,
        )
        if is_bookmarked:
            self.bookmarked_chapters.add(chapter)
        else:
            self.bookmarked_chapters.discard(chapter)

        button.blockSignals(True)
        button.setChecked(is_bookmarked)
        button.blockSignals(False)
        self._apply_bookmark_icon(button, is_bookmarked)
        self._update_chapter_count_label()

        if self.show_only_bookmarked or self.show_only_scene_marks:
            progress = self._detail_progress()
            self._build_chapter_list(progress)

    def _remote_entry_bookmark_key(self, entry: dict) -> str:
        return str(entry.get("chapter_key") or entry.get("url") or entry.get("local_name") or "").strip()

    def _is_remote_entry_bookmarked(self, entry: dict) -> bool:
        bookmark_key = self._remote_entry_bookmark_key(entry)
        return bool(bookmark_key) and bookmark_key in self.bookmarked_chapters

    def _toggle_remote_chapter_bookmark(self, entry: dict, button: QToolButton):
        if self.webtoon is None:
            return
        bookmark_key = self._remote_entry_bookmark_key(entry)
        if not bookmark_key:
            return
        is_bookmarked = self.settings_store.toggle_bookmarked_chapter(self.webtoon.name, bookmark_key)
        if is_bookmarked:
            self.bookmarked_chapters.add(bookmark_key)
        else:
            self.bookmarked_chapters.discard(bookmark_key)

        button.blockSignals(True)
        button.setChecked(is_bookmarked)
        button.blockSignals(False)
        self._apply_bookmark_icon(button, is_bookmarked)
        self._update_chapter_count_label()

        if self.show_only_bookmarked or self.show_only_scene_marks:
            progress = self._detail_progress()
            self._build_chapter_list(progress)

    def _remote_entry_progress(self, entry: dict) -> dict | None:
        if self.progress_store is None:
            return None
        chapter_key = str(entry.get("chapter_key") or "").strip()
        if not chapter_key:
            return None
        return self.progress_store.get_by_chapter_key(chapter_key)

    def _remote_entry_percent(self, progress: dict | None) -> int:
        if not progress:
            return 0
        return self._calc_percent(float(progress.get("scroll") or 0.0), int(progress.get("total_images") or 0))

    def _is_remote_entry_last_read(self, entry: dict) -> bool:
        progress = self._detail_progress()
        if not progress:
            return False
        return str(progress.get("chapter_key") or "").strip() == str(entry.get("chapter_key") or "").strip()

    def _remote_entry_cached_chapter_name(self, entry: dict) -> str:
        cached_path = str(entry.get("cache_path") or "").strip()
        if not cached_path:
            return ""
        return str(Path(cached_path).name or "").strip()

    def _remote_entry_scene_count(self, entry: dict) -> int:
        chapter_name = self._remote_entry_cached_chapter_name(entry)
        if not chapter_name:
            return 0
        return int(self.scene_bookmark_counts.get(chapter_name, 0) or 0)

    def _open_remote_scene_bookmarks(self, entry: dict) -> None:
        chapter_name = self._remote_entry_cached_chapter_name(entry)
        if not chapter_name:
            return
        self._open_scene_bookmarks_for_chapter(chapter_name)

    def _remote_entry_for_cached_chapter(self, chapter: str) -> dict | None:
        normalized = str(chapter or "").strip()
        if not normalized:
            return None
        for entry in self._new_remote_chapters:
            if self._remote_entry_cached_chapter_name(entry) == normalized:
                return dict(entry)
        return None

    def _open_cached_remote_entry(self, entry: dict, *, start_scroll: float = 0.0) -> bool:
        if self.webtoon is None:
            return False
        cached_path = str(entry.get("cache_path") or "").strip()
        chapter_key = str(entry.get("chapter_key") or "").strip()
        if not cached_path or not os.path.exists(cached_path) or not chapter_key:
            return False
        owner = SimpleNamespace(
            title=self.webtoon.name,
            thumbnail=str(getattr(self.webtoon, "thumbnail", "") or "").strip(),
            cover_url=str(getattr(self.webtoon, "thumbnail", "") or "").strip(),
            content_type=str(getattr(self.webtoon, "content_type", "webtoon") or "webtoon").strip() or "webtoon",
            track_id=str(self._hybrid_track_row().get("track_id") or "").strip(),
            source_url=self._remote_source_url(),
        )
        setattr(owner, "_viewer_return", self._return_from_remote_viewer)
        source = ViewerChapterSource(
            chapter_key=chapter_key,
            title=str(entry.get("display_name") or entry.get("local_name") or "Chapter").strip(),
            content_type=str(getattr(self.webtoon, "content_type", "webtoon") or "webtoon").strip() or "webtoon",
            source_kind="cached_remote",
            storage_path=cached_path,
            remote_url=str(entry.get("url") or "").strip(),
            local_chapter_name=str(Path(cached_path).name or "").strip() or str(entry.get("local_name") or chapter_key),
        )
        self.main_window.set_window_context_title(self.webtoon.name)
        self.main_window.stack.setCurrentWidget(self.main_window.viewer)
        self.main_window.sidebar_controller.set_target(self._detail_origin_target())
        self.main_window.viewer.load_hybrid_title(
            self.webtoon.name,
            owner,
            [source],
            start_chapter_key=chapter_key,
            start_scroll=float(start_scroll or 0.0),
        )
        return True

    def _clear_chapter_selection(self):
        self.selected_chapters.clear()
        progress = self._detail_progress()
        self._build_chapter_list(progress)
        self._sync_chapter_batch_actions()

    def _on_remote_chapter_row_hover(self, row: QWidget, button: QToolButton, hovered: bool, event):
        set_selector_visibility(row, button, force=self._remote_chapter_selection_visible() or hovered)
        QWidget.enterEvent(row, event) if hovered else QWidget.leaveEvent(row, event)

    def _remote_chapter_select_buttons(self) -> list[QToolButton]:
        return selector_buttons(self.chapter_list_widget, "remote_chapter_url")

    def _refresh_remote_chapter_selection_visibility(self):
        refresh_selector_visibility(
            self.chapter_list_widget,
            "remote_chapter_url",
            force=self._remote_chapter_selection_visible(),
        )

    def _toggle_remote_chapter_selected(self, url: str, button: QToolButton, is_selected: bool):
        if not url:
            return
        if is_selected:
            self.selected_remote_chapter_urls.add(url)
        else:
            self.selected_remote_chapter_urls.discard(url)
        button.blockSignals(True)
        button.setChecked(is_selected)
        button.blockSignals(False)
        apply_select_icon(button, is_selected)
        self._refresh_remote_chapter_selection_visibility()
        self._sync_remote_batch_actions()

    def _sync_remote_batch_actions(self):
        count = len(self.selected_remote_chapter_urls)
        if self._remote_selection_label is not None:
            self._remote_selection_label.setText(t("library.detail.new_chapters_selected", count=count))
        if self._remote_download_selected_btn is not None:
            self._remote_download_selected_btn.setEnabled(count > 0)
        if self._remote_clear_selection_btn is not None:
            self._remote_clear_selection_btn.setEnabled(count > 0)

    def _select_all_remote_chapters(self):
        urls = [
            entry.get("url", "")
            for entry in self._filtered_new_remote_chapters()
            if entry.get("url")
        ]
        self.selected_remote_chapter_urls = set(urls)
        sync_selector_checked_state(
            self.chapter_list_widget,
            "remote_chapter_url",
            self.selected_remote_chapter_urls,
            force=True,
        )
        self._sync_remote_batch_actions()

    def _clear_remote_chapter_selection(self):
        self.selected_remote_chapter_urls.clear()
        sync_selector_checked_state(
            self.chapter_list_widget,
            "remote_chapter_url",
            self.selected_remote_chapter_urls,
            force=False,
        )
        self._sync_remote_batch_actions()

    def _download_selected_remote_chapters(self):
        ordered_urls = [
            entry.get("url", "")
            for entry in self._filtered_new_remote_chapters()
            if entry.get("url", "") in self.selected_remote_chapter_urls
        ]
        self._download_remote_chapters(ordered_urls)

    def _open_remote_chapter(self, entry: dict):
        if self.webtoon is None:
            return
        cached_path = str(entry.get("cache_path") or "").strip()
        chapter_key = str(entry.get("chapter_key") or "").strip()
        if cached_path and os.path.exists(cached_path) and chapter_key:
            owner = SimpleNamespace(
                title=self.webtoon.name,
                thumbnail=str(getattr(self.webtoon, "thumbnail", "") or "").strip(),
                cover_url=str(getattr(self.webtoon, "thumbnail", "") or "").strip(),
                content_type=str(getattr(self.webtoon, "content_type", "webtoon") or "webtoon").strip() or "webtoon",
                track_id=str(self._hybrid_track_row().get("track_id") or "").strip(),
                source_url=self._remote_source_url(),
            )
            setattr(owner, "_viewer_return", self._return_from_remote_viewer)
            source = ViewerChapterSource(
                chapter_key=chapter_key,
                title=str(entry.get("display_name") or entry.get("local_name") or "Chapter").strip(),
                content_type=str(getattr(self.webtoon, "content_type", "webtoon") or "webtoon").strip() or "webtoon",
                source_kind="cached_remote",
                storage_path=cached_path,
                remote_url=str(entry.get("url") or "").strip(),
                local_chapter_name=str(Path(cached_path).name or "").strip() or str(entry.get("local_name") or chapter_key),
            )
            progress = self.progress_store.get_by_chapter_key(chapter_key) if self.progress_store is not None else None
            start_scroll = float((progress or {}).get("scroll") or 0.0)
            self.main_window.set_window_context_title(self.webtoon.name)
            self.main_window.stack.setCurrentWidget(self.main_window.viewer)
            self.main_window.sidebar_controller.set_target(self._detail_origin_target())
            self.main_window.viewer.load_hybrid_title(
                self.webtoon.name,
                owner,
                [source],
                start_chapter_key=chapter_key,
                start_scroll=start_scroll,
            )
            return
        if self._remote_series is None:
            return
        chapter = entry.get("chapter_obj")
        if chapter is None:
            return
        source_url = self._remote_source_url()
        if not source_url:
            QMessageBox.warning(self, t("library.detail.download_new_title"), t("library.detail.no_source_url"))
            return
        chapter_label = str(entry.get("display_name") or entry.get("local_name") or getattr(chapter, "title", "") or "Chapter").strip()
        self._remote_read_request_id += 1
        self.remote_status_label.setText(t("library.detail.checking_new"))
        self.remote_status_label.show()
        self.main_window.set_window_context_title(self.webtoon.name)
        self.main_window.stack.setCurrentWidget(self.main_window.viewer)
        self.main_window.sidebar_controller.set_target(self._detail_origin_target())
        self.main_window.chapter_overlay.show(self.webtoon.name, chapter_label)
        self._remote_read_loader.load(self._remote_read_request_id, source_url, self._remote_series, chapter)

    def _on_remote_chapter_loaded(self, request_id: int, payload, start_scroll: float, error: str):
        if request_id != self._remote_read_request_id or self.webtoon is None:
            return
        if error:
            self.main_window.chapter_overlay.hide()
            self.main_window.set_window_context_title(self.webtoon.name)
            self.main_window.stack.setCurrentWidget(self)
            self.main_window.sidebar_controller.set_target(self._detail_origin_target())
            QMessageBox.warning(self, t("library.detail.download_new_title"), error)
            return
        if not payload:
            self.main_window.chapter_overlay.hide()
            self.main_window.set_window_context_title(self.webtoon.name)
            self.main_window.stack.setCurrentWidget(self)
            self.main_window.sidebar_controller.set_target(self._detail_origin_target())
            return
        owner, chapter_sources, start_chapter_key = payload
        setattr(owner, "_viewer_return", self._return_from_remote_viewer)
        self.main_window.set_window_context_title(self.webtoon.name)
        self.main_window.stack.setCurrentWidget(self.main_window.viewer)
        self.main_window.sidebar_controller.set_target(self._detail_origin_target())
        self.main_window.viewer.load_hybrid_title(
            self.webtoon.name,
            owner,
            chapter_sources,
            start_chapter_key=start_chapter_key,
            start_scroll=float(start_scroll or 0.0),
        )
    def _return_from_remote_viewer(self):
        if self.webtoon is None:
            return
        self.main_window.set_window_context_title(self.webtoon.name)
        self.main_window.stack.setCurrentWidget(self)
        self.main_window.sidebar_controller.set_target(self._detail_origin_target())
        updated_row = self._tracked_library_row()
        chapter_key = str(updated_row.get("last_read_chapter_key") or "").strip()
        if chapter_key and self.progress_store is not None:
            progress = self.progress_store.get_by_chapter_key(chapter_key)
            if progress:
                self._refresh_last_read_label(progress)
        self._begin_remote_series_lookup()

    def _auto_download_remote_urls(self) -> list[str]:
        chapters = list(self._filtered_new_remote_chapters())
        if self.webtoon is None:
            return []
        if self.hide_specials:
            filtered = [entry for entry in chapters if str(entry.get("local_name") or "").lower().startswith("chapter ")]
            if filtered:
                chapters = filtered
        limit = self.settings_store.get_auto_download_limit(self.webtoon.name)
        if limit > 0:
            chapters = chapters[-limit:]
        return [entry.get("url", "") for entry in chapters if entry.get("url")]

    def _maybe_auto_download_remote_updates(self):
        if self.webtoon is None:
            return
        if self.settings_store.get_update_mode(self.webtoon.name) != "auto_download":
            return
        if self._active_progress_service() is not None:
            return
        urls = [url for url in self._auto_download_remote_urls() if url not in self._pending_remote_chapter_urls]
        if not urls:
            return
        error = self.main_window.updates.start_update_for_webtoon(self.webtoon.name, chapter_urls=urls)
        if error:
            logger.warning("Detail-page auto-download could not start for %s: %s", self.webtoon.name, error)
            return
        self._pending_remote_chapter_urls.update(urls)
        self.remote_status_label.setText(t("library.detail.auto_download_started", count=len(urls)))
        self.remote_status_label.show()
    def _download_all_new_chapters(self):
        urls = [entry.get("url", "") for entry in self._filtered_new_remote_chapters() if entry.get("url")]
        self._download_remote_chapters(urls)

    def _download_remote_chapters(self, urls: list[str]):
        if self.webtoon is None or not urls:
            return
        urls = [
            url for url in dict.fromkeys(urls)
            if url and url not in self._pending_remote_chapter_urls
        ]
        if not urls:
            return
        source_url = self._remote_source_url()
        if not source_url:
            QMessageBox.warning(self, t("library.detail.download_new_title"), t("library.detail.no_source_url"))
            return
        error = self.main_window.downloader.start_download_from_url(
            source_url,
            preferred_name=self.webtoon.name,
            chapter_urls=urls,
        )
        if error:
            QMessageBox.warning(self, t("library.detail.download_new_title"), error)
            return
        self._pending_remote_chapter_urls.update(urls)
        self.selected_remote_chapter_urls.difference_update(urls)
        noun = t("library.detail.chapter_singular") if len(urls) == 1 else t("library.detail.chapter_plural")
        self.remote_status_label.setText(t("library.detail.download_started", count=len(urls), noun=noun))
        self.remote_status_label.show()
        progress = self._detail_progress()
        self._update_chapter_count_label()
        self._build_chapter_list(progress)

    def _chapter_total_images(self, chapter: str) -> int:
        scroll, total = self.progress_map.get(chapter, (0.0, 0))
        if total > 0:
            return total
        if self.webtoon is None:
            return 0
        if chapter_has_text_payload(self.webtoon.path, chapter):
            return 1
        return count_chapter_images(self.webtoon.path, chapter)

    def _mark_selected_chapters_read(self):
        if self.webtoon is None or self.progress_store is None or not self.selected_chapters:
            return
        entries = []
        for chapter in sorted(self.selected_chapters, key=chapter_sort_key):
            total = self._chapter_total_images(chapter)
            entries.append((chapter, float(total), total))
        self.progress_store.save_many(self.webtoon.name, entries)
        for chapter, scroll, total in entries:
            self.progress_map[chapter] = (float(total), total)
        self.latest_new_chapter = self.settings_store.get_latest_new_chapter(self.webtoon.name)
        self.selected_chapters.clear()
        progress = self._detail_progress()
        self._build_chapter_list(progress)
        self._sync_chapter_batch_actions()

    def _mark_selected_chapters_unread(self):
        if self.webtoon is None or self.progress_store is None or not self.selected_chapters:
            return
        chapters = sorted(self.selected_chapters, key=chapter_sort_key)
        self.progress_store.clear_chapters(self.webtoon.name, chapters)
        for chapter in chapters:
            self.progress_map.pop(chapter, None)
        self.selected_chapters.clear()
        progress = self._detail_progress()
        self._build_chapter_list(progress)
        self._sync_chapter_batch_actions()

    def _delete_selected_chapters(self):
        if self.webtoon is None or self.progress_store is None or not self.selected_chapters:
            return
        selected = sorted(self.selected_chapters, key=chapter_sort_key)
        answer = QMessageBox.question(
            self,
            t("library.detail.delete_selected_title"),
            t("library.detail.delete_selected_text", count=len(selected)),
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return

        import shutil

        failed = []
        bookmarks_changed = False

        for chapter in selected:
            chapter_path = chapter_storage_path(self.webtoon.path, chapter)
            try:
                if chapter_path.is_dir():
                    shutil.rmtree(chapter_path)
                    if chapter_path.exists():
                        raise OSError(f"Chapter folder still exists after deletion: {chapter_path}")
                elif chapter_path.is_file():
                    chapter_path.unlink()
                else:
                    raise OSError(f"Chapter path is missing: {chapter_path}")
            except OSError:
                logger.exception("Failed to delete chapter folder %s", chapter_path)
                failed.append(chapter)
                continue

            self.progress_store.clear_chapter(self.webtoon.name, chapter)
            self.scene_bookmark_store.clear_chapter(self.webtoon.name, chapter)
            self.progress_map.pop(chapter, None)
            self.scene_bookmark_counts.pop(chapter, None)
            if chapter in self.bookmarked_chapters:
                self.bookmarked_chapters.discard(chapter)
                bookmarks_changed = True
            if self.latest_new_chapter == chapter:
                self.settings_store.clear_latest_new_chapter(self.webtoon.name)
                self.latest_new_chapter = None

        if bookmarks_changed:
            self.settings_store.set_bookmarked_chapters(self.webtoon.name, self.bookmarked_chapters)

        self._refresh_webtoon_from_disk()
        self._clear_chapter_selection()
        if failed:
            noun = t("library.detail.chapter_singular") if len(failed) == 1 else t("library.detail.chapter_plural")
            QMessageBox.warning(
                self,
                t("library.detail.delete_selected_title"),
                t("library.detail.delete_failed", count=len(failed), noun=noun),
            )

    def _toggle_hide_specials(self):
        self.hide_specials = self.hide_specials_btn.isChecked()
        logger.info("Hide filler toggled for %s: %s", self.webtoon.name if self.webtoon else "<none>", self.hide_specials)
        icon_name = "fa5s.eye-slash" if self.hide_specials else "fa5s.eye"
        self.hide_specials_btn.setIcon(qta.icon(icon_name, color=TEXT_MUTED))
        self.hide_specials_btn.setIconSize(QSize(12, 12))

        if self.webtoon:
            self.settings_store.set_hide_filler(self.webtoon.name, self.hide_specials)
            self._update_chapter_count_label()

        progress = self._detail_progress()
        self._build_chapter_list(progress)

    def _toggle_bookmarks_filter(self):
        self.show_only_bookmarked = self.bookmarks_filter_btn.isChecked()
        logger.info(
            "Bookmarked-only filter toggled for %s: %s",
            self.webtoon.name if self.webtoon else "<none>",
            self.show_only_bookmarked,
        )
        self._update_chapter_count_label()
        progress = self._detail_progress()
        self._build_chapter_list(progress)

    def _toggle_scene_marks_filter(self):
        self.show_only_scene_marks = self.scene_marks_filter_btn.isChecked()
        logger.info(
            "Scene-mark filter toggled for %s: %s",
            self.webtoon.name if self.webtoon else "<none>",
            self.show_only_scene_marks,
        )
        if self._manga_preview_active and self.webtoon is not None and 0 <= self._manga_preview_index < len(self.webtoon.chapters):
            self._open_manga_chapter_preview(self._manga_preview_chapter, self._manga_preview_index)
            return
        self._update_chapter_count_label()
        progress = self._detail_progress()
        self._build_chapter_list(progress)

    def _saved_mode_label(self) -> str:
        content_type = str(getattr(self.webtoon, "content_type", "webtoon") or "webtoon").strip().casefold()
        return t("library.detail.bookmark_mode") if content_type == "webnovel" else t("library.detail.scene_mode")

    def _is_manga_webtoon(self) -> bool:
        return str(getattr(self.webtoon, "content_type", "webtoon") or "webtoon").strip().casefold() == "manga"

    def _is_image_webtoon(self) -> bool:
        content_type = str(getattr(self.webtoon, "content_type", "webtoon") or "webtoon").strip().casefold()
        return content_type in {"webtoon", "manga"}

    def _can_edit_chapter_pages(self, chapter: str) -> bool:
        if not self._is_image_webtoon() or self.webtoon is None:
            return False
        return chapter_is_editable(self.webtoon.path, chapter)

    def _sync_detail_filter_visibility(self) -> None:
        manga = self._is_manga_webtoon()
        remote_mode = self._is_remote_library_title()
        self.section_caption_label.setText("" if manga else t("library.detail.chapters"))
        self.sort_btn.setVisible(not manga)
        self.hide_specials_btn.setVisible(not manga)
        self.bookmarks_filter_btn.setVisible(not manga)
        self.scene_marks_filter_btn.setVisible(not manga)

    def _scene_counts_for_preview_chapter(self, chapter: str) -> dict[int, int]:
        if self.webtoon is None or chapter not in self.webtoon.chapters:
            return {}
        counts: dict[int, int] = {}
        for bookmark in self.scene_bookmark_store.list_for_chapter(self.webtoon.name, chapter):
            image_index = max(1, int(bookmark.get("image_index") or 0))
            page_index = image_index - 1
            counts[page_index] = counts.get(page_index, 0) + 1
        return counts

    def _set_manga_preview_visible(self, visible: bool) -> None:
        self._manga_preview_active = bool(visible)
        self.chapter_scroll.setVisible(not visible)
        self.chapter_batch_bar.setVisible((not visible) and self._chapter_selection_visible())
        self.manga_preview_panel.setVisible(visible)

    def _clear_manga_preview_grid(self) -> None:
        self._manga_preview_build_timer.stop()
        self._manga_preview_pending_tiles = []
        self._manga_preview_loader_timer.stop()
        self._manga_preview_queue.clear()
        self._manga_preview_queued.clear()
        self._manga_preview_loading.clear()
        self._manga_preview_tiles = []
        while self.manga_preview_grid.count():
            item = self.manga_preview_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _manga_preview_column_count(self) -> int:
        viewport = self.manga_preview_scroll.viewport()
        available_width = max(0, viewport.width())
        tile_width = MangaPageTile.PREVIEW_WIDTH
        spacing = self.manga_preview_grid.horizontalSpacing()
        if available_width <= 0 or tile_width <= 0:
            return 1
        return max(1, int((available_width + spacing) // (tile_width + spacing)))

    def _refresh_manga_preview_layout(self) -> None:
        columns = self._manga_preview_column_count()
        if columns == self._manga_preview_columns:
            return
        self._manga_preview_columns = columns
        self._relayout_manga_preview_tiles()

    def _relayout_manga_preview_tiles(self) -> None:
        if not self._manga_preview_tiles:
            return
        columns = max(1, self._manga_preview_columns)
        for tile in self._manga_preview_tiles:
            self.manga_preview_grid.removeWidget(tile)
        for index, tile in enumerate(self._manga_preview_tiles):
            self.manga_preview_grid.addWidget(
                tile,
                index // columns,
                index % columns,
            )

    def _hide_manga_page_preview(self) -> None:
        self._manga_preview_chapter = ""
        self._manga_preview_index = -1
        self._clear_manga_preview_grid()
        self._set_manga_preview_visible(False)

    def _refresh_active_manga_preview_scene_markers(self) -> None:
        if not self._manga_preview_active or not self._manga_preview_chapter:
            return
        if self.show_only_scene_marks and self.webtoon is not None and 0 <= self._manga_preview_index < len(self.webtoon.chapters):
            self._open_manga_chapter_preview(self._manga_preview_chapter, self._manga_preview_index)
            return
        counts = self._scene_counts_for_preview_chapter(self._manga_preview_chapter)
        for index, tile in enumerate(self._manga_preview_tiles):
            tile.set_scene_count(counts.get(index, 0))

    def _queue_manga_preview_indexes(self, indexes: list[int], *, prioritize: bool = False) -> None:
        if not indexes:
            return
        pending = []
        for index in indexes:
            if index < 0 or index >= len(self._manga_preview_tiles):
                continue
            tile = self._manga_preview_tiles[index]
            if tile.preview_loaded or index in self._manga_preview_queued or index in self._manga_preview_loading:
                continue
            self._manga_preview_queued.add(index)
            pending.append(index)
        if not pending:
            return
        if prioritize:
            self._manga_preview_queue = pending + self._manga_preview_queue
        else:
            self._manga_preview_queue.extend(pending)
        if not self._manga_preview_loader_timer.isActive():
            self._manga_preview_loader_timer.start(0)

    def _drain_manga_preview_tile_build(self) -> None:
        if not self._manga_preview_active or not self._manga_preview_pending_tiles:
            return
        loaded = 0
        batch = []
        while self._manga_preview_pending_tiles and loaded < MANGA_PREVIEW_TILE_BATCH_SIZE:
            batch.append(self._manga_preview_pending_tiles.pop(0))
            loaded += 1

        if batch:
            self._append_manga_preview_tiles(batch, self._manga_preview_index)

        self._queue_manga_preview_indexes(self._visible_manga_preview_indexes(), prioritize=True)
        if self._manga_preview_pending_tiles:
            self._manga_preview_build_timer.start(0)

    def _append_manga_preview_tiles(
        self,
        entries: list[tuple[int, str, int, int]],
        chapter_index: int,
    ) -> None:
        for visible_pos, image_path, page_index, scene_count in entries:
            tile = MangaPageTile(image_path, page_index, self.manga_preview_content)
            tile.set_scene_count(scene_count)
            tile.clicked.connect(
                lambda page_index, chapter_idx=chapter_index: self._open_manga_preview_page(chapter_idx, page_index)
            )
            self.manga_preview_grid.addWidget(
                tile,
                visible_pos // self._manga_preview_columns,
                visible_pos % self._manga_preview_columns,
            )
            self._manga_preview_tiles.append(tile)

    def _visible_manga_preview_indexes(self) -> list[int]:
        if not self._manga_preview_tiles:
            return []
        bar = self.manga_preview_scroll.verticalScrollBar()
        viewport = self.manga_preview_scroll.viewport()
        row_height = max(1, self._manga_preview_tiles[0].sizeHint().height() + self.manga_preview_grid.verticalSpacing())
        first_row = max(0, bar.value() // row_height)
        visible_rows = max(1, (viewport.height() // row_height) + 2)
        start_row = max(0, first_row - 1)
        end_row = first_row + visible_rows + 1
        indexes: list[int] = []
        for row in range(start_row, end_row + 1):
            row_start = row * self._manga_preview_columns
            row_end = min(len(self._manga_preview_tiles), row_start + self._manga_preview_columns)
            indexes.extend(range(row_start, row_end))
        return indexes

    def _schedule_visible_manga_preview_tiles(self, *_args) -> None:
        if not self._manga_preview_active:
            return
        self._queue_manga_preview_indexes(self._visible_manga_preview_indexes(), prioritize=True)

    def _drain_manga_preview_queue(self) -> None:
        if not self._manga_preview_active or not self._manga_preview_queue:
            return
        while self._manga_preview_queue and len(self._manga_preview_loading) < MANGA_PREVIEW_INFLIGHT_LIMIT:
            index = self._manga_preview_queue.pop(0)
            self._manga_preview_queued.discard(index)
            if index < 0 or index >= len(self._manga_preview_tiles):
                continue
            tile = self._manga_preview_tiles[index]
            if tile.preview_loaded:
                continue
            cached = self._manga_preview_pixmap_for_tile(tile)
            if cached is not None:
                tile.set_preview_pixmap(cached)
                continue
            self._manga_preview_loading.add(index)
            self._manga_preview_image_loader.load(
                self._manga_preview_generation,
                index,
                tile.image_path,
                tile.PREVIEW_WIDTH,
                tile.PREVIEW_HEIGHT,
            )
        if self._manga_preview_queue and len(self._manga_preview_loading) < MANGA_PREVIEW_INFLIGHT_LIMIT:
            self._manga_preview_loader_timer.start(0)

    def _manga_preview_cache_key(self, path: str, width: int, height: int) -> tuple[str, int, int, int, int, int]:
        try:
            stat = os.stat(path)
            mtime_ns = int(stat.st_mtime_ns)
            file_size = int(stat.st_size)
        except OSError:
            mtime_ns = 0
            file_size = 0
        return (str(path or ""), int(width), int(height), 12, mtime_ns, file_size)

    def _manga_preview_pixmap_for_tile(self, tile: MangaPageTile) -> QPixmap | None:
        cache_key = self._manga_preview_cache_key(
            tile.image_path,
            tile.PREVIEW_WIDTH,
            tile.PREVIEW_HEIGHT,
        )
        cached = self._manga_preview_pixmap_cache.get(cache_key)
        if cached is not None:
            self._manga_preview_pixmap_cache.move_to_end(cache_key)
            return cached
        return None

    def _store_manga_preview_pixmap(self, tile: MangaPageTile, pixmap: QPixmap) -> None:
        cache_key = self._manga_preview_cache_key(
            tile.image_path,
            tile.PREVIEW_WIDTH,
            tile.PREVIEW_HEIGHT,
        )
        self._manga_preview_pixmap_cache[cache_key] = pixmap
        self._manga_preview_pixmap_cache.move_to_end(cache_key)
        while len(self._manga_preview_pixmap_cache) > MANGA_PREVIEW_PIXMAP_CACHE_LIMIT:
            self._manga_preview_pixmap_cache.popitem(last=False)

    def _on_manga_preview_image_loaded(self, generation: int, index: int, image: QImage) -> None:
        if generation != self._manga_preview_generation:
            return
        self._manga_preview_loading.discard(index)
        if index < 0 or index >= len(self._manga_preview_tiles):
            return
        tile = self._manga_preview_tiles[index]
        if tile.preview_loaded:
            return
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            return
        self._store_manga_preview_pixmap(tile, pixmap)
        tile.set_preview_pixmap(pixmap)
        if self._manga_preview_queue:
            self._manga_preview_loader_timer.start(0)

    def _sync_saved_marks_button(self):
        count = sum(int(value or 0) for value in self.scene_bookmark_counts.values())
        mode_label = self._saved_mode_label()
        self.saved_marks_btn.setText(t("library.detail.saved_count", count=count) if count else t("library.detail.saved"))
        self.saved_marks_btn.setToolTip(t("library.detail.saved_tooltip", mode_label=mode_label.lower()))
        self.saved_marks_btn.setEnabled(count > 0)

    def _open_all_scene_bookmarks(self):
        if self.webtoon is None:
            return
        dialog = AllSceneBookmarksDialog(
            self.webtoon,
            self.scene_bookmark_store,
            self._open_scene_bookmark,
            parent=self,
            mode_label=self._saved_mode_label(),
        )
        dialog.exec()
        self.scene_bookmark_counts = self.scene_bookmark_store.counts_for_webtoon(self.webtoon.name)
        self._sync_saved_marks_button()
        self._refresh_active_manga_preview_scene_markers()
        self._update_chapter_count_label()
        progress = self._detail_progress()
        self._build_chapter_list(progress)

    def _open_scene_bookmarks_for_chapter(self, chapter: str):
        if self.webtoon is None:
            return
        dialog = SceneBookmarksDialog(
            self.webtoon,
            chapter,
            self.scene_bookmark_store,
            lambda packed, ch=chapter: self._open_scene_bookmark(ch, packed),
            parent=self,
        )
        dialog.exec()
        self.scene_bookmark_counts = self.scene_bookmark_store.counts_for_webtoon(self.webtoon.name)
        self._sync_saved_marks_button()
        self._refresh_active_manga_preview_scene_markers()
        self._update_chapter_count_label()
        progress = self._detail_progress()
        self._build_chapter_list(progress)

    def _open_scene_bookmark(self, chapter: str, packed: float):
        if self.webtoon is None:
            return
        if chapter not in self.webtoon.chapters:
            remote_entry = self._remote_entry_for_cached_chapter(chapter)
            if remote_entry is not None and self._open_cached_remote_entry(remote_entry, start_scroll=float(packed)):
                return
            return
        idx = self.webtoon.chapters.index(chapter)
        self.main_window.open_chapter(self.webtoon, idx, float(packed))

    def _ordered_chapters_for_display(self, chapters: list[str]) -> list[str]:
        base = list(chapters)
        if self.sort_latest_first:
            base.reverse()
        return base

    def _is_current_webtoon_updating(self) -> bool:
        return bool(
            self.webtoon
            and (
                (self._update_service is not None and self._update_service.has_active_download(self.webtoon.name))
                or (self._manual_download_service is not None and self._manual_download_service.has_active_download(self.webtoon.name))
            )
        )

    def _append_new_chapters_to_display_order(
        self,
        previous_display: list[str],
        chapter_dirs: list[str],
    ) -> list[str]:
        existing = set(chapter_dirs)
        kept = [chapter for chapter in previous_display if chapter in existing]
        new_chapters = [chapter for chapter in chapter_dirs if chapter not in kept]
        return kept + new_chapters

    def _get_disk_chapter_dirs(self) -> list[str]:
        if self.webtoon is None:
            return []

        storage_path = str(getattr(self.webtoon, "storage_path", self.webtoon.path) or self.webtoon.path)
        cache_path = storage_path
        try:
            mtime_ns = os.stat(storage_path).st_mtime_ns
        except OSError:
            return []

        cached = self._chapter_dir_cache
        if cached is not None and cached[0] == cache_path and cached[1] == mtime_ns:
            return list(cached[2])

        if os.path.isfile(storage_path):
            chapter_dirs = [os.path.basename(storage_path)]
        else:
            chapter_dirs = self.settings_store.order_chapters(
                self.webtoon.name,
                sorted(list_series_chapters(self.webtoon.path), key=chapter_sort_key),
            )
        self._chapter_dir_cache = (cache_path, mtime_ns, chapter_dirs)
        return list(chapter_dirs)

    def _refresh_webtoon_from_disk(self, preserve_display_order: bool = False) -> bool:
        if self.webtoon is None:
            return False

        chapter_dirs = self._get_disk_chapter_dirs()
        if not chapter_dirs and not os.path.isdir(self.webtoon.path):
            return False

        if chapter_dirs == list(self.webtoon.chapters):
            return True

        logger.info("Detail page refreshed chapter list from disk for %s", self.webtoon.name)
        previous_display = list(self._chapter_display_order or self._ordered_chapters_for_display(self.webtoon.chapters))
        self.webtoon.chapters = chapter_dirs
        if self._manga_preview_active and self._manga_preview_chapter not in chapter_dirs:
            self._hide_manga_page_preview()
        if preserve_display_order or self._is_current_webtoon_updating():
            self._chapter_display_order = self._append_new_chapters_to_display_order(
                previous_display,
                chapter_dirs,
            )
        else:
            self._chapter_display_order = self._ordered_chapters_for_display(chapter_dirs)
        self.selected_chapters &= set(chapter_dirs)
        self.latest_new_chapter = self.settings_store.get_latest_new_chapter(self.webtoon.name)
        chapter_names = set(chapter_dirs)
        self.scene_bookmark_counts = {
            chapter: count
            for chapter, count in self.scene_bookmark_store.counts_for_webtoon(self.webtoon.name).items()
            if chapter in chapter_names
        }
        self._sync_remote_chapter_state(rebuild_chapter_list=False)
        progress = self._detail_progress()
        self._update_chapter_count_label()
        self._build_chapter_list(progress)
        self._maybe_auto_open_single_manga_preview()
        self._sync_chapter_batch_actions()
        return True

    def _open_chapter_editor(self, chapter: str) -> None:
        if self.webtoon is None or self.progress_store is None or not self._can_edit_chapter_pages(chapter):
            return
        chapter_path = os.path.join(self.webtoon.path, chapter)
        dialog = ChapterEditorDialog(
            self.webtoon.name,
            chapter,
            chapter_path,
            progress_store=self.progress_store,
            scene_bookmark_store=self.scene_bookmark_store,
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted or not dialog.changed:
            return

        logger.info("Chapter page order updated for %s / %s", self.webtoon.name, chapter)
        self.progress_map = self.progress_store.get_progress_map(self.webtoon.name)
        self.scene_bookmark_counts = self.scene_bookmark_store.counts_for_webtoon(self.webtoon.name)
        progress = self._detail_progress()
        if self._manga_preview_active and self._manga_preview_chapter == chapter and chapter in self.webtoon.chapters:
            self._open_manga_chapter_preview(chapter, self.webtoon.chapters.index(chapter))
        else:
            self._build_chapter_list(progress)

        single_manga_chapter = self._single_manga_chapter()
        if single_manga_chapter:
            total_pages = self._chapter_total_images(single_manga_chapter)
            self.last_read_label.setText(t("library.detail.pages", count=total_pages) if total_pages > 0 else "")
        elif progress:
            scroll, total = self._progress_for_chapter(progress["chapter"])
            percent = self._calc_percent(scroll, total)
            self.last_read_label.setText(t("library.detail.last_read_status", chapter=progress["chapter"], percent=percent))
        else:
            self.last_read_label.setText(t("library.detail.not_started"))

    def _open_chapter(self, chapter: str):
        logger.info("Opening chapter from detail page: %s / %s", self.webtoon.name if self.webtoon else "<none>", chapter)
        if not self._refresh_webtoon_from_disk():
            return
        if chapter not in self.webtoon.chapters:
            QMessageBox.information(
                self,
                t("library.detail.chapter_removed_title"),
                t("library.detail.chapter_removed_text", chapter=chapter),
            )
            return
        if self.latest_new_chapter == chapter:
            self.settings_store.clear_latest_new_chapter(self.webtoon.name)
            self.latest_new_chapter = None
        idx = self.webtoon.chapters.index(chapter)
        if self._is_manga_webtoon():
            self._open_manga_chapter_preview(chapter, idx)
            return
        self.main_window.open_chapter_with_prompt(self.webtoon, idx)

    def _chapter_image_paths(self, chapter: str) -> list[str]:
        if self.webtoon is None or chapter not in self.webtoon.chapters:
            return []
        return sorted(
            list_chapter_image_paths(self.webtoon.path, chapter),
            key=lambda path: _page_sort_key(os.path.basename(path)),
        )

    def _open_manga_chapter_preview(self, chapter: str, chapter_index: int) -> None:
        image_paths = self._chapter_image_paths(chapter)
        if not image_paths:
            QMessageBox.information(
                self,
                t("library.detail.chapter_empty_title"),
                t("library.detail.chapter_empty_text", chapter=chapter),
            )
            return
        self._manga_preview_chapter = chapter
        self._manga_preview_index = int(chapter_index)
        self._manga_preview_generation += 1
        self._clear_manga_preview_grid()
        self._manga_preview_columns = self._manga_preview_column_count()
        scene_counts = self._scene_counts_for_preview_chapter(chapter)
        visible_pages = [(index, image_path) for index, image_path in enumerate(image_paths)]
        if self.show_only_scene_marks:
            visible_pages = [(index, image_path) for index, image_path in visible_pages if scene_counts.get(index, 0) > 0]
        tile_entries = [
            (visible_pos, image_path, page_index, scene_counts.get(page_index, 0))
            for visible_pos, (page_index, image_path) in enumerate(visible_pages)
        ]
        first_batch = tile_entries[:MANGA_PREVIEW_TILE_BATCH_SIZE]
        remaining = tile_entries[MANGA_PREVIEW_TILE_BATCH_SIZE:]
        self._append_manga_preview_tiles(first_batch, chapter_index)
        self._manga_preview_pending_tiles = list(remaining)
        self._set_manga_preview_visible(True)
        QTimer.singleShot(0, self._refresh_manga_preview_layout)
        self.manga_preview_scroll.verticalScrollBar().setValue(0)
        self._queue_manga_preview_indexes(self._visible_manga_preview_indexes(), prioritize=True)
        if self._manga_preview_pending_tiles:
            self._manga_preview_build_timer.start(0)

    def _open_manga_preview_page(self, chapter_index: int, page_index: int) -> None:
        logger.info(
            "Opening manga chapter page from inline detail preview: %s / %s page=%d",
            self.webtoon.name if self.webtoon else "<none>",
            self._manga_preview_chapter,
            page_index,
        )
        self.main_window.open_chapter(self.webtoon, chapter_index, float(page_index))

    def _continue_reading(self):
        logger.info("Continue reading requested for %s", self.webtoon.name if self.webtoon else "<none>")
        if self._is_remote_library_title():
            progress = self._detail_progress()
            remote_entries = self._remote_series_entries()
            if not remote_entries:
                return
            target = self._remote_entry_for_progress(progress) or remote_entries[0]
            if progress:
                current_index = remote_entries.index(target)
                scroll_pct = float(progress.get("scroll") or 0.0)
                total_images = int(progress.get("total_images") or 0)
                if total_images > 0 and scroll_pct >= total_images and current_index + 1 < len(remote_entries):
                    target = remote_entries[current_index + 1]
            self._open_remote_chapter(target)
            return
        if not self._refresh_webtoon_from_disk():
            return
        progress = self._detail_progress()
        if not progress:
            self.main_window.open_chapter(self.webtoon, 0, 0.0)
            return
        chapter = progress["chapter"]
        scroll_pct = progress.get("scroll", 0.0)
        if chapter in self.webtoon.chapters:
            idx = self.webtoon.chapters.index(chapter)
            total_images = int(progress.get("total_images", 0) or 0)
            if total_images > 0 and scroll_pct >= total_images and idx + 1 < len(self.webtoon.chapters):
                self.main_window.open_chapter(self.webtoon, idx + 1, 0.0)
                return
            self.main_window.open_chapter(self.webtoon, idx, scroll_pct)
        else:
            QMessageBox.information(
                self,
                t("library.detail.chapter_removed_title"),
                t("library.detail.saved_chapter_removed_text", chapter=chapter),
            )

    def _go_back(self):
        if self._manga_preview_active:
            if self._single_manga_chapter():
                logger.info("Leaving single-chapter manga preview for %s back to library", self.webtoon.name if self.webtoon else "<none>")
                self.main_window.open_library()
                return
            logger.info("Leaving inline manga preview for %s", self.webtoon.name if self.webtoon else "<none>")
            self._hide_manga_page_preview()
            return
        logger.info("Returning from detail page to %s", self._detail_origin_target())
        self.main_window.open_library()

    def apply_theme(self):
        self.setStyleSheet(PAGE_BG_STYLE)
        if hasattr(self, "back_btn"):
            self.back_btn.setIcon(qta.icon("fa5s.arrow-left", color=TEXT_MUTED))
            self.back_btn.setStyleSheet(TOOLBAR_TEXT_BUTTON_STYLE)
        if hasattr(self, "edit_btn"):
            self.edit_btn.setIcon(qta.icon("fa5s.edit", color=TEXT_MUTED))
            self.edit_btn.setStyleSheet(TOOLBAR_TEXT_BUTTON_STYLE)
        if hasattr(self, "saved_marks_btn"):
            self.saved_marks_btn.setIcon(qta.icon("fa5s.bookmark", color=TEXT_MUTED))
            self.saved_marks_btn.setStyleSheet(TOOLBAR_TEXT_BUTTON_STYLE)
        if hasattr(self, "sort_btn"):
            icon_name = "fa5s.sort-amount-down" if getattr(self, "sort_latest_first", True) else "fa5s.sort-amount-up"
            self.sort_btn.setIcon(qta.icon(icon_name, color=TEXT_MUTED))
            self.sort_btn.setStyleSheet(MINIMAL_FILTER_BUTTON_STYLE)
        if hasattr(self, "hide_specials_btn"):
            icon_name = "fa5s.eye-slash" if getattr(self, "hide_specials", False) else "fa5s.eye"
            self.hide_specials_btn.setIcon(qta.icon(icon_name, color=TEXT_MUTED))
            self.hide_specials_btn.setStyleSheet(MINIMAL_FILTER_BUTTON_BLUE_CHECKED_STYLE)
        if hasattr(self, "bookmarks_filter_btn"):
            self.bookmarks_filter_btn.setIcon(qta.icon("fa5s.star", color=TEXT_MUTED))
            self.bookmarks_filter_btn.setStyleSheet(MINIMAL_FILTER_BUTTON_GOLD_CHECKED_STYLE)
        if hasattr(self, "scene_marks_filter_btn"):
            self.scene_marks_filter_btn.setIcon(qta.icon("fa5s.map-marker-alt", color=TEXT_MUTED))
            self.scene_marks_filter_btn.setStyleSheet(MINIMAL_FILTER_BUTTON_BLUE_CHECKED_STYLE)
        if hasattr(self, "continue_btn"):
            self.continue_btn.setIcon(qta.icon("fa5s.play", color=BG))
            self.continue_btn.setStyleSheet(PRIMARY_ACTION_BUTTON_STYLE)
        if hasattr(self, "start_btn"):
            self.start_btn.setIcon(qta.icon("fa5s.step-backward", color=BG))
            self.start_btn.setStyleSheet(PRIMARY_ACTION_BUTTON_STYLE)
        if hasattr(self, "update_btn"):
            self.update_btn.setIcon(qta.icon("fa5s.sync", color=TEXT))
            self.update_btn.setStyleSheet(SECONDARY_ACTION_BUTTON_STYLE)
        if hasattr(self, "download_new_btn"):
            self.download_new_btn.setIcon(qta.icon("fa5s.download", color=TEXT))
            self.download_new_btn.setStyleSheet(SECONDARY_ACTION_BUTTON_STYLE)
        if hasattr(self, "batch_bar"):
            self.batch_bar.setStyleSheet(BATCH_BAR_STYLE)
        if hasattr(self, "selection_label"):
            self.selection_label.setStyleSheet(BATCH_LABEL_STYLE)
        if hasattr(self, "chapter_scroll"):
            self.chapter_scroll.setStyleSheet(CHAPTER_SCROLL_AREA_STYLE)
        if hasattr(self, "chapter_list_widget"):
            self.chapter_list_widget.setStyleSheet(CHAPTER_LIST_WIDGET_STYLE)
        if getattr(self, "webtoon", None) is not None and getattr(self, "progress_store", None) is not None:
            self._sync_webtoon_bookmark_button()
            self._build_chapter_list(self._detail_progress())

    def attach_update_service(self, service):
        if self._update_service is service:
            return
        logger.info("Attaching shared update service to detail page")
        self._update_service = service
        self._update_service.download_started.connect(self._on_update_started)
        self._update_service.download_finished.connect(self._on_update_finished)
        self._update_service.status_changed.connect(self._on_update_status_changed)
        self._update_service.progress_changed.connect(self._on_update_progress_changed)
        self._update_service.library_changed.connect(self._on_update_library_changed)
        self._sync_update_button()

    def attach_manual_download_service(self, service):
        if self._manual_download_service is service:
            return
        logger.info("Attaching manual download service to detail page")
        self._manual_download_service = service
        self._manual_download_service.download_started.connect(self._on_update_started)
        self._manual_download_service.download_finished.connect(self._on_update_finished)
        self._manual_download_service.status_changed.connect(self._on_update_status_changed)
        self._manual_download_service.progress_changed.connect(self._on_update_progress_changed)
        self._manual_download_service.library_changed.connect(self._on_update_library_changed)
        self._sync_update_button()

    def _active_progress_service(self):
        if self.webtoon is None:
            return None
        if self._update_service is not None and self._update_service.has_active_download(self.webtoon.name):
            return self._update_service
        if self._manual_download_service is not None and self._manual_download_service.has_active_download(self.webtoon.name):
            return self._manual_download_service
        return None

    def _cooldown_remaining(self) -> int:
        if self.webtoon is None:
            return 0
        return cooldown_remaining(self.settings_store.get_last_update_at(self.webtoon.name))

    def _start_update(self):
        if self.webtoon is None or self._update_service is None:
            return
        if self.settings_store.get_completed(self.webtoon.name):
            logger.info("Detail page update blocked for completed webtoon %s", self.webtoon.name)
            self._sync_update_button()
            return
        source_url = self._remote_source_url()
        if not source_url:
            return
        if self._cooldown_remaining() > 0:
            logger.info("Detail page update blocked by cooldown for %s", self.webtoon.name)
            self._sync_update_button()
            return
        logger.info("Starting detail-page update for %s", self.webtoon.name)
        error = self._update_service.start_download(
            source_url,
            load_library_path(),
            preferred_name=self.webtoon.name,
        )
        if error:
            logger.warning("Failed to start detail-page update for %s: %s", self.webtoon.name, error)
            self._sync_update_button()
            return
        self._sync_update_button()

    def _sync_update_button(self):
        if self.webtoon is None:
            self.update_btn.hide()
            self.update_progress_label.hide()
            self.update_progress_circle.hide()
            return
        if self._is_remote_library_title():
            self.update_btn.hide()
            self.update_progress_label.hide()
            self.update_progress_circle.hide()
            return
        active_service = self._active_progress_service()
        if active_service is not None:
            self.update_btn.show()
            current, total = active_service.get_progress(self.webtoon.name)
            if total > 0:
                self._update_progress_current = current
                self._update_progress_total = total
            self.update_btn.setEnabled(False)
            self.update_btn.setText(t("library.detail.updating"))
            self._show_update_progress()
            return
        if self.settings_store.get_completed(self.webtoon.name):
            self.update_btn.hide()
            self.update_progress_label.hide()
            self.update_progress_circle.hide()
            return
        source_url = self._remote_source_url()
        if not source_url:
            self.update_btn.hide()
            self.update_progress_label.hide()
            self.update_progress_circle.hide()
            return
        if not is_scraper_enabled_for_url(source_url):
            self.update_btn.hide()
            self.update_progress_label.hide()
            self.update_progress_circle.hide()
            return

        self.update_btn.show()
        self._update_progress_current = 0
        self._update_progress_total = 0
        self.update_progress_label.hide()
        self.update_progress_circle.hide()
        remaining = self._cooldown_remaining()
        self.update_btn.setEnabled(remaining == 0)
        self.update_btn.setText(t("library.detail.cooldown", remaining=remaining) if remaining > 0 else t("library.detail.update"))

    def _on_update_started(self, name: str):
        if self.webtoon and name == self.webtoon.name:
            self._update_progress_current = 0
            self._update_progress_total = 0
        self._sync_update_button()

    def _on_update_finished(self, name: str, status: str):
        logger.info("Detail page received update finished for %s with status=%s", name, status)
        if self.webtoon and name == self.webtoon.name and self._pending_remote_chapter_urls:
            self._pending_remote_chapter_urls.clear()
        if status == "Completed" and self.webtoon and name == self.webtoon.name:
            self.settings_store.set_last_update_at(name, int(time.time()))
            self.latest_new_chapter = self.settings_store.get_latest_new_chapter(name)
            self._refresh_webtoon_from_disk(preserve_display_order=True)
        elif self.webtoon and name == self.webtoon.name:
            self._sync_remote_chapter_state()
        self._sync_update_button()

    def _on_update_status_changed(self, name: str, status: str):
        if self.webtoon and name == self.webtoon.name:
            self._sync_update_button()

    def _on_update_progress_changed(self, name: str, current: int, total: int):
        if not self.webtoon or name != self.webtoon.name:
            return
        self._update_progress_current = max(0, int(current))
        self._update_progress_total = max(0, int(total))
        self._show_update_progress()

    def _on_update_library_changed(self, name: str):
        if self.webtoon and name == self.webtoon.name:
            self._pending_disk_refresh = True
            if not self._disk_refresh_timer.isActive():
                self._disk_refresh_timer.start(1000 if self._is_current_webtoon_updating() else 150)
            self._sync_update_button()

    def _flush_disk_refresh(self):
        if not self._pending_disk_refresh:
            return
        self._pending_disk_refresh = False
        self._refresh_webtoon_from_disk(preserve_display_order=True)
        self._sync_update_button()

    def _open_edit_dialog(self):
        if self.webtoon is None or self.progress_store is None:
            return
        logger.info("Opening edit dialog for %s", self.webtoon.name)

        dlg = EditWebtoonDialog(
            self.webtoon,
            settings_store=self.settings_store,
            progress_store=self.progress_store,
            parent=self,
        )
        if dlg.exec() != QDialog.Accepted:
            return

        self.main_window.library.load_library()
        self.main_window.library.refresh_progress()

        if dlg.deleted:
            self.main_window.stack.setCurrentWidget(self.main_window.library)
            return

        updated = next(
            (w for w in self.main_window.library._webtoons if w.name == self.webtoon.name),
            None,
        )
        if updated is None:
            self.main_window.stack.setCurrentWidget(self.main_window.library)
            return

        self.load_webtoon(updated, self.progress_store)
        self.main_window.stack.setCurrentWidget(self)

    def _toggle_sort(self):
        self.sort_latest_first = not self.sort_latest_first
        logger.info(
            "Detail page sort toggled for %s latest_first=%s",
            self.webtoon.name if self.webtoon else "<none>",
            self.sort_latest_first,
        )
        if self.sort_latest_first:
            self.sort_btn.setText(t("library.detail.sort_latest"))
            self.sort_btn.setIcon(qta.icon("fa5s.sort-amount-down", color=TEXT_MUTED))
        else:
            self.sort_btn.setText(t("library.detail.sort_oldest"))
            self.sort_btn.setIcon(qta.icon("fa5s.sort-amount-up", color=TEXT_MUTED))
        self.sort_btn.setIconSize(QSize(12, 12))
        if self.webtoon is not None:
            self._chapter_display_order = self._ordered_chapters_for_display(self.webtoon.chapters)
        if self._new_remote_chapters:
            self._sync_remote_chapter_state(rebuild_chapter_list=False)
        progress = self._detail_progress()
        self._build_chapter_list(progress)

    def _show_update_progress(self):
        if self.webtoon is None:
            self.update_progress_label.hide()
            self.update_progress_circle.hide()
            return
        if self._update_progress_total > 0:
            percent = int((max(0, min(self._update_progress_current, self._update_progress_total)) / self._update_progress_total) * 100)
            self.update_progress_circle.set_percent(percent)
            self.update_progress_label.setText(
                t("library.detail.downloading_progress", current=self._update_progress_current, total=self._update_progress_total)
            )
        else:
            self.update_progress_circle.set_percent(0)
            self.update_progress_label.setText(t("library.detail.downloading_simple"))
        self.update_progress_circle.show()
        self.update_progress_label.show()

    def _start_from_beginning(self): 
        logger.info("Start from beginning requested for %s", self.webtoon.name if self.webtoon else "<none>")
        if self._is_remote_library_title():
            remote_entries = self._remote_series_entries()
            if remote_entries:
                self._open_remote_chapter(remote_entries[0])
            return
        self.main_window.open_chapter(self.webtoon, 0)







