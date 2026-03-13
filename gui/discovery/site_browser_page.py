import threading
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QByteArray, QBuffer, QEvent, QIODevice, QObject, QPoint, QSize, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QImageReader, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.app_logging import get_logger
from gui.common.styles import (
    BG,
    BUTTON_STYLE,
    EMPTY_STATE_LABEL_STYLE,
    ERROR_LABEL_STYLE,
    PAGE_BG_STYLE,
    PAGE_TITLE_STYLE,
    NEW_CHIP_STYLE,
    SCROLL_AREA_STYLE,
    SEARCH_INPUT_STYLE,
    SECTION_LABEL_STYLE,
    STATUS_LABEL_STYLE,
    TEXT_DIM_LABEL_STYLE,
    TRANSPARENT_BG_STYLE,
    card_image_border_style,
)
from gui.library.webtoon_card import CARD_HEIGHT, CARD_RADIUS, CARD_WIDTH
from library.library_manager import scan_library
from gui.settings.settings_page import load_library_path
from scrapers.base import ScraperError
from scrapers.discovery_registry import get_all_discovery_providers
from scrapers.models import CatalogSeries

logger = get_logger(__name__)
DISCOVERY_CARD_SPACING = 16
DISCOVERY_AUTO_SCROLL_CURSOR_SIZE = 32
DISCOVERY_AUTO_SCROLL_LINE = "#fff0ec"

DISCOVERY_COMBO_STYLE = """
    QComboBox {
        background: #181212;
        border: 1px solid #4b302c;
        border-radius: 6px;
        padding: 6px 10px;
        color: #fff0ec;
        font-size: 13px;
        min-width: 180px;
    }
    QComboBox::drop-down {
        border: none;
        width: 24px;
    }
    QComboBox QAbstractItemView {
        background: #171111;
        color: #fff0ec;
        border: 1px solid #4b302c;
        selection-background-color: #2b1c1b;
    }
"""

DISCOVERY_CARD_TITLE_STYLE = """
    QLabel {
        color: #fff0ec;
        font-size: 12px;
        font-weight: 500;
        background: transparent;
        border: none;
        padding: 0;
    }
"""
DISCOVERY_CARD_COUNT_STYLE = TEXT_DIM_LABEL_STYLE
DISCOVERY_FILTER_BUTTON_STYLE = BUTTON_STYLE + """
    QPushButton:checked {
        background-color: #2a1716;
        border-color: #ff8a7a;
        color: #fff0ec;
    }
"""
DISCOVERY_LOADING_LABEL_STYLE = """
    QLabel {
        color: #ffd7cf;
        background: #1c1413;
        border: 1px solid #4b302c;
        border-radius: 10px;
        padding: 10px 16px;
        font-size: 12px;
        font-weight: 600;
    }
"""


class DiscoveryTitleLabel(QLabel):

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full_text = text
        self.setText(text)

    def setText(self, text: str):
        self._full_text = text or ""
        self._update_elided_text()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_elided_text()

    def _update_elided_text(self):
        metrics = QFontMetrics(self.font())
        width = max(0, self.contentsRect().width())
        if width <= 0:
            super().setText(self._full_text)
            return
        super().setText(metrics.elidedText(self._full_text, Qt.ElideRight, width))


class DiscoveryCoverLoader(QObject):

    loaded = Signal(object, object, str)
    _MAX_WORKERS = 8
    _MAX_CACHE_ENTRIES = 256

    def __init__(self, parent=None):
        super().__init__(parent)
        self._executor = ThreadPoolExecutor(
            max_workers=self._MAX_WORKERS,
            thread_name_prefix="discovery-cover",
        )
        self._lock = threading.Lock()
        self._inflight: dict[tuple[str, tuple[tuple[str, str], ...]], list[object]] = {}
        self._cache: OrderedDict[tuple[str, tuple[tuple[str, str], ...]], tuple[bytes | None, str]] = OrderedDict()

    def load(self, widget, url: str, headers: dict[str, str] | None):
        request_headers = dict(headers or {})
        key = (url, tuple(sorted(request_headers.items())))

        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                data, error = cached
                self.loaded.emit(widget, data, error)
                return

            waiting_widgets = self._inflight.get(key)
            if waiting_widgets is not None:
                waiting_widgets.append(widget)
                return

            self._inflight[key] = [widget]

        self._executor.submit(self._fetch_cover, key, request_headers)

    def _fetch_cover(self, key: tuple[str, tuple[tuple[str, str], ...]], headers: dict[str, str]) -> None:
        url = key[0]
        data = None
        error = ""
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=20) as response:
                data = response.read()
        except Exception as e:
            error = str(e)

        with self._lock:
            waiting_widgets = self._inflight.pop(key, [])
            self._cache[key] = (data, error)
            self._cache.move_to_end(key)
            while len(self._cache) > self._MAX_CACHE_ENTRIES:
                self._cache.popitem(last=False)

        for widget in waiting_widgets:
            self.loaded.emit(widget, data, error)


class DiscoveryCatalogLoader(QObject):

    loaded = Signal(int, str, int, bool, object, str)

    def load(self, request_id: int, provider, page: int, reset: bool, search_query: str = ""):
        provider_key = getattr(provider, "site_name", "") or ""

        def worker():
            try:
                result = provider.get_catalog_page(page=page, search_query=search_query)
                self.loaded.emit(request_id, provider_key, page, reset, result, "")
            except ScraperError as e:
                self.loaded.emit(request_id, provider_key, page, reset, None, str(e))
            except Exception as e:
                logger.exception("Unexpected catalog loading failure for %s", provider_key)
                self.loaded.emit(request_id, provider_key, page, reset, None, str(e))

        threading.Thread(target=worker, daemon=True).start()


class DiscoveryEntryWidget(QFrame):

    def __init__(self, entry, on_download, cover_loader: DiscoveryCoverLoader, local_info: dict | None):
        super().__init__()
        self.entry = entry
        self._on_download = on_download
        self._cover_loader = cover_loader
        self._local_info = local_info or {}
        self._card_width = CARD_WIDTH
        self._card_height = int(CARD_WIDTH * (CARD_HEIGHT / CARD_WIDTH))
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self.setStyleSheet(TRANSPARENT_BG_STYLE)
        self.setFixedWidth(self._card_width + 16)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.thumb_label = QLabel("No Cover", self)
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setFixedSize(self._card_width, self._card_height)
        self.thumb_label.setStyleSheet(card_image_border_style("#2a2a2a", CARD_RADIUS))
        layout.addWidget(self.thumb_label)

        self.title_label = DiscoveryTitleLabel(entry.title or "Untitled", self)
        self.title_label.setFixedWidth(max(80, self._card_width - 4))
        self.title_label.setToolTip(entry.title or "Untitled")
        self.title_label.setStyleSheet(DISCOVERY_CARD_TITLE_STYLE)
        title_font = QFont("Segoe UI", 10)
        title_font.setWeight(QFont.Medium)
        self.title_label.setFont(title_font)
        layout.addWidget(self.title_label)

        self.count_label = QLabel(self._format_chapter_count(self.entry.total_chapters), self)
        self.count_label.setStyleSheet(DISCOVERY_CARD_COUNT_STYLE)
        self.count_label.setVisible(bool(self.count_label.text().strip()))

        self.new_chip = QLabel("", self)
        self.new_chip.setAlignment(Qt.AlignCenter)
        self.new_chip.setFixedHeight(14)
        self.new_chip.setStyleSheet(NEW_CHIP_STYLE)
        self._refresh_new_chip()

        info_row = QHBoxLayout()
        info_row.setContentsMargins(0, 0, 0, 0)
        info_row.setSpacing(6)
        info_row.addWidget(self.count_label)
        info_row.addWidget(self.new_chip, 0, Qt.AlignVCenter)
        info_row.addStretch()
        layout.addLayout(info_row)

        self.setToolTip(self._build_tooltip())
        self._cover_requested = False
        self._cover_applied = False

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._on_download(self.entry.url)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def enterEvent(self, event):
        self.thumb_label.setStyleSheet(card_image_border_style("#666666", CARD_RADIUS))
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.thumb_label.setStyleSheet(card_image_border_style("#2a2a2a", CARD_RADIUS))
        super().leaveEvent(event)

    def ensure_cover_requested(self):
        if self._cover_requested or self._cover_applied:
            return
        if not self.entry.cover_url or self._cover_loader is None:
            return
        self._cover_requested = True
        self._cover_loader.load(self, self.entry.cover_url, self.entry.cover_headers or {})

    def apply_cover_data(self, data):
        if not data:
            self._cover_requested = False
            return
        pixmap = self._decode_cover_pixmap(data)
        if pixmap.isNull():
            return
        scaled = pixmap.scaled(
            self._card_width,
            self._card_height,
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation,
        )
        x = max(0, (scaled.width() - self._card_width) // 2)
        y = max(0, (scaled.height() - self._card_height) // 2)
        cropped = scaled.copy(x, y, self._card_width, self._card_height)

        rounded = QPixmap(self._card_width, self._card_height)
        rounded.fill(Qt.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, self._card_width, self._card_height, CARD_RADIUS, CARD_RADIUS)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, cropped)
        painter.end()

        self.thumb_label.setPixmap(rounded)
        self.thumb_label.setText("")
        self._cover_applied = True

    def _decode_cover_pixmap(self, data) -> QPixmap:
        byte_array = QByteArray(data)
        buffer = QBuffer()
        buffer.setData(byte_array)
        if not buffer.open(QIODevice.ReadOnly):
            logger.warning("Discovery cover buffer open failed for %s", self.entry.title)
            return QPixmap()

        reader = QImageReader(buffer)
        size = reader.size()
        if size.isValid() and size.width() > 0 and size.height() > 0:
            width_ratio = self._card_width / size.width()
            height_ratio = self._card_height / size.height()
            scale_ratio = max(width_ratio, height_ratio)
            scaled_size = QSize(
                max(1, int(size.width() * scale_ratio)),
                max(1, int(size.height() * scale_ratio)),
            )
            reader.setScaledSize(scaled_size)

        image = reader.read()
        if image.isNull():
            logger.warning(
                "Discovery cover reader failed for %s: %s",
                self.entry.title,
                reader.errorString(),
            )
            return QPixmap()
        return QPixmap.fromImage(image)

    def _format_chapter_count(self, count: int | None) -> str:
        if count is None or count <= 0:
            return "Unknown chapters"
        if count == 1:
            return "1 chapter"
        return f"{count} chapters"

    def _build_tooltip(self) -> str:
        parts = [self.entry.title or "Untitled"]
        local_count = self._local_info.get("chapters")
        if local_count:
            parts.append(f"In library: {local_count} chapters")
        remote_count = self.entry.total_chapters
        if local_count and remote_count and remote_count > local_count:
            parts.append(f"New chapters available: {remote_count - local_count}")
        parts.append("Click to download")
        return "\n".join(parts)

    def _refresh_new_chip(self):
        local_count = self._local_info.get("chapters")
        remote_count = self.entry.total_chapters
        new_count = 0
        if local_count and remote_count and remote_count > local_count:
            new_count = remote_count - local_count
        if new_count > 0:
            self.new_chip.setText(f"+{new_count} New")
            self.new_chip.show()
        else:
            self.new_chip.hide()


class SiteBrowserPage(QWidget):

    AUTO_LOAD_MORE_THRESHOLD_PX = 120
    AUTO_LOAD_MORE_TRIGGER_RATIO = 0.35
    AUTO_LOAD_MORE_DELAY_MS = 40
    COVER_PRELOAD_MARGIN_PX = 240

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self._providers_by_key = {}
        self._current_page = 1
        self._has_next_page = False
        self._loaded_once = False
        self._cover_loader = DiscoveryCoverLoader(self)
        self._cover_loader.loaded.connect(self._on_cover_loaded)
        self._catalog_loader = DiscoveryCatalogLoader(self)
        self._catalog_loader.loaded.connect(self._on_catalog_loaded)
        self._catalog_request_id = 0
        self._catalog_loading = False
        self._library_titles = {}
        self._library_sources = {}
        self._library_entries_by_site = {}
        self._library_snapshot_dirty = True
        self._library_snapshot_path = ""
        self._loaded_entries = []
        self._entry_widgets = []
        self._empty_state_label = None
        self._loading_more_label = None
        self._entry_keys = set()
        self._search_text = ""
        self._pending_append_anchor_bottom = False
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._apply_search)
        self.auto_scroll = False
        self.auto_scroll_origin = QPoint()
        self.current_mouse_pos = QPoint()
        self.scroll_timer = QTimer(self)
        self.scroll_timer.timeout.connect(self.perform_auto_scroll)
        self._auto_load_timer = QTimer(self)
        self._auto_load_timer.setSingleShot(True)
        self._auto_load_timer.timeout.connect(self._trigger_auto_load_more)
        self._auto_scroll_direction = 0
        self._auto_scroll_cursors = {
            -1: self._build_auto_scroll_cursor(-1),
            0: self._build_auto_scroll_cursor(0),
            1: self._build_auto_scroll_cursor(1),
        }

        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(PAGE_BG_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 32, 32, 32)
        root.setSpacing(20)
        root.setAlignment(Qt.AlignTop)

        title = QLabel("Discover")
        title.setStyleSheet(PAGE_TITLE_STYLE)
        root.addWidget(title)

        controls = QHBoxLayout()
        controls.setSpacing(8)

        self.site_combo = QComboBox()
        self.site_combo.setStyleSheet(DISCOVERY_COMBO_STYLE)
        self.site_combo.currentIndexChanged.connect(self._on_site_changed)
        controls.addWidget(self.site_combo)

        self.page_label = QLabel("Page 1")
        self.page_label.setStyleSheet(SECTION_LABEL_STYLE)
        controls.addWidget(self.page_label)

        self.search_input = QLineEdit(self)
        self.search_input.setPlaceholderText("Search series...")
        self.search_input.setFixedHeight(36)
        self.search_input.setMinimumWidth(220)
        self.search_input.setStyleSheet(SEARCH_INPUT_STYLE)
        self.search_input.textChanged.connect(self._on_search_text_changed)
        controls.addWidget(self.search_input)

        self.downloaded_only_btn = QPushButton("Downloaded Only")
        self.downloaded_only_btn.setCheckable(True)
        self.downloaded_only_btn.setStyleSheet(DISCOVERY_FILTER_BUTTON_STYLE)
        self.downloaded_only_btn.toggled.connect(self._on_downloaded_only_toggled)
        controls.addWidget(self.downloaded_only_btn)

        self.load_more_btn = QPushButton("Load More")
        self.load_more_btn.setStyleSheet(BUTTON_STYLE)
        self.load_more_btn.clicked.connect(self.load_more_catalog)
        self.load_more_btn.hide()

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setStyleSheet(BUTTON_STYLE)
        self.refresh_btn.clicked.connect(lambda: self.refresh_catalog(reset=True))
        controls.addWidget(self.refresh_btn)

        controls.addStretch()
        root.addLayout(controls)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet(ERROR_LABEL_STYLE)
        self.error_label.hide()
        root.addWidget(self.error_label)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(STATUS_LABEL_STYLE)
        root.addWidget(self.status_label)

        section_label = QLabel("Available series")
        section_label.setStyleSheet(SECTION_LABEL_STYLE)
        root.addWidget(section_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet(SCROLL_AREA_STYLE)
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll_position_changed)
        self.scroll.verticalScrollBar().rangeChanged.connect(self._on_scroll_range_changed)
        self.scroll.viewport().setMouseTracking(True)
        self.scroll.viewport().installEventFilter(self)

        self.content = QWidget()
        self.content.setMouseTracking(True)
        self.content.installEventFilter(self)
        self.content.setStyleSheet(f"background: {BG};")
        self.content_layout = QGridLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setHorizontalSpacing(DISCOVERY_CARD_SPACING)
        self.content_layout.setVerticalSpacing(DISCOVERY_CARD_SPACING)
        self.content_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.scroll.setWidget(self.content)
        root.addWidget(self.scroll)

        self._reload_scrapers(load_catalog=False)

    def showEvent(self, event):
        super().showEvent(event)
        if self.site_combo.count() == 0:
            self._reload_scrapers(load_catalog=False)
        if not self._loaded_once and self.site_combo.count() > 0:
            self._loaded_once = True
            self.refresh_catalog(reset=True)

    def refresh_catalog(self, reset: bool = False):
        if self._catalog_loading:
            logger.info("Ignoring discovery refresh while a catalog request is already in flight")
            return
        provider = self._current_provider()
        if provider is None:
            self._show_message("No discovery providers are available yet.", is_error=False)
            self._set_entries([])
            self._sync_paging()
            return

        if reset:
            self._current_page = 1

        self._show_message("", is_error=False)
        search_query = self._remote_search_query()
        loading_label = f"Loading {self._display_name(provider.site_name)} page {self._current_page}"
        if search_query:
            loading_label += f" for '{search_query}'"
        self.status_label.setText(loading_label + "...")
        logger.info("Loading catalog page %d for %s", self._current_page, provider.site_name)
        self._ensure_library_snapshot()
        self._catalog_loading = True
        self._set_controls_enabled(False)
        self._set_loading_more_visible(not reset and not self.downloaded_only_btn.isChecked() and not self._remote_search_query())
        self._catalog_request_id += 1
        self._catalog_loader.load(self._catalog_request_id, provider, self._current_page, reset, self._remote_search_query())

    def _reload_scrapers(self, load_catalog: bool = True):
        current_key = self.site_combo.currentData()
        self._providers_by_key = {
            provider.site_name: provider for provider in get_all_discovery_providers()
        }

        self.site_combo.blockSignals(True)
        self.site_combo.clear()
        for site_name in sorted(self._providers_by_key):
            self.site_combo.addItem(self._display_name(site_name), site_name)
        self.site_combo.blockSignals(False)

        if self.site_combo.count() == 0:
            self._show_message("No discovery providers are available yet.", is_error=False)
            self._set_entries([])
            self._sync_paging()
            return

        index = self.site_combo.findData(current_key)
        self.site_combo.blockSignals(True)
        self.site_combo.setCurrentIndex(index if index >= 0 else 0)
        self.site_combo.blockSignals(False)
        if load_catalog:
            self.refresh_catalog(reset=True)
        else:
            self._show_message("Select a site to load its catalog.", is_error=False)
            self._set_entries([])
            self._sync_paging()

    def _current_provider(self):
        key = self.site_combo.currentData()
        if not key:
            return None
        return self._providers_by_key.get(key)

    def _on_site_changed(self, _index: int):
        if self._catalog_loading:
            return
        self.refresh_catalog(reset=True)

    def load_more_catalog(self):
        if self._catalog_loading or not self._has_next_page:
            logger.info(
                "Discovery load_more skipped loading=%s has_next=%s page=%d",
                self._catalog_loading,
                self._has_next_page,
                self._current_page,
            )
            return
        scrollbar = self.scroll.verticalScrollBar()
        self._pending_append_anchor_bottom = self._is_near_bottom(scrollbar)
        logger.info(
            "Discovery load_more triggered page=%d value=%d max=%d anchor_bottom=%s",
            self._current_page + 1,
            scrollbar.value(),
            scrollbar.maximum(),
            self._pending_append_anchor_bottom,
        )
        self._current_page += 1
        self.refresh_catalog(reset=False)

    def _sync_paging(self):
        self.page_label.setText(f"Page {self._current_page}")
        self.load_more_btn.setEnabled(self._has_next_page and not self._catalog_loading)

    def _is_near_bottom(self, scrollbar, *, value: int | None = None, maximum: int | None = None) -> bool:
        current_value = scrollbar.value() if value is None else value
        current_maximum = scrollbar.maximum() if maximum is None else maximum
        if current_maximum <= 0:
            return False
        threshold = max(24, self.AUTO_LOAD_MORE_THRESHOLD_PX + 24)
        return current_value >= current_maximum - threshold

    def _should_prefetch_more(self, scrollbar, *, value: int | None = None, maximum: int | None = None, force_fill: bool = False) -> bool:
        current_value = scrollbar.value() if value is None else value
        current_maximum = scrollbar.maximum() if maximum is None else maximum
        if current_maximum <= 0:
            return False
        trigger_value = int(current_maximum * self.AUTO_LOAD_MORE_TRIGGER_RATIO)
        return current_value >= trigger_value

    def _set_entries(self, entries):
        self._loaded_entries = list(entries or [])
        self._entry_keys = {self._entry_key(entry) for entry in self._loaded_entries}
        self._render_entries()

    def _render_entries(self):
        scrollbar = self.scroll.verticalScrollBar()
        old_value = scrollbar.value()
        old_maximum = scrollbar.maximum()
        anchor_to_bottom = self._pending_append_anchor_bottom or (old_maximum > 0 and self._is_near_bottom(scrollbar, value=old_value, maximum=old_maximum))

        self._clear_rendered_entries()
        visible_entries = self._visible_entries()
        if not visible_entries:
            self._show_empty_state()
            self._restore_scroll_position(old_value, anchor_to_bottom)
            self._pending_append_anchor_bottom = False
            return

        self._append_entry_widgets(visible_entries)
        self._relayout_entries()
        self._restore_scroll_position(old_value, anchor_to_bottom)
        self._pending_append_anchor_bottom = False

    def _append_entries(self, entries):
        new_entries = []
        for entry in entries:
            entry_key = self._entry_key(entry)
            if entry_key in self._entry_keys:
                continue
            self._entry_keys.add(entry_key)
            self._loaded_entries.append(entry)
            new_entries.append(entry)

        if not new_entries:
            return

        if self.downloaded_only_btn.isChecked() or self._remote_search_query():
            self._render_entries()
            return

        scrollbar = self.scroll.verticalScrollBar()
        old_value = scrollbar.value()
        old_maximum = scrollbar.maximum()
        anchor_to_bottom = self._pending_append_anchor_bottom or (old_maximum > 0 and self._is_near_bottom(scrollbar, value=old_value, maximum=old_maximum))

        if self._empty_state_label is not None:
            self._empty_state_label.deleteLater()
            self._empty_state_label = None

        self._append_entry_widgets(new_entries)
        self._relayout_entries()
        self._restore_scroll_position(old_value, anchor_to_bottom)
        self._pending_append_anchor_bottom = False

    def _set_loading_more_visible(self, visible: bool):
        if visible:
            if self._loading_more_label is None:
                label = QLabel("Loading more series...")
                label.setAlignment(Qt.AlignCenter)
                label.setStyleSheet(DISCOVERY_LOADING_LABEL_STYLE)
                label.setMinimumHeight(44)
                self._loading_more_label = label
        elif self._loading_more_label is not None:
            self._loading_more_label.deleteLater()
            self._loading_more_label = None

    def _clear_rendered_entries(self):
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._entry_widgets = []
        self._empty_state_label = None
        if self._loading_more_label is not None:
            self._loading_more_label.deleteLater()
            self._loading_more_label = None

    def _show_empty_state(self):
        empty = QLabel("No series found for this page.")
        if self._normalized_search_text():
            empty.setText("No series match your search.")
        elif self.downloaded_only_btn.isChecked():
            empty.setText("No downloaded titles match the loaded discovery results.")
        empty.setAlignment(Qt.AlignCenter)
        empty.setMinimumHeight(120)
        empty.setStyleSheet(EMPTY_STATE_LABEL_STYLE)
        self.content_layout.addWidget(empty, 0, 0)
        self._empty_state_label = empty

    def _append_entry_widgets(self, entries):
        for entry in entries:
            local_info = self._local_info_for_entry(entry)
            widget = DiscoveryEntryWidget(
                entry,
                on_download=self._start_download_for_entry,
                cover_loader=self._cover_loader,
                local_info=local_info,
            )
            widget.setMouseTracking(True)
            widget.installEventFilter(self)
            self._entry_widgets.append(widget)

    def _start_download_for_entry(self, url: str):
        if not url:
            self._show_message("This entry does not expose a downloadable series URL.", is_error=True)
            return
        error = self.main_window.downloader.start_download_from_url(url)
        if error:
            self._show_message(error, is_error=True)
            return
        self._show_message("Download added to the downloader queue.", is_error=False)
        self.main_window.open_downloader()

    def _show_message(self, text: str, is_error: bool):
        self.error_label.setVisible(is_error and bool(text))
        self.error_label.setText(text if is_error else "")
        self.status_label.setText("" if is_error else text)

    def _display_name(self, site_name: str) -> str:
        return site_name.replace("_", " ").title()

    def _refresh_library_snapshot(self):
        try:
            webtoons = scan_library(load_library_path(), self.main_window.settings_store)
        except Exception as e:
            logger.warning("Failed to refresh discovery library snapshot", exc_info=e)
            self._library_titles = {}
            self._library_sources = {}
            self._library_entries_by_site = {}
            return

        title_snapshot = {}
        source_snapshot = {}
        library_entries_by_site = {}
        for webtoon in webtoons:
            info = {
                "name": webtoon.name,
                "webtoon": webtoon,
                "chapters": len(getattr(webtoon, "chapters", []) or []),
                "source_url": self.main_window.settings_store.get_source_url(webtoon.name),
                "source_title": self.main_window.settings_store.get_source_title(webtoon.name),
                "source_site": self.main_window.settings_store.get_source_site(webtoon.name),
                "source_series_id": self.main_window.settings_store.get_source_series_id(webtoon.name),
            }
            title_snapshot[self._normalize_title(webtoon.name)] = info
            source_title = info["source_title"]
            if source_title:
                title_snapshot.setdefault(self._normalize_title(source_title), info)
            source_site = info["source_site"]
            source_series_id = info["source_series_id"]
            source_url = info["source_url"]
            normalized_site = str(source_site).strip()
            normalized_series_id = str(source_series_id).strip()
            if normalized_site and normalized_series_id:
                source_snapshot[(normalized_site, normalized_series_id)] = info
            if normalized_site and source_url:
                library_entries_by_site.setdefault(normalized_site, []).append(
                    CatalogSeries(
                        site=normalized_site,
                        series_id=normalized_series_id or webtoon.name,
                        title=str(info["source_title"] or webtoon.name),
                        url=str(source_url),
                        total_chapters=info["chapters"],
                    )
                )
        self._library_titles = title_snapshot
        self._library_sources = source_snapshot
        self._library_entries_by_site = library_entries_by_site
        self._library_snapshot_dirty = False
        self._library_snapshot_path = load_library_path()

    def _ensure_library_snapshot(self):
        current_path = load_library_path()
        if self._library_snapshot_dirty or self._library_snapshot_path != current_path:
            self._refresh_library_snapshot()

    def invalidate_library_snapshot(self):
        self._library_snapshot_dirty = True

    def attach_update_service(self, service):
        if service is None:
            return
        service.library_changed.connect(self._on_library_changed)

    def attach_manual_download_service(self, service):
        if service is None:
            return
        service.library_changed.connect(self._on_library_changed)

    def _normalize_title(self, title: str) -> str:
        return " ".join((title or "").casefold().split())

    def resizeEvent(self, event):
        scrollbar = self.scroll.verticalScrollBar()
        old_value = scrollbar.value()
        old_maximum = scrollbar.maximum()
        anchor_to_bottom = self._pending_append_anchor_bottom or (old_maximum > 0 and self._is_near_bottom(scrollbar, value=old_value, maximum=old_maximum))
        super().resizeEvent(event)
        self._relayout_entries()
        self._restore_scroll_position(old_value, anchor_to_bottom)
        self._pending_append_anchor_bottom = False

    def _relayout_entries(self):
        if not self._entry_widgets:
            return

        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            widget = item.widget()
            if widget is None:
                continue

        viewport_width = max(1, self.scroll.viewport().width())
        card_span = CARD_WIDTH + 16 + DISCOVERY_CARD_SPACING
        columns = max(1, viewport_width // max(1, card_span))

        for index, widget in enumerate(self._entry_widgets):
            row = index // columns
            column = index % columns
            self.content_layout.addWidget(widget, row, column, Qt.AlignTop | Qt.AlignLeft)

        if self._loading_more_label is not None:
            loading_row = len(self._entry_widgets) // columns
            if self._entry_widgets and len(self._entry_widgets) % columns != 0:
                loading_row += 1
            self.content_layout.addWidget(self._loading_more_label, loading_row, 0, 1, columns, Qt.AlignTop)

        self._request_visible_covers()

    def _request_visible_covers(self):
        if not self._entry_widgets:
            return
        viewport = self.scroll.viewport()
        viewport_rect = viewport.rect().adjusted(
            0,
            -self.COVER_PRELOAD_MARGIN_PX,
            0,
            self.COVER_PRELOAD_MARGIN_PX,
        )
        for widget in self._entry_widgets:
            top_left = widget.mapTo(viewport, widget.rect().topLeft())
            widget_rect = widget.rect().translated(top_left)
            if widget_rect.intersects(viewport_rect):
                widget.ensure_cover_requested()

    def _on_cover_loaded(self, widget, data, error: str):
        if widget not in self._entry_widgets:
            return
        if error:
            logger.warning("Discovery cover request failed for %s: %s", widget.entry.title, error)
            return
        widget.apply_cover_data(data)

    def _on_catalog_loaded(self, request_id: int, provider_key: str, page_number: int, reset: bool, page, error: str):
        if request_id != self._catalog_request_id:
            logger.info("Ignoring stale discovery catalog response for %s page %d", provider_key, page_number)
            return

        self._catalog_loading = False
        self._set_controls_enabled(True)

        current_provider = self._current_provider()
        current_provider_key = getattr(current_provider, "site_name", "") if current_provider is not None else ""
        if provider_key != current_provider_key or page_number != self._current_page:
            logger.info("Ignoring mismatched discovery catalog response for %s page %d", provider_key, page_number)
            return

        if error:
            logger.warning("Catalog loading failed for %s page %d: %s", provider_key, page_number, error)
            self._show_message(error, is_error=True)
            if reset:
                self._set_entries([])
                self._has_next_page = False
            self._set_loading_more_visible(False)
            self._sync_paging()
            return

        self._set_loading_more_visible(False)
        self._has_next_page = bool(getattr(page, "has_next_page", False))
        logger.info(
            "Discovery catalog loaded page=%d reset=%s entries=%d has_next=%s value=%d max=%d",
            page_number,
            reset,
            len(getattr(page, "entries", []) or []),
            self._has_next_page,
            self.scroll.verticalScrollBar().value(),
            self.scroll.verticalScrollBar().maximum(),
        )
        entries = getattr(page, "entries", []) or []
        if reset:
            self._set_entries(entries)
        else:
            self._append_entries(entries)
        visible_count = len(self._visible_entries())
        self.status_label.setText(
            f"{visible_count} series loaded from {self._display_name(provider_key)}"
        )
        self._sync_paging()
        if not self.downloaded_only_btn.isChecked() and not self._remote_search_query():
            self._schedule_auto_load_more(force_fill=True)

    def _set_controls_enabled(self, enabled: bool):
        self.site_combo.setEnabled(enabled)
        self.load_more_btn.setEnabled(enabled and self._has_next_page)
        self.refresh_btn.setEnabled(enabled)

    def _on_scroll_position_changed(self, _value: int):
        self._request_visible_covers()
        self._schedule_auto_load_more(force_fill=False)

    def _on_scroll_range_changed(self, _minimum: int, _maximum: int):
        self._request_visible_covers()
        self._schedule_auto_load_more(force_fill=True)

    def _schedule_auto_load_more(self, *, force_fill: bool):
        if self.downloaded_only_btn.isChecked() or self._remote_search_query() or self._catalog_loading or not self._has_next_page:
            self._auto_load_timer.stop()
            return
        scrollbar = self.scroll.verticalScrollBar()
        should_load = self._should_prefetch_more(scrollbar, force_fill=force_fill)
        if should_load and not self._auto_load_timer.isActive():
            logger.info(
                "Discovery prefetch armed page=%d value=%d max=%d force_fill=%s delay_ms=%d",
                self._current_page,
                scrollbar.value(),
                scrollbar.maximum(),
                force_fill,
                self.AUTO_LOAD_MORE_DELAY_MS,
            )
            self._auto_load_timer.start(self.AUTO_LOAD_MORE_DELAY_MS)

    def _trigger_auto_load_more(self):
        if self.downloaded_only_btn.isChecked() or self._remote_search_query() or self._catalog_loading or not self._has_next_page:
            return
        scrollbar = self.scroll.verticalScrollBar()
        should_load = self._should_prefetch_more(scrollbar, force_fill=True)
        logger.info(
            "Discovery prefetch timer fired page=%d value=%d max=%d should_load=%s",
            self._current_page,
            scrollbar.value(),
            scrollbar.maximum(),
            should_load,
        )
        if should_load:
            self.load_more_catalog()

    def _on_library_changed(self, _name: str):
        self.invalidate_library_snapshot()
        if self.downloaded_only_btn.isChecked():
            self._ensure_library_snapshot()
            self._render_entries()

    def _local_info_for_entry(self, entry) -> dict | None:
        source_key = self._entry_source_key(entry)
        if source_key is not None:
            match = self._library_sources.get(source_key)
            if match is not None:
                return match
        return self._library_titles.get(self._normalize_title(entry.title))

    def _entry_key(self, entry) -> str:
        return str(getattr(entry, "url", "") or getattr(entry, "series_id", "") or getattr(entry, "title", ""))

    def _entry_source_key(self, entry) -> tuple[str, str] | None:
        site = str(getattr(entry, "site", "") or "").strip()
        series_id = str(getattr(entry, "series_id", "") or "").strip()
        if not site or not series_id:
            return None
        return site, series_id

    def _visible_entries(self):
        if self.downloaded_only_btn.isChecked():
            entries = self._downloaded_only_entries()
            if self._search_text:
                entries = [entry for entry in entries if self._entry_matches_search(entry)]
            return entries
        return list(self._loaded_entries)

    def _downloaded_only_entries(self):
        self._ensure_library_snapshot()
        provider = self._current_provider()
        provider_key = getattr(provider, "site_name", "") if provider is not None else ""
        entries = list(self._library_entries_by_site.get(provider_key, []))
        entries.sort(key=lambda entry: self._normalize_title(getattr(entry, "title", "")))
        return entries

    def _normalized_search_text(self) -> str:
        return self._normalize_title(self._search_text)

    def _remote_search_query(self) -> str:
        if self.downloaded_only_btn.isChecked():
            return ""
        return " ".join(str(self._search_text or "").split()).strip()

    def _entry_matches_search(self, entry) -> bool:
        haystack = " ".join(
            [
                str(getattr(entry, "title", "") or ""),
                str(getattr(entry, "author", "") or ""),
                str(getattr(entry, "description", "") or ""),
                str(getattr(entry, "latest_chapter", "") or ""),
            ]
        )
        return self._normalized_search_text() in self._normalize_title(haystack)

    def _on_search_text_changed(self, text: str):
        self._search_text = text
        if self.downloaded_only_btn.isChecked():
            self._render_entries()
            return
        self._search_timer.start()

    def _apply_search(self):
        if self.downloaded_only_btn.isChecked():
            self._render_entries()
            return
        self.refresh_catalog(reset=True)

    def _on_downloaded_only_toggled(self, checked: bool):
        self._auto_load_timer.stop()
        self._search_timer.stop()
        if checked:
            self._ensure_library_snapshot()
            self._render_entries()
            return
        self.refresh_catalog(reset=True)

    def _restore_scroll_position(self, old_value: int, anchor_to_bottom: bool):
        def restore():
            scrollbar = self.scroll.verticalScrollBar()
            if anchor_to_bottom:
                scrollbar.setValue(scrollbar.maximum())
            else:
                scrollbar.setValue(min(old_value, scrollbar.maximum()))
            self._request_visible_covers()

        QTimer.singleShot(0, restore)

    def _build_auto_scroll_cursor(self, direction: int) -> QCursor:
        size = DISCOVERY_AUTO_SCROLL_CURSOR_SIZE
        center = size // 2
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        painter.setPen(QPen(QColor(DISCOVERY_AUTO_SCROLL_LINE), 2))
        painter.setBrush(QColor(DISCOVERY_AUTO_SCROLL_LINE))

        def draw_arrow_up() -> None:
            painter.drawLine(center, 5, center, 13)
            painter.drawLine(center, 5, center - 4, 9)
            painter.drawLine(center, 5, center + 4, 9)

        def draw_arrow_down() -> None:
            painter.drawLine(center, size - 6, center, size - 14)
            painter.drawLine(center, size - 6, center - 4, size - 10)
            painter.drawLine(center, size - 6, center + 4, size - 10)

        if direction <= 0:
            draw_arrow_up()
        if direction >= 0:
            draw_arrow_down()

        painter.end()

        return QCursor(pixmap, center, center)

    def _set_auto_scroll_direction(self, direction: int) -> None:
        viewport = self.scroll.viewport() if hasattr(self, "scroll") else None
        if viewport is None or not self.auto_scroll:
            return
        normalized = -1 if direction < 0 else 1 if direction > 0 else 0
        if normalized == self._auto_scroll_direction:
            return
        self._auto_scroll_direction = normalized
        viewport.setCursor(self._auto_scroll_cursors[normalized])

    def _set_auto_scroll_enabled(self, enabled: bool, *, origin: QPoint | None = None):
        viewport = self.scroll.viewport() if hasattr(self, "scroll") else None
        if viewport is None:
            return
        self.auto_scroll = enabled
        if enabled:
            point = origin if origin is not None else QPoint()
            self.auto_scroll_origin = QPoint(point)
            self.current_mouse_pos = QPoint(point)
            self._auto_scroll_direction = 0
            viewport.setCursor(self._auto_scroll_cursors[0])
            if not self.scroll_timer.isActive():
                self.scroll_timer.start(16)
        else:
            self._auto_scroll_direction = 0
            self.scroll_timer.stop()
            viewport.unsetCursor()

    def eventFilter(self, obj, event):
        viewport = self.scroll.viewport() if hasattr(self, "scroll") else None
        if viewport is not None and isinstance(obj, QWidget):
            handles_scroll_area_event = obj == viewport or obj == self.content or viewport.isAncestorOf(obj)
            if handles_scroll_area_event:
                event_type = event.type()

                if event_type in (QEvent.MouseButtonPress, QEvent.MouseMove):
                    event_pos = event.pos() if obj == viewport else obj.mapTo(viewport, event.pos())

                    if event_type == QEvent.MouseButtonPress and event.button() == Qt.MiddleButton:
                        self._set_auto_scroll_enabled(not self.auto_scroll, origin=event_pos)
                        viewport.update()
                        return True

                    if event_type == QEvent.MouseMove and self.auto_scroll:
                        self.current_mouse_pos = event_pos
                        self._set_auto_scroll_direction(event_pos.y() - self.auto_scroll_origin.y())
                        return True

                    if event_type == QEvent.MouseButtonPress and event.button() == Qt.LeftButton and self.auto_scroll:
                        self._set_auto_scroll_enabled(False)
                        viewport.update()
                        return True

                if event_type in (QEvent.Leave, QEvent.Hide, QEvent.FocusOut) and self.auto_scroll:
                    self._set_auto_scroll_enabled(False)

        return super().eventFilter(obj, event)

    def perform_auto_scroll(self):
        dy = self.current_mouse_pos.y() - self.auto_scroll_origin.y()
        deadzone = 8
        if abs(dy) <= deadzone:
            self._set_auto_scroll_direction(0)
            return
        self._set_auto_scroll_direction(dy)
        speed = ((abs(dy) - deadzone) ** 1.4) * (0.08 if dy > 0 else -0.08)
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.value() + int(speed))
