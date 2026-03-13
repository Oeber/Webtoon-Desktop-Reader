import threading
import urllib.request

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
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

logger = get_logger(__name__)
DISCOVERY_CARD_SPACING = 16

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

    def load(self, widget, url: str, headers: dict[str, str] | None):
        def worker():
            try:
                request = urllib.request.Request(url, headers=headers or {})
                with urllib.request.urlopen(request, timeout=20) as response:
                    data = response.read()
                self.loaded.emit(widget, data, "")
            except Exception as e:
                self.loaded.emit(widget, None, str(e))

        threading.Thread(target=worker, daemon=True).start()


class DiscoveryCatalogLoader(QObject):

    loaded = Signal(int, str, int, bool, object, str)

    def load(self, request_id: int, provider, page: int, reset: bool):
        provider_key = getattr(provider, "site_name", "") or ""

        def worker():
            try:
                result = provider.get_catalog_page(page=page)
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
        self._load_cover()

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

    def _load_cover(self):
        if not self.entry.cover_url or self._cover_loader is None:
            return
        self._cover_loader.load(self, self.entry.cover_url, self.entry.cover_headers or {})

    def apply_cover_data(self, data):
        if not data:
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            logger.warning("Discovery cover decode failed for %s", self.entry.title)
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
        self._library_snapshot_dirty = True
        self._library_snapshot_path = ""
        self._loaded_entries = []
        self._entry_widgets = []
        self._entry_keys = set()

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

        self.downloaded_only_btn = QPushButton("Downloaded Only")
        self.downloaded_only_btn.setCheckable(True)
        self.downloaded_only_btn.setStyleSheet(DISCOVERY_FILTER_BUTTON_STYLE)
        self.downloaded_only_btn.toggled.connect(self._on_downloaded_only_toggled)
        controls.addWidget(self.downloaded_only_btn)

        self.load_more_btn = QPushButton("Load More")
        self.load_more_btn.setStyleSheet(BUTTON_STYLE)
        self.load_more_btn.clicked.connect(self.load_more_catalog)
        controls.addWidget(self.load_more_btn)

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

        self.content = QWidget()
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
        self.status_label.setText(
            f"Loading {self._display_name(provider.site_name)} page {self._current_page}..."
        )
        logger.info("Loading catalog page %d for %s", self._current_page, provider.site_name)
        self._ensure_library_snapshot()
        self._catalog_loading = True
        self._set_controls_enabled(False)
        self._catalog_request_id += 1
        self._catalog_loader.load(self._catalog_request_id, provider, self._current_page, reset)

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
            return
        self._current_page += 1
        self.refresh_catalog(reset=False)

    def _sync_paging(self):
        self.page_label.setText(f"Page {self._current_page}")
        self.load_more_btn.setEnabled(self._has_next_page and not self._catalog_loading)

    def _set_entries(self, entries):
        self._loaded_entries = list(entries or [])
        self._entry_keys = {self._entry_key(entry) for entry in self._loaded_entries}
        self._render_entries()

    def _render_entries(self):
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._entry_widgets = []

        visible_entries = self._visible_entries()
        if not visible_entries:
            empty = QLabel("No series found for this page.")
            if self.downloaded_only_btn.isChecked():
                empty.setText("No downloaded titles match the loaded discovery results.")
            empty.setAlignment(Qt.AlignCenter)
            empty.setMinimumHeight(120)
            empty.setStyleSheet(EMPTY_STATE_LABEL_STYLE)
            self.content_layout.addWidget(empty, 0, 0)
            return

        for entry in visible_entries:
            local_info = self._local_info_for_entry(entry)
            self._entry_widgets.append(
                DiscoveryEntryWidget(
                    entry,
                    on_download=self._start_download_for_entry,
                    cover_loader=self._cover_loader,
                    local_info=local_info,
                )
            )
        self._relayout_entries()

    def _append_entries(self, entries):
        for entry in entries:
            entry_key = self._entry_key(entry)
            if entry_key in self._entry_keys:
                continue
            self._entry_keys.add(entry_key)
            self._loaded_entries.append(entry)
        self._render_entries()

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
            return

        title_snapshot = {}
        source_snapshot = {}
        for webtoon in webtoons:
            info = {
                "name": webtoon.name,
                "chapters": len(getattr(webtoon, "chapters", []) or []),
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
            if source_site and source_series_id:
                source_snapshot[(str(source_site).strip(), str(source_series_id).strip())] = info
        self._library_titles = title_snapshot
        self._library_sources = source_snapshot
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
        super().resizeEvent(event)
        self._relayout_entries()

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
            self._sync_paging()
            return

        self._has_next_page = bool(getattr(page, "has_next_page", False))
        entries = getattr(page, "entries", []) or []
        if reset:
            self._set_entries(entries)
        else:
            self._append_entries(entries)
        self.status_label.setText(
            f"{len(self._entry_widgets)} series loaded from {self._display_name(provider_key)}"
        )
        self._sync_paging()

    def _set_controls_enabled(self, enabled: bool):
        self.site_combo.setEnabled(enabled)
        self.load_more_btn.setEnabled(enabled and self._has_next_page)
        self.refresh_btn.setEnabled(enabled)

    def _on_library_changed(self, _name: str):
        self.invalidate_library_snapshot()

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
        if not self.downloaded_only_btn.isChecked():
            return list(self._loaded_entries)
        return [entry for entry in self._loaded_entries if self._local_info_for_entry(entry)]

    def _on_downloaded_only_toggled(self, _checked: bool):
        self._render_entries()
