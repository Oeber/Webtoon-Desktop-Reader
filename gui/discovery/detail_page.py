import threading

import qtawesome as qta
from PySide6.QtCore import QObject, Qt, QSize, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.app_logging import get_logger
from gui.common.chapter_selection import (
    apply_select_icon,
    refresh_selector_visibility,
    selector_buttons,
    set_selector_visibility,
    sync_selector_checked_state,
)
from gui.common.chapter_utils import SPECIAL_CHAPTER_RE
from gui.common.styles import (
    sized_button_style,
    BATCH_BAR_STYLE,
    BATCH_LABEL_STYLE,
    CHAPTER_LIST_WIDGET_STYLE,
    CHAPTER_ROW_STYLE,
    CHAPTER_SCROLL_AREA_STYLE,
    CHAPTER_SELECT_SLOT_STYLE,
    CHAPTER_TOOL_BUTTON_STYLE,
    DETAIL_TITLE_STYLE,
    HERO_PANEL_STYLE,
    PAGE_BG_STYLE,
    PRIMARY_ACTION_BUTTON_STYLE,
    SECONDARY_ACTION_BUTTON_STYLE,
    SECTION_CAPTION_STYLE,
    SECTION_HEADER_PANEL_STYLE,
    SUBTLE_META_LABEL_STYLE,
    TOOLBAR_TEXT_BUTTON_STYLE,
    TOP_BAR_STYLE,
    TRANSPARENT_BG_STYLE,
    WARNING_META_LABEL_STYLE,
    chapter_name_style,
    detail_thumb_style,
)
from gui.common.detail_shared import ACTION_BTN_H, ACTION_BTN_W, BATCH_ACTION_BTN_H, RADIUS, THUMB_H, THUMB_W
from gui.discovery.cover_loader import DiscoveryCoverLoader
from gui.library.detail_page import MangaPageTile
from scrapers.base import ScraperError
from scrapers.discovery_registry import get_all_discovery_providers
from scrapers.models import CatalogSeries
from scrapers.registry import get_scraper

logger = get_logger(__name__)


class DiscoverySeriesLoader(QObject):
    loaded = Signal(int, object, str)

    def load(self, request_id: int, entry):
        def worker():
            try:
                url = getattr(entry, "url", "") or ""
                if not url:
                    raise ScraperError("This entry does not expose a downloadable series URL.")
                scraper = get_scraper(url)
                series_url = url if not scraper.is_chapter_url(url) else scraper.series_url_from_chapter_url(url)
                series = scraper.get_series_info(series_url)
                if str(getattr(series, "content_type", "webtoon") or "webtoon").strip().casefold() == "manga":
                    for chapter in getattr(series, "chapters", []) or []:
                        try:
                            chapter.pages = scraper.get_chapter_pages(chapter.url)
                        except Exception as exc:
                            logger.warning("Discovery manga page load failed for %s", chapter.url, exc_info=exc)
                            chapter.pages = []
                self.loaded.emit(request_id, series, "")
            except ScraperError as e:
                self.loaded.emit(request_id, None, str(e))
            except Exception as e:
                logger.exception("Unexpected discovery detail loading failure")
                self.loaded.emit(request_id, None, str(e))

        threading.Thread(target=worker, daemon=True).start()


class DiscoveryDetailPage(QWidget):
    MANGA_PAGE_RENDER_BATCH_SIZE = 24
    MANGA_PAGE_COLUMNS = 6

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.entry = None
        self.series = None
        self._selected_urls = set()
        self._request_id = 0
        self._series_loader = DiscoverySeriesLoader(self)
        self._series_loader.loaded.connect(self._on_series_loaded)
        self._cover_loader = DiscoveryCoverLoader(self)
        self._cover_loader.loaded.connect(self._on_cover_loaded)
        self._page_preview_loader = DiscoveryCoverLoader(self)
        self._page_preview_loader.loaded.connect(self._on_page_preview_loaded)
        self._providers_by_site = {provider.site_name: provider for provider in get_all_discovery_providers()}
        self._pending_manga_page_entries = []
        self._manga_page_grid = None
        self._manga_page_render_token = 0
        self._manga_page_render_timer = QTimer(self)
        self._manga_page_render_timer.setSingleShot(True)
        self._manga_page_render_timer.timeout.connect(self._render_next_manga_page_batch)

        self.setStyleSheet(PAGE_BG_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        top_bar = QWidget()
        top_bar.setFixedHeight(52)
        top_bar.setStyleSheet(TOP_BAR_STYLE)
        tb_layout = QHBoxLayout(top_bar)
        tb_layout.setContentsMargins(16, 0, 16, 0)

        self.back_btn = QPushButton("  Back")
        self.back_btn.setIcon(qta.icon("fa5s.arrow-left", color="#d8b7b0"))
        self.back_btn.setIconSize(QSize(14, 14))
        self.back_btn.setCursor(Qt.PointingHandCursor)
        self.back_btn.setStyleSheet(TOOLBAR_TEXT_BUTTON_STYLE)
        self.back_btn.clicked.connect(self.main_window.open_discovery)
        tb_layout.addWidget(self.back_btn)
        tb_layout.addStretch()
        root.addWidget(top_bar)

        hero = QWidget()
        hero.setStyleSheet(HERO_PANEL_STYLE)
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(32, 28, 32, 28)
        hero_layout.setSpacing(28)

        self.thumb_label = QLabel("No Cover")
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setFixedSize(THUMB_W, THUMB_H)
        self.thumb_label.setStyleSheet(detail_thumb_style(RADIUS))
        hero_layout.addWidget(self.thumb_label)

        info_widget = QWidget()
        info_widget.setStyleSheet(TRANSPARENT_BG_STYLE)
        info_layout = QVBoxLayout(info_widget)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(10)
        info_layout.setAlignment(Qt.AlignTop)

        self.title_label = QLabel("")
        self.title_label.setStyleSheet(DETAIL_TITLE_STYLE)
        self.title_label.setFont(QFont("Segoe UI", 24, QFont.Bold))
        info_layout.addWidget(self.title_label)

        self.author_label = QLabel("")
        self.author_label.setStyleSheet(SUBTLE_META_LABEL_STYLE)
        info_layout.addWidget(self.author_label)

        self.chapter_count_label = QLabel("")
        self.chapter_count_label.setStyleSheet(SUBTLE_META_LABEL_STYLE)
        info_layout.addWidget(self.chapter_count_label)

        self.description_label = QLabel("")
        self.description_label.setWordWrap(True)
        self.description_label.setStyleSheet(SUBTLE_META_LABEL_STYLE)
        info_layout.addWidget(self.description_label)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(WARNING_META_LABEL_STYLE)
        self.status_label.hide()
        info_layout.addWidget(self.status_label)

        action_row = QHBoxLayout()
        action_row.setSpacing(10)

        self.download_all_btn = QPushButton("Download All Chapters")
        self.download_all_btn.setFixedSize(ACTION_BTN_W, ACTION_BTN_H)
        self.download_all_btn.setStyleSheet(SECONDARY_ACTION_BUTTON_STYLE)
        self.download_all_btn.clicked.connect(self._download_all)
        self.download_all_btn.setEnabled(False)
        action_row.addWidget(self.download_all_btn)

        action_row.addStretch()
        info_layout.addLayout(action_row)

        hero_layout.addWidget(info_widget, 1)
        root.addWidget(hero)

        section_header = QWidget()
        section_header.setStyleSheet(SECTION_HEADER_PANEL_STYLE)
        sh_layout = QHBoxLayout(section_header)
        sh_layout.setContentsMargins(32, 20, 32, 8)
        self.section_caption_label = QLabel("CHAPTERS")
        self.section_caption_label.setStyleSheet(SECTION_CAPTION_STYLE)
        sh_layout.addWidget(self.section_caption_label)
        sh_layout.addStretch()
        self.hide_specials_checkbox = QCheckBox("Hide filler")
        self.hide_specials_checkbox.toggled.connect(self._rebuild_chapter_list)
        sh_layout.addWidget(self.hide_specials_checkbox)
        root.addWidget(section_header)

        self.batch_bar = QWidget()
        self.batch_bar.setStyleSheet(BATCH_BAR_STYLE)
        batch_layout = QHBoxLayout(self.batch_bar)
        batch_layout.setContentsMargins(32, 10, 32, 10)
        batch_layout.setSpacing(10)
        self.selection_label = QLabel("0 selected")
        self.selection_label.setStyleSheet(BATCH_LABEL_STYLE)
        batch_layout.addWidget(self.selection_label)

        batch_primary_btn_style = sized_button_style(PRIMARY_ACTION_BUTTON_STYLE, BATCH_ACTION_BTN_H)
        batch_secondary_btn_style = sized_button_style(SECONDARY_ACTION_BUTTON_STYLE, BATCH_ACTION_BTN_H)

        self.select_all_btn = QPushButton("Select All")
        self.select_all_btn.setStyleSheet(batch_secondary_btn_style)
        self.select_all_btn.clicked.connect(self._select_all_visible)
        batch_layout.addWidget(self.select_all_btn)

        self.download_selected_btn = QPushButton("Download Selected")
        self.download_selected_btn.setStyleSheet(batch_primary_btn_style)
        self.download_selected_btn.clicked.connect(self._download_selected)
        self.download_selected_btn.setEnabled(False)
        batch_layout.addWidget(self.download_selected_btn)

        self.clear_selection_btn = QPushButton("Clear")
        self.clear_selection_btn.setStyleSheet(batch_secondary_btn_style)
        self.clear_selection_btn.clicked.connect(self._clear_selection)
        batch_layout.addWidget(self.clear_selection_btn)
        batch_layout.addStretch()
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
        root.addWidget(self.batch_bar)

    def load_entry(self, entry: CatalogSeries):
        self.entry = entry
        self.series = None
        self._selected_urls.clear()
        self._request_id += 1
        self._cancel_pending_manga_page_render()

        self.title_label.setText(getattr(entry, "title", "") or "Untitled")
        self.author_label.setText(getattr(entry, "author", "") or "Unknown author")
        self.chapter_count_label.setText(self._entry_count_text(entry))
        self.description_label.setText(getattr(entry, "description", "") or "Loading series details...")
        self.status_label.setText("Loading chapters...")
        self.status_label.show()
        self.download_selected_btn.setEnabled(False)
        self.download_all_btn.setEnabled(False)
        self.thumb_label.setPixmap(QPixmap())
        self.thumb_label.setText("No Cover")
        if getattr(entry, "cover_url", ""):
            provider = self._providers_by_site.get(str(getattr(entry, "site", "") or "").strip())
            self._cover_loader.load(self, entry.cover_url, getattr(entry, "cover_headers", {}) or {}, provider)
        self._refresh_mode_state()
        self._rebuild_chapter_list()
        self._series_loader.load(self._request_id, entry)

    def _on_cover_loaded(self, widget, data, error: str):
        if widget is not self or error or not data:
            return
        pixmap = self._rounded_pixmap_from_bytes(data, THUMB_W, THUMB_H, RADIUS)
        if pixmap.isNull():
            return
        self.thumb_label.setPixmap(pixmap)
        self.thumb_label.setText("")

    def _on_page_preview_loaded(self, widget, data, error: str):
        if not isinstance(widget, MangaPageTile) or error or not data:
            return
        pixmap = self._rounded_pixmap_from_bytes(
            data,
            widget.PREVIEW_WIDTH,
            widget.PREVIEW_HEIGHT,
            12,
            fill_color="#1d1514",
            border_color="#3c2522",
        )
        if pixmap.isNull():
            return
        widget.set_preview_pixmap(pixmap)

    def _rounded_pixmap_from_bytes(
        self,
        data: bytes,
        width: int,
        height: int,
        radius: int,
        *,
        fill_color: str | None = None,
        border_color: str | None = None,
    ) -> QPixmap:
        source = QPixmap()
        if not source.loadFromData(data):
            return QPixmap()
        scaled = source.scaled(width, height, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x = max(0, (scaled.width() - width) // 2)
        y = max(0, (scaled.height() - height) // 2)
        cropped = scaled.copy(x, y, width, height)
        rounded = QPixmap(width, height)
        rounded.fill(Qt.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, width, height, radius, radius)
        if fill_color:
            painter.fillPath(path, QColor(fill_color))
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, cropped)
        painter.setClipping(False)
        if border_color:
            painter.setPen(QPen(QColor(border_color), 1))
            painter.drawPath(path)
        painter.end()
        return rounded
    def _on_series_loaded(self, request_id: int, series, error: str):
        if request_id != self._request_id:
            return
        if error:
            site_name = str(getattr(self.entry, "site", "") or "").strip()
            auth_url = getattr(self.entry, "url", "") or ""
            if self._looks_like_access_block(error) and site_name and self.main_window.open_site_authorization(site_name, url=auth_url):
                self._series_loader.load(self._request_id, self.entry)
                return
            self.series = None
            self.status_label.setText(error)
            self.status_label.show()
            self._rebuild_chapter_list()
            return

        self.series = series
        self.title_label.setText(getattr(series, "title", "") or self.title_label.text())
        self.author_label.setText(getattr(series, "author", None) or self.author_label.text())
        self.chapter_count_label.setText(self._series_count_text(series))
        self.description_label.setText(self._merged_description(series))
        self.status_label.hide()
        self.download_all_btn.setEnabled(True)
        self._refresh_mode_state()
        self._rebuild_chapter_list()

    def _visible_chapters(self):
        chapters = list(getattr(self.series, "chapters", []) or [])
        if self.hide_specials_checkbox.isChecked():
            chapters = [chapter for chapter in chapters if not SPECIAL_CHAPTER_RE.search(chapter.title or "")]
        return chapters

    def _is_manga_series(self) -> bool:
        return str(getattr(self.series, "content_type", "webtoon") or "webtoon").strip().casefold() == "manga"

    def _visible_manga_pages(self):
        pages = []
        for chapter in self._visible_chapters():
            for page in list(getattr(chapter, "pages", []) or []):
                pages.append((chapter, page))
        return pages

    def _refresh_mode_state(self) -> None:
        manga = self._is_manga_series() if self.series is not None else False
        self.section_caption_label.setText("PAGES" if manga else "CHAPTERS")
        self.hide_specials_checkbox.setVisible(not manga)
        self.batch_bar.setVisible((not manga) and bool(self._selected_urls))
        self.download_all_btn.setText("Download All Pages" if manga else "Download All Chapters")

    def _rebuild_chapter_list(self):
        self._cancel_pending_manga_page_render()
        while self.chapter_list_layout.count():
            item = self.chapter_list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if self.series is None:
            label = QLabel(self.status_label.text() or "Loading chapter list...")
            label.setStyleSheet(SUBTLE_META_LABEL_STYLE)
            self.chapter_list_layout.addWidget(label)
            self._sync_selection_state()
            return

        valid_urls = {chapter.url for chapter in getattr(self.series, "chapters", []) or []}
        self._selected_urls &= valid_urls
        if self._is_manga_series():
            page_entries = self._visible_manga_pages()
            if not page_entries:
                label = QLabel("No pages available.")
                label.setStyleSheet(SUBTLE_META_LABEL_STYLE)
                self.chapter_list_layout.addWidget(label)
                self._sync_selection_state()
                return
            self._selected_urls.clear()
            page_grid_host = QWidget()
            page_grid = QGridLayout(page_grid_host)
            page_grid.setContentsMargins(0, 8, 0, 8)
            page_grid.setHorizontalSpacing(12)
            page_grid.setVerticalSpacing(12)
            self._manga_page_grid = page_grid
            self._pending_manga_page_entries = list(page_entries)
            self._manga_page_render_token += 1
            self.chapter_list_layout.addWidget(page_grid_host)
            self._manga_page_render_timer.start(0)
            self._sync_selection_state()
            return

        chapters = self._visible_chapters()
        if not chapters:
            label = QLabel("No chapters available.")
            label.setStyleSheet(SUBTLE_META_LABEL_STYLE)
            self.chapter_list_layout.addWidget(label)
            self._sync_selection_state()
            return

        for chapter in chapters:
            self.chapter_list_layout.addWidget(self._make_chapter_row(chapter))
        self._sync_selection_state()

    def _make_manga_page_tile(self, chapter, page):
        page_number = max(1, int(getattr(page, "index", 0) or 0))
        image_url = str(getattr(page, "image_url", "") or "")
        tile = MangaPageTile(image_url, page_number, self.chapter_list_widget)
        tile.setCursor(Qt.ArrowCursor)
        tile.setToolTip(f"{chapter.title or chapter.url}\nPage {page_number}")
        if image_url:
            headers = self._page_preview_headers(image_url)
            provider = self._page_preview_provider(chapter)
            self._page_preview_loader.load(tile, image_url, headers, provider)
        return tile

    def _page_preview_headers(self, image_url: str) -> dict[str, str]:
        if self.series is not None:
            try:
                scraper = get_scraper(getattr(self.series, "url", "") or getattr(self.entry, "url", ""))
                request_headers = getattr(scraper, "get_request_headers", None)
                if callable(request_headers):
                    return dict(request_headers(image_url) or {})
            except Exception:
                logger.debug("Discovery page preview header build failed for %s", image_url, exc_info=True)
        return {}

    def _page_preview_provider(self, chapter):
        try:
            return get_scraper(chapter.url)
        except Exception:
            return None

    def _cancel_pending_manga_page_render(self) -> None:
        self._manga_page_render_timer.stop()
        self._pending_manga_page_entries = []
        self._manga_page_grid = None
        self._manga_page_render_token += 1

    def _render_next_manga_page_batch(self) -> None:
        grid = self._manga_page_grid
        if grid is None or not self._pending_manga_page_entries:
            return

        columns = self.MANGA_PAGE_COLUMNS
        start_index = grid.count()
        batch = self._pending_manga_page_entries[:self.MANGA_PAGE_RENDER_BATCH_SIZE]
        self._pending_manga_page_entries = self._pending_manga_page_entries[self.MANGA_PAGE_RENDER_BATCH_SIZE:]

        for offset, (chapter, page) in enumerate(batch):
            index = start_index + offset
            tile = self._make_manga_page_tile(chapter, page)
            grid.addWidget(tile, index // columns, index % columns, Qt.AlignTop)

        if self._pending_manga_page_entries:
            self._manga_page_render_timer.start(0)

    def _make_chapter_row(self, chapter):
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

        select_btn = QToolButton()
        select_btn.setCursor(Qt.PointingHandCursor)
        select_btn.setAutoRaise(True)
        select_btn.setCheckable(True)
        select_btn.setChecked(chapter.url in self._selected_urls)
        select_btn.setIconSize(QSize(14, 14))
        select_btn.setStyleSheet(CHAPTER_TOOL_BUTTON_STYLE)
        select_btn.setProperty("chapter_url", chapter.url)
        apply_select_icon(select_btn, select_btn.isChecked())
        set_selector_visibility(row, select_btn, force=bool(self._selected_urls), hide_when_inactive=True)
        select_btn.clicked.connect(
            lambda checked, url=chapter.url, btn=select_btn: self._toggle_chapter(url, checked, btn)
        )
        select_slot_layout.addWidget(select_btn, 0, Qt.AlignCenter)
        layout.addWidget(select_slot)

        title = QLabel(chapter.title or chapter.url)
        title.setStyleSheet(chapter_name_style("#ffd7cf"))
        layout.addWidget(title, 1)

        single_btn = QToolButton()
        single_btn.setText("Download")
        single_btn.setCursor(Qt.PointingHandCursor)
        single_btn.setStyleSheet(CHAPTER_TOOL_BUTTON_STYLE)
        single_btn.clicked.connect(lambda checked=False, url=chapter.url: self._download_selected_urls([url]))
        layout.addWidget(single_btn)

        row.enterEvent = lambda event, btn=select_btn, widget=row: self._on_chapter_row_hover(widget, btn, True, event)
        row.leaveEvent = lambda event, btn=select_btn, widget=row: self._on_chapter_row_hover(widget, btn, False, event)
        return row

    def _on_chapter_row_hover(self, row: QWidget, button: QToolButton, hovered: bool, event):
        set_selector_visibility(
            row,
            button,
            force=bool(self._selected_urls) or hovered,
            hide_when_inactive=True,
        )
        QWidget.enterEvent(row, event) if hovered else QWidget.leaveEvent(row, event)

    def _refresh_visible_selectors(self):
        refresh_selector_visibility(
            self.chapter_list_widget,
            "chapter_url",
            force=bool(self._selected_urls),
            hide_when_inactive=True,
        )

    def _toggle_chapter(self, url: str, checked: bool, button: QToolButton | None = None):
        if checked:
            self._selected_urls.add(url)
        else:
            self._selected_urls.discard(url)
        if button is not None:
            button.blockSignals(True)
            button.setChecked(checked)
            button.blockSignals(False)
            apply_select_icon(button, checked)
        self._refresh_visible_selectors()
        self._sync_selection_state()

    def _select_all_visible(self):
        visible_urls = {chapter.url for chapter in self._visible_chapters()}
        self._selected_urls |= visible_urls
        sync_selector_checked_state(
            self.chapter_list_widget,
            "chapter_url",
            visible_urls | self._selected_urls,
            force=True,
            hide_when_inactive=True,
        )
        self._sync_selection_state()

    def _clear_selection(self):
        self._selected_urls.clear()
        sync_selector_checked_state(
            self.chapter_list_widget,
            "chapter_url",
            self._selected_urls,
            force=False,
            hide_when_inactive=True,
        )
        self._sync_selection_state()

    def _sync_selection_state(self):
        selected = len(self._selected_urls)
        self.selection_label.setText(f"{selected} selected")
        manga = self._is_manga_series() if self.series is not None else False
        self.batch_bar.setVisible((not manga) and selected > 0)
        self.download_selected_btn.setEnabled((self.series is not None) and (not manga) and selected > 0)

    def _download_all(self):
        if self.series is None:
            return
        error = self.main_window.downloader.start_download_from_url(
            getattr(self.series, "url", "") or getattr(self.entry, "url", ""),
            preferred_name=getattr(self.series, "title", None) or getattr(self.entry, "title", None),
        )
        self._handle_download_result(error, whole_series=True)

    def _download_selected(self):
        self._download_selected_urls(sorted(self._selected_urls))

    def _download_selected_urls(self, urls: list[str]):
        if not urls or self.series is None:
            return
        error = self.main_window.downloader.start_download_from_url(
            getattr(self.series, "url", "") or getattr(self.entry, "url", ""),
            preferred_name=getattr(self.series, "title", None) or getattr(self.entry, "title", None),
            chapter_urls=urls,
        )
        self._handle_download_result(error, whole_series=False, selected_count=len(urls))

    def _handle_download_result(self, error: str | None, *, whole_series: bool, selected_count: int = 0):
        if error:
            QMessageBox.warning(self, "Download", error)
            return
        if whole_series:
            self.status_label.setText("Whole-comic download started.")
        else:
            noun = "chapter" if selected_count == 1 else "chapters"
            self.status_label.setText(f"Download started for {selected_count} {noun}.")
        self.status_label.show()
        self.main_window.open_downloader()

    def _looks_like_access_block(self, error: str) -> bool:
        text = " ".join(str(error or "").casefold().split())
        return "cloudflare" in text or "anti-bot" in text

    def _merged_description(self, series) -> str:
        parts = []
        for raw in (
            getattr(self.entry, "description", None),
            getattr(series, "description", None),
        ):
            text = str(raw or "").strip()
            if not text:
                continue
            for piece in text.split(" | "):
                normalized = " ".join(piece.split()).strip()
                if normalized and normalized not in parts:
                    parts.append(normalized)
        if not parts:
            return "No description available."
        return " | ".join(parts)

    def _entry_count_text(self, entry) -> str:
        count_text = self._format_chapter_count(getattr(entry, "total_chapters", None))
        if count_text:
            return count_text
        return " ".join(str(getattr(entry, "latest_chapter", "") or "").split()).strip()

    def _series_count_text(self, series) -> str:
        if self._is_manga_series():
            page_total = 0
            for chapter in list(getattr(series, "chapters", []) or []):
                page_total += len(list(getattr(chapter, "pages", []) or []))
            if page_total > 0:
                return "1 page" if page_total == 1 else f"{page_total} pages"
        return self._format_chapter_count(len(getattr(series, "chapters", []) or []))

    def _format_chapter_count(self, count: int | None) -> str:
        if not count:
            return ""
        if count == 1:
            return "1 chapter"
        return f"{count} chapters"













