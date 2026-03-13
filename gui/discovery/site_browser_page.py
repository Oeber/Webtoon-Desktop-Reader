import threading
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
from gui.discovery.cover_loader import DiscoveryCoverLoader
from gui.library.webtoon_card import CARD_HEIGHT, CARD_RADIUS, CARD_WIDTH
from library.library_manager import scan_library
from gui.settings.settings_page import load_library_path
from scrapers.base import ScraperError
from scrapers.discovery_registry import get_all_discovery_providers
from scrapers.discovery_support import build_discovery_library_snapshot
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


class DiscoveryCatalogLoader(QObject):

    loaded = Signal(int, str, int, bool, object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="discovery-catalog",
        )

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

        self._executor.submit(worker)


class DiscoveryEntryWidget(QFrame):

    def __init__(self, entry, on_open_detail, cover_loader: DiscoveryCoverLoader, local_info: dict | None):
        super().__init__()
        self.entry = entry
        self._on_open_detail = on_open_detail
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
        if not self.entry.cover_url:
            self._apply_local_cover_fallback()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._on_open_detail(self.entry)
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
            self._apply_local_cover_fallback()
            return
        self._cover_requested = True
        self._cover_loader.load(self, self.entry.cover_url, self.entry.cover_headers or {})

    def apply_cover_data(self, data):
        if not data:
            self._cover_requested = False
            self._apply_local_cover_fallback()
            return
        pixmap = self._decode_cover_pixmap(data)
        if pixmap.isNull():
            self._cover_requested = False
            self._apply_local_cover_fallback()
            return
        self._apply_cover_pixmap(pixmap)

    def _apply_cover_pixmap(self, pixmap: QPixmap) -> bool:
        if pixmap.isNull():
            return False
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
        return True

    def _apply_local_cover_fallback(self) -> bool:
        local_webtoon = self._local_info.get("webtoon") if isinstance(self._local_info, dict) else None
        local_path = getattr(local_webtoon, "thumbnail", "") or ""
        if not local_path:
            return False
        pixmap = QPixmap(local_path)
        if pixmap.isNull():
            return False
        return self._apply_cover_pixmap(pixmap)

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
        parts.append("Click to open details")
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
    COVER_PRELOAD_MARGIN_PX = 120
    COVER_REQUEST_BATCH_SIZE = 12
    COVER_REQUEST_DEBOUNCE_MS = 16

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
        self._inflight_catalog_signature = None
        self._last_catalog_signature = None
        self._pending_catalog_request = None
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
        self._entry_columns = 1
        self._search_text = ""
        self._last_cover_request_signature = None
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
        self._cover_request_timer = QTimer(self)
        self._cover_request_timer.setSingleShot(True)
        self._cover_request_timer.timeout.connect(self._request_visible_covers_now)
        self._auto_scroll_direction = 0
        self._auth_in_progress = False
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
        self.refresh_btn.clicked.connect(lambda: self.refresh_catalog(reset=True, force=True))
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

    def refresh_catalog(self, reset: bool = False, force: bool = False, page_override: int | None = None):
        provider = self._current_provider()
        if provider is None:
            self._show_message("No discovery providers are available yet.", is_error=False)
            self._set_entries([])
            self._sync_paging()
            return

        search_query = self._remote_search_query()
        requested_page = max(1, int(page_override if page_override is not None else (1 if reset else self._current_page)))
        signature = (provider.site_name, requested_page, search_query)

        if self._catalog_loading:
            if self._inflight_catalog_signature == signature and not force:
                logger.info(
                    "Ignoring duplicate discovery refresh for %s page=%d search=%r while request is in flight",
                    provider.site_name,
                    requested_page,
                    search_query,
                )
                return
            self._pending_catalog_request = {
                "reset": reset,
                "force": force,
                "page": requested_page,
                "signature": signature,
            }
            logger.info(
                "Queued discovery refresh for %s page=%d search=%r force=%s",
                provider.site_name,
                requested_page,
                search_query,
                force,
            )
            return

        if not force and self._last_catalog_signature == signature:
            logger.info(
                "Skipping no-op discovery refresh for %s page=%d search=%r",
                provider.site_name,
                requested_page,
                search_query,
            )
            self._sync_paging()
            return

        self._show_message("", is_error=False)
        loading_label = f"Loading {provider.get_display_name()} page {requested_page}"
        if search_query:
            loading_label += f" for '{search_query}'"
        self.status_label.setText(loading_label + "...")
        logger.info("Loading catalog page %d for %s", requested_page, provider.site_name)
        self._ensure_library_snapshot()
        self._catalog_loading = True
        self._inflight_catalog_signature = signature
        self._current_page = requested_page
        self._set_controls_enabled(False)
        self._set_loading_more_visible(not reset and not self.downloaded_only_btn.isChecked() and not search_query)
        self._catalog_request_id += 1
        self._catalog_loader.load(self._catalog_request_id, provider, requested_page, reset, search_query)

    def _reload_scrapers(self, load_catalog: bool = True):
        current_key = self.site_combo.currentData()
        providers = get_all_discovery_providers()
        providers.sort(key=lambda provider: provider.get_display_name().casefold())
        self._providers_by_key = {provider.site_name: provider for provider in providers}

        self.site_combo.blockSignals(True)
        self.site_combo.clear()
        for provider in providers:
            self.site_combo.addItem(provider.get_display_name(), provider.site_name)
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

    def apply_command_search(self, query: str = "", scraper: str = "") -> bool:
        self._auto_load_timer.stop()
        self._search_timer.stop()

        normalized_query = " ".join(str(query or "").split()).strip()
        provider_key = self.resolve_provider_key(scraper)
        if scraper and not provider_key:
            logger.info("Discovery command could not resolve provider %r", scraper)
            return False

        if self.site_combo.count() == 0:
            self._reload_scrapers(load_catalog=False)
            if self.site_combo.count() == 0:
                return False

        if provider_key:
            index = self.site_combo.findData(provider_key)
            if index >= 0 and index != self.site_combo.currentIndex():
                self.site_combo.blockSignals(True)
                self.site_combo.setCurrentIndex(index)
                self.site_combo.blockSignals(False)

        if self.downloaded_only_btn.isChecked():
            self.downloaded_only_btn.blockSignals(True)
            self.downloaded_only_btn.setChecked(False)
            self.downloaded_only_btn.blockSignals(False)

        if self.search_input.text() != normalized_query:
            self.search_input.blockSignals(True)
            self.search_input.setText(normalized_query)
            self.search_input.blockSignals(False)
        self._search_text = normalized_query
        self.refresh_catalog(reset=True)
        return True

    def resolve_provider_key(self, text: str) -> str | None:
        normalized = self._normalize_provider_text(text)
        if not normalized:
            return None

        exact_matches = []
        prefix_matches = []
        contains_matches = []
        for provider in self._providers_by_key.values():
            provider_keys = self._provider_search_keys(provider)
            if normalized in provider_keys:
                exact_matches.append(provider.site_name)
                continue
            if any(key.startswith(normalized) for key in provider_keys):
                prefix_matches.append(provider.site_name)
                continue
            if any(normalized in key for key in provider_keys):
                contains_matches.append(provider.site_name)

        for matches in (exact_matches, prefix_matches, contains_matches):
            if len(matches) == 1:
                return matches[0]
        return None

    def available_provider_labels(self) -> list[str]:
        providers = sorted(
            self._providers_by_key.values(),
            key=lambda provider: provider.get_display_name().casefold(),
        )
        return [provider.get_display_name() for provider in providers]

    def matching_provider_labels(self, text: str = "") -> list[str]:
        normalized = self._normalize_provider_text(text)
        providers = sorted(
            self._providers_by_key.values(),
            key=lambda provider: provider.get_display_name().casefold(),
        )
        if not normalized:
            return [provider.get_display_name() for provider in providers]

        prefix_matches = []
        contains_matches = []
        for provider in providers:
            provider_keys = self._provider_search_keys(provider)
            label = provider.get_display_name()
            if any(key.startswith(normalized) for key in provider_keys):
                prefix_matches.append(label)
            elif any(normalized in key for key in provider_keys):
                contains_matches.append(label)
        return prefix_matches or contains_matches

    def _provider_search_keys(self, provider) -> set[str]:
        names = {
            getattr(provider, "site_name", "") or "",
            provider.get_display_name(),
        }
        normalized = set()
        for name in names:
            key = self._normalize_provider_text(name)
            if key:
                normalized.add(key)
        return normalized

    def _normalize_provider_text(self, text: str) -> str:
        return "".join(ch for ch in str(text or "").casefold() if ch.isalnum())

    def _current_provider(self):
        key = self.site_combo.currentData()
        if not key:
            return None
        return self._providers_by_key.get(key)

    def _authorize_current_site(self):
        if self._auth_in_progress:
            return False
        self._auth_in_progress = True
        try:
            return bool(self.main_window.open_site_authorization("hiper_cool", url="https://hiper.cool/"))
        finally:
            self._auth_in_progress = False

    def _on_site_changed(self, _index: int):
        if self._catalog_loading:
            return
        self.scroll.verticalScrollBar().setValue(0)
        self._pending_append_anchor_bottom = False
        if self.downloaded_only_btn.isChecked():
            self._pending_catalog_request = None
            self._last_catalog_signature = None
            self._ensure_library_snapshot()
            self._render_entries()
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
        next_page = self._current_page + 1
        self.refresh_catalog(reset=False, page_override=next_page)

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
        self._last_cover_request_signature = None
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
        self._entry_columns = 1
        self._empty_state_label = None
        if self._loading_more_label is not None:
            self._loading_more_label.deleteLater()
            self._loading_more_label = None

    def _show_empty_state(self):
        empty = QLabel("No series found for this page.")
        if self._search_text.strip():
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
                on_open_detail=self._open_entry_detail,
                cover_loader=self._cover_loader,
                local_info=local_info,
            )
            widget.setMouseTracking(True)
            widget.installEventFilter(self)
            self._entry_widgets.append(widget)

    def _open_entry_detail(self, entry):
        if not getattr(entry, "url", ""):
            self._show_message("This entry does not expose a downloadable series URL.", is_error=True)
            return
        self.main_window.open_discovery_detail(entry)

    def _show_message(self, text: str, is_error: bool):
        self.error_label.setVisible(is_error and bool(text))
        self.error_label.setText(text if is_error else "")
        self.status_label.setText("" if is_error else text)

    def _refresh_library_snapshot(self):
        try:
            webtoons = scan_library(load_library_path(), self.main_window.settings_store)
        except Exception as e:
            logger.warning("Failed to refresh discovery library snapshot", exc_info=e)
            self._library_titles = {}
            self._library_sources = {}
            self._library_entries_by_site = {}
            return

        snapshot = build_discovery_library_snapshot(webtoons, self.main_window.settings_store)
        self._library_titles = snapshot.title_matches
        self._library_sources = snapshot.source_matches
        self._library_entries_by_site = snapshot.entries_by_site
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
        self._entry_columns = columns

        for index, widget in enumerate(self._entry_widgets):
            row = index // columns
            column = index % columns
            self.content_layout.addWidget(widget, row, column, Qt.AlignTop | Qt.AlignLeft)

        if self._loading_more_label is not None:
            loading_row = len(self._entry_widgets) // columns
            if self._entry_widgets and len(self._entry_widgets) % columns != 0:
                loading_row += 1
            self.content_layout.addWidget(self._loading_more_label, loading_row, 0, 1, columns, Qt.AlignTop)

        self._queue_visible_cover_request(force=True)

    def _queue_visible_cover_request(self, *, force: bool = False):
        if force:
            self._last_cover_request_signature = None
        if self._cover_request_timer.isActive():
            return
        self._cover_request_timer.start(self.COVER_REQUEST_DEBOUNCE_MS)

    def _request_visible_covers(self):
        self._queue_visible_cover_request()

    def _request_visible_covers_now(self):
        if not self._entry_widgets:
            return
        viewport = self.scroll.viewport()
        scrollbar = self.scroll.verticalScrollBar()
        content_top = max(0, scrollbar.value() - self.COVER_PRELOAD_MARGIN_PX)
        content_bottom = scrollbar.value() + viewport.height() + self.COVER_PRELOAD_MARGIN_PX
        viewport_rect = viewport.rect().adjusted(
            0,
            -self.COVER_PRELOAD_MARGIN_PX,
            0,
            self.COVER_PRELOAD_MARGIN_PX,
        )
        signature = (
            content_top,
            content_bottom,
            viewport.width(),
            self._entry_columns,
            len(self._entry_widgets),
        )
        if signature == self._last_cover_request_signature:
            return
        self._last_cover_request_signature = signature

        columns = max(1, self._entry_columns)
        widget_height = self._entry_widgets[0].height() + DISCOVERY_CARD_SPACING
        first_row = max(0, content_top // max(1, widget_height) - 1)
        last_row = max(first_row, content_bottom // max(1, widget_height) + 1)
        start_index = min(len(self._entry_widgets), first_row * columns)
        end_index = min(len(self._entry_widgets), (last_row + 1) * columns)

        visible_widgets = []
        viewport_center_y = viewport.rect().center().y()
        for widget in self._entry_widgets[start_index:end_index]:
            top_left = widget.mapTo(viewport, widget.rect().topLeft())
            widget_rect = widget.rect().translated(top_left)
            if widget_rect.intersects(viewport_rect):
                distance = abs(widget_rect.center().y() - viewport_center_y)
                visible_widgets.append((distance, widget))
        visible_widgets.sort(key=lambda item: item[0])
        for _distance, widget in visible_widgets[:self.COVER_REQUEST_BATCH_SIZE]:
            widget.ensure_cover_requested()

    def _on_cover_loaded(self, widget, data, error: str):
        if widget not in self._entry_widgets:
            return
        if error:
            logger.warning("Discovery cover request failed for %s: %s", widget.entry.title, error)
            self._queue_visible_cover_request(force=True)
            return
        widget.apply_cover_data(data)
        self._queue_visible_cover_request(force=True)

    def _on_catalog_loaded(self, request_id: int, provider_key: str, page_number: int, reset: bool, page, error: str):
        if request_id != self._catalog_request_id:
            logger.info("Ignoring stale discovery catalog response for %s page %d", provider_key, page_number)
            return

        completed_signature = self._inflight_catalog_signature
        self._catalog_loading = False
        self._inflight_catalog_signature = None
        self._set_controls_enabled(True)

        current_provider = self._current_provider()
        current_provider_key = getattr(current_provider, "site_name", "") if current_provider is not None else ""
        if provider_key != current_provider_key or page_number != self._current_page:
            logger.info("Ignoring mismatched discovery catalog response for %s page %d", provider_key, page_number)
            return

        if error:
            if self._looks_like_expected_provider_block(error):
                logger.info("Catalog access blocked for %s page %d: %s", provider_key, page_number, error)
                if provider_key == "hiper_cool" and self._authorize_current_site():
                    self.refresh_catalog(reset=True, force=True)
                    return
            else:
                logger.warning("Catalog loading failed for %s page %d: %s", provider_key, page_number, error)
            self._show_message(error, is_error=True)
            if reset:
                self._set_entries([])
                self._has_next_page = False
            self._set_loading_more_visible(False)
            self._sync_paging()
            self._run_pending_catalog_request(completed_signature)
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
        self._last_catalog_signature = completed_signature
        visible_count = len(self._visible_entries())
        self.status_label.setText(
            f"{visible_count} series loaded from {self._provider_display_name(provider_key)}"
        )
        self._sync_paging()
        if not self.downloaded_only_btn.isChecked() and not self._remote_search_query():
            self._schedule_auto_load_more(force_fill=True)
        self._run_pending_catalog_request(completed_signature)

    def _run_pending_catalog_request(self, completed_signature=None):
        pending = self._pending_catalog_request
        self._pending_catalog_request = None
        if not pending:
            return
        signature = pending.get("signature")
        if signature == completed_signature and not pending.get("force"):
            logger.info(
                "Dropping queued duplicate discovery refresh for %s page=%d search=%r",
                signature[0],
                signature[1],
                signature[2],
            )
            return
        self.refresh_catalog(
            reset=bool(pending.get("reset", False)),
            force=bool(pending.get("force", False)),
            page_override=pending.get("page"),
        )

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

    def _provider_for_site(self, site_name: str | None):
        if not site_name:
            return None
        return self._providers_by_key.get(str(site_name))

    def _provider_for_entry(self, entry):
        provider = self._provider_for_site(getattr(entry, "site", ""))
        if provider is not None:
            return provider
        return self._current_provider()

    def _provider_display_name(self, site_name: str | None) -> str:
        provider = self._provider_for_site(site_name)
        if provider is not None:
            return provider.get_display_name()
        return str(site_name or "Unknown")

    def _looks_like_expected_provider_block(self, error: str) -> bool:
        text = " ".join(str(error or "").casefold().split())
        return "cloudflare" in text or "anti-bot" in text

    def _local_info_for_entry(self, entry) -> dict | None:
        provider = self._provider_for_entry(entry)
        if provider is None:
            return None
        return provider.match_entry_to_library(entry, self._library_sources, self._library_titles)

    def _entry_key(self, entry) -> str:
        provider = self._provider_for_entry(entry)
        if provider is None:
            return entry.identity_key()
        return provider.entry_key(entry)

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
        if provider is None:
            return []
        return provider.downloaded_entries(self._library_entries_by_site)

    def _remote_search_query(self) -> str:
        if self.downloaded_only_btn.isChecked():
            return ""
        return " ".join(str(self._search_text or "").split()).strip()

    def _entry_matches_search(self, entry) -> bool:
        provider = self._provider_for_entry(entry)
        if provider is None:
            return entry.matches_query(self._search_text)
        return provider.matches_search(entry, self._search_text)

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
        self.scroll.verticalScrollBar().setValue(0)
        self._pending_append_anchor_bottom = False
        self._pending_catalog_request = None
        self._last_catalog_signature = None
        if checked:
            self._ensure_library_snapshot()
            self._render_entries()
            return
        self.refresh_catalog(reset=True, force=True)

    def _restore_scroll_position(self, old_value: int, anchor_to_bottom: bool):
        def restore():
            scrollbar = self.scroll.verticalScrollBar()
            if anchor_to_bottom:
                scrollbar.setValue(scrollbar.maximum())
            else:
                scrollbar.setValue(min(old_value, scrollbar.maximum()))
            self._queue_visible_cover_request(force=True)

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



