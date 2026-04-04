from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.app_logging import get_logger
from core.app_paths import data_path
from core.chapter_identity import build_remote_chapter_key
from core.hybrid_models import ViewerChapterSource
from gui.common.styles import (
    BUTTON_STYLE,
    PAGE_BG_STYLE,
    PAGE_TITLE_STYLE,
    SCROLL_AREA_STYLE,
    STATUS_LABEL_STYLE,
    SURFACE_PANEL_STYLE,
    TEXT_MUTED_LABEL_STYLE,
    TRANSPARENT_BG_STYLE,
)
from scrapers.base import ScraperError
from scrapers.registry import get_scraper
from stores.chapter_ref_store import get_instance as get_chapter_ref_store
from stores.progress_store import get_instance as get_progress_store
from stores.scraper_settings_store import load_scraper_default_config
from stores.settings_store import load_library_path
from stores.tracked_titles_store import get_instance as get_tracked_titles_store
from stores.webtoon_settings_store import get_instance as get_webtoon_settings
from gui.viewer.remote_read_service import RemoteReadService


logger = get_logger(__name__)


class TrackedRemoteReadLoader(QObject):
    loaded = Signal(int, object, float, str)

    def __init__(self, progress_store, parent=None):
        super().__init__(parent)
        self._service = RemoteReadService(progress_store)

    def load(self, request_id: int, row: dict, *, mode: str = "latest"):
        def worker():
            try:
                source_url = str(row.get("source_url") or "").strip()
                if not source_url:
                    raise ScraperError("This tracked title does not have a saved source URL.")
                scraper = get_scraper(source_url)
                if scraper is None:
                    raise ScraperError("This tracked title does not support remote reading.")
                scraper.apply_source_config(load_scraper_default_config(getattr(scraper, "site_name", "") or ""))
                series_url = source_url if not scraper.is_chapter_url(source_url) else scraper.series_url_from_chapter_url(source_url)
                series = scraper.get_series_info(series_url)
                chapters = list(getattr(series, "chapters", []) or [])
                if not chapters:
                    raise ScraperError("No readable chapters were found for this title.")
                target_chapter = self._select_chapter(row, series, chapters, mode=mode)
                webtoon, chapter_index, start_scroll = self._service.prepare_chapter(scraper, series, target_chapter)
                self.loaded.emit(request_id, (webtoon, chapter_index), float(start_scroll or 0.0), "")
            except ScraperError as e:
                self.loaded.emit(request_id, None, 0.0, str(e))
            except Exception as e:
                logger.exception("Unexpected tracked remote-read preparation failure")
                self.loaded.emit(request_id, None, 0.0, str(e))

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _select_chapter(row: dict, series, chapters: list, *, mode: str):
        normalized_mode = str(mode or "latest").strip().lower()
        if normalized_mode == "continue":
            site_name = str(getattr(series, "site", "") or row.get("site_name") or "").strip()
            series_id = str(getattr(series, "series_id", "") or row.get("series_id") or getattr(series, "url", "") or "").strip()
            target_key = str(row.get("last_read_chapter_key") or "").strip()
            if target_key:
                for index, chapter in enumerate(chapters):
                    chapter_id = str(getattr(chapter, "id", "") or "").strip()
                    chapter_url = str(getattr(chapter, "url", "") or "").strip()
                    candidate_key = build_remote_chapter_key(site_name, series_id, chapter_id, chapter_url)
                    if candidate_key == target_key:
                        progress_store = get_progress_store()
                        progress = progress_store.get_by_chapter_key(candidate_key) or {}
                        if _chapter_progress_complete(progress) and index + 1 < len(chapters):
                            return chapters[index + 1]
                        return chapter
            return chapters[0]
        return chapters[-1]


class TrackedSeriesInfoLoader(QObject):
    loaded = Signal(int, str, object, str)

    def load(self, request_id: int, row: dict):
        def worker():
            try:
                source_url = str(row.get("source_url") or "").strip()
                if not source_url:
                    raise ScraperError("No saved source URL.")
                scraper = get_scraper(source_url)
                if scraper is None:
                    raise ScraperError("This tracked title does not support remote lookup.")
                scraper.apply_source_config(load_scraper_default_config(getattr(scraper, "site_name", "") or ""))
                series_url = source_url if not scraper.is_chapter_url(source_url) else scraper.series_url_from_chapter_url(source_url)
                series = scraper.get_series_info(series_url)
                self.loaded.emit(request_id, str(row.get("track_id") or "").strip(), series, "")
            except ScraperError as e:
                self.loaded.emit(request_id, str(row.get("track_id") or "").strip(), None, str(e))
            except Exception as e:
                logger.exception("Unexpected tracked series info load failure")
                self.loaded.emit(request_id, str(row.get("track_id") or "").strip(), None, str(e))

        threading.Thread(target=worker, daemon=True).start()


def _chapter_progress_complete(progress: dict | None) -> bool:
    if not progress:
        return False
    total_images = int(progress.get("total_images") or 0)
    scroll = float(progress.get("scroll") or 0.0)
    return total_images > 0 and scroll >= float(total_images)


class TrackedTitleCard(QFrame):
    def __init__(self, row: dict, on_open, on_continue, on_read_latest, on_add_to_library, on_download, on_remove, parent=None):
        super().__init__(parent)
        self._row = dict(row or {})
        self._on_open = on_open
        self._on_continue = on_continue
        self._on_read_latest = on_read_latest
        self._on_add_to_library = on_add_to_library
        self._on_download = on_download
        self._on_remove = on_remove

        self.setStyleSheet(SURFACE_PANEL_STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        self.title_label = QLabel()
        self.title_label.setStyleSheet(PAGE_TITLE_STYLE)
        layout.addWidget(self.title_label)

        self.meta_label = QLabel()
        self.meta_label.setStyleSheet(TEXT_MUTED_LABEL_STYLE)
        self.meta_label.setWordWrap(True)
        layout.addWidget(self.meta_label)

        self.status_label = QLabel()
        self.status_label.setStyleSheet(STATUS_LABEL_STYLE)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.remote_info_label = QLabel()
        self.remote_info_label.setStyleSheet(TEXT_MUTED_LABEL_STYLE)
        self.remote_info_label.setWordWrap(True)
        self.remote_info_label.hide()
        layout.addWidget(self.remote_info_label)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 4, 0, 0)
        button_row.setSpacing(8)

        self.open_btn = QPushButton("Open")
        self.open_btn.setStyleSheet(BUTTON_STYLE)
        self.open_btn.clicked.connect(lambda: self._on_open(dict(self._row)))
        button_row.addWidget(self.open_btn)

        self.continue_btn = QPushButton("Continue")
        self.continue_btn.setStyleSheet(BUTTON_STYLE)
        self.continue_btn.clicked.connect(lambda: self._on_continue(dict(self._row)))
        button_row.addWidget(self.continue_btn)

        self.read_latest_btn = QPushButton("Read Latest")
        self.read_latest_btn.setStyleSheet(BUTTON_STYLE)
        self.read_latest_btn.clicked.connect(lambda: self._on_read_latest(dict(self._row)))
        button_row.addWidget(self.read_latest_btn)

        self.add_to_library_btn = QPushButton("Add to Library")
        self.add_to_library_btn.setStyleSheet(BUTTON_STYLE)
        self.add_to_library_btn.clicked.connect(lambda: self._on_add_to_library(dict(self._row)))
        button_row.addWidget(self.add_to_library_btn)

        self.download_btn = QPushButton("Download")
        self.download_btn.setStyleSheet(BUTTON_STYLE)
        self.download_btn.clicked.connect(lambda: self._on_download(dict(self._row)))
        button_row.addWidget(self.download_btn)

        self.remove_btn = QPushButton("Remove")
        self.remove_btn.setStyleSheet(BUTTON_STYLE)
        self.remove_btn.clicked.connect(lambda: self._on_remove(dict(self._row)))
        button_row.addWidget(self.remove_btn)

        button_row.addStretch()
        layout.addLayout(button_row)
        self.update_row(row)

    def apply_theme(self):
        self.setStyleSheet(SURFACE_PANEL_STYLE)
        self.title_label.setStyleSheet(PAGE_TITLE_STYLE)
        self.meta_label.setStyleSheet(TEXT_MUTED_LABEL_STYLE)
        self.status_label.setStyleSheet(STATUS_LABEL_STYLE)
        self.remote_info_label.setStyleSheet(TEXT_MUTED_LABEL_STYLE)
        self.open_btn.setStyleSheet(BUTTON_STYLE)
        self.continue_btn.setStyleSheet(BUTTON_STYLE)
        self.read_latest_btn.setStyleSheet(BUTTON_STYLE)
        self.add_to_library_btn.setStyleSheet(BUTTON_STYLE)
        self.download_btn.setStyleSheet(BUTTON_STYLE)
        self.remove_btn.setStyleSheet(BUTTON_STYLE)

    def update_row(self, row: dict):
        self._row = dict(row or {})
        title = str(self._row.get("title") or "Tracked Title").strip()
        site_name = str(self._row.get("site_name") or "Unknown source").strip()
        content_type = str(self._row.get("content_type") or "webtoon").strip()
        cache_status = str(self._row.get("cache_status") or "none").strip()
        local_name = str(self._row.get("local_webtoon_name") or "").strip()
        last_read = str(self._row.get("last_read_chapter_key") or "").strip()
        source_url = str(self._row.get("source_url") or "").strip()
        cached_count = max(0, int(self._row.get("cached_chapter_count") or 0))
        last_read_title = str(self._row.get("last_read_title") or "").strip()
        remote_total = max(0, int(self._row.get("remote_chapter_count") or 0))
        unread_count = max(0, int(self._row.get("remote_unread_count") or 0))

        self.title_label.setText(title)
        cache_text = f"{cached_count} cached chapter" if cached_count == 1 else f"{cached_count} cached chapters"
        if cache_status == "cached" and cached_count == 0:
            cache_text = "cached"
        self.meta_label.setText(f"{site_name} | {content_type} | {cache_text}")
        status = str(self._row.get("status") or "tracked").strip()
        if local_name:
            self.status_label.setText(f"Bound local title: {local_name}")
        elif status == "library":
            self.status_label.setText("Shown in library")
        elif last_read_title and unread_count > 0:
            noun = "chapter" if unread_count == 1 else "chapters"
            self.status_label.setText(f"Continue after {last_read_title} | {unread_count} unread {noun}")
        elif last_read_title:
            self.status_label.setText(f"Last read: {last_read_title}")
        elif last_read:
            self.status_label.setText("Remote progress saved")
        elif remote_total > 0:
            noun = "chapter" if remote_total == 1 else "chapters"
            self.status_label.setText(f"{remote_total} remote {noun} available")
        else:
            self.status_label.setText("Tracked for remote reading")
        self.open_btn.setEnabled(bool(source_url))
        self.read_latest_btn.setEnabled(bool(source_url))
        self.add_to_library_btn.setVisible(not local_name and status != "library")
        self.remote_info_label.hide()
        self.remote_info_label.clear()

    def set_remote_info(self, text: str):
        message = str(text or "").strip()
        self.remote_info_label.setVisible(bool(message))
        self.remote_info_label.setText(message)


class TrackedPage(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.store = get_tracked_titles_store()
        self.chapter_ref_store = get_chapter_ref_store()
        self.progress_store = get_progress_store()
        self.settings_store = get_webtoon_settings()
        self._remote_read_loader = TrackedRemoteReadLoader(self.progress_store, self)
        self._remote_read_loader.loaded.connect(self._on_remote_read_loaded)
        self._series_info_loader = TrackedSeriesInfoLoader(self)
        self._series_info_loader.loaded.connect(self._on_series_info_loaded)
        self._read_request_id = 0
        self._series_request_id = 0
        self._pending_read_row = None
        self._pending_read_mode = "latest"
        self._cards_by_track_id: dict[str, TrackedTitleCard] = {}
        self.setStyleSheet(PAGE_BG_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(12)

        self.title_label = QLabel("Tracked")
        self.title_label.setStyleSheet(PAGE_TITLE_STYLE)
        root.addWidget(self.title_label)

        self.status_label = QLabel("Remote titles you opened without a full download show up here.")
        self.status_label.setStyleSheet(STATUS_LABEL_STYLE)
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet(SCROLL_AREA_STYLE)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root.addWidget(self.scroll, 1)

        self.content = QWidget()
        self.content.setStyleSheet(TRANSPARENT_BG_STYLE)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(12)
        self.content_layout.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(self.content)

    def apply_theme(self):
        self.setStyleSheet(PAGE_BG_STYLE)
        self.title_label.setStyleSheet(PAGE_TITLE_STYLE)
        self.status_label.setStyleSheet(STATUS_LABEL_STYLE)
        self.scroll.setStyleSheet(SCROLL_AREA_STYLE)
        self.content.setStyleSheet(TRANSPARENT_BG_STYLE)
        for index in range(self.content_layout.count()):
            item = self.content_layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is not None and hasattr(widget, "apply_theme"):
                widget.apply_theme()

    def schedule_open_refresh(self):
        self.refresh_entries()

    def refresh_entries(self):
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cards_by_track_id = {}
        rows = self.store.list_titles()
        if not rows:
            empty = QLabel("No tracked remote titles yet.")
            empty.setStyleSheet(TEXT_MUTED_LABEL_STYLE)
            self.content_layout.addWidget(empty)
            return
        self._series_request_id += 1
        request_id = self._series_request_id
        for row in rows:
            enriched_row = self._enrich_row(row)
            card = TrackedTitleCard(
                enriched_row,
                self._open_detail,
                self._continue_title,
                self._read_latest_title,
                self._add_title_to_library,
                self._download_title,
                self._remove_title,
                self.content,
            )
            self.content_layout.addWidget(card)
            track_id = str(enriched_row.get("track_id") or "").strip()
            if track_id:
                self._cards_by_track_id[track_id] = card
                if str(enriched_row.get("source_url") or "").strip():
                    card.set_remote_info("Checking remote chapters...")
                    self._series_info_loader.load(request_id, enriched_row)

    def _continue_title(self, row: dict):
        if self.open_tracked_title(row):
            return
        self._read_remote_title(row, mode="continue")

    def _open_detail(self, row: dict):
        synthetic = self._synthetic_tracked_webtoon(row)
        if synthetic is None:
            QMessageBox.warning(self, "Tracked", "This tracked title could not be opened.")
            return
        self.main_window.open_detail(synthetic, force=True)

    def _read_latest_title(self, row: dict):
        self._read_remote_title(row, mode="latest")

    def _read_remote_title(self, row: dict, *, mode: str):
        source_url = str(row.get("source_url") or "").strip()
        if not source_url:
            QMessageBox.warning(self, "Tracked", "This tracked title does not have a saved source URL.")
            return
        self._read_request_id += 1
        self._pending_read_row = dict(row)
        self._pending_read_mode = str(mode or "latest").strip().lower() or "latest"
        chapter_label = "latest chapter" if self._pending_read_mode == "latest" else "saved chapter"
        title = str(row.get("title") or "Tracked Title").strip()
        self.main_window.set_window_context_title(title)
        self.main_window.stack.setCurrentWidget(self.main_window.viewer)
        self.main_window.sidebar_controller.set_target("tracked")
        self.main_window.chapter_overlay.show(title, chapter_label)
        self._remote_read_loader.load(self._read_request_id, row, mode=self._pending_read_mode)

    def _on_remote_read_loaded(self, request_id: int, payload, start_scroll: float, error: str):
        if request_id != self._read_request_id:
            return
        row = dict(self._pending_read_row or {})
        self._pending_read_row = None
        if error:
            self.main_window.chapter_overlay.hide()
            site_name = str(row.get("site_name") or "").strip()
            source_url = str(row.get("source_url") or "").strip()
            if self._looks_like_access_block(error) and site_name and self.main_window.open_site_authorization(site_name, url=source_url):
                self._read_remote_title(row, mode=self._pending_read_mode)
                return
            self.main_window.set_window_context_title("Tracked")
            self.main_window.stack.setCurrentWidget(self)
            self.main_window.sidebar_controller.set_target("tracked")
            QMessageBox.warning(self, "Tracked", error)
            return
        if not payload:
            self.main_window.chapter_overlay.hide()
            self.main_window.set_window_context_title("Tracked")
            self.main_window.stack.setCurrentWidget(self)
            self.main_window.sidebar_controller.set_target("tracked")
            return
        owner, chapter_sources, start_chapter_key = payload
        setattr(owner, "_viewer_return", self._return_from_viewer)
        title = str(row.get("title") or getattr(owner, "title", "Tracked Title")).strip()
        self.main_window.set_window_context_title(title)
        self.main_window.stack.setCurrentWidget(self.main_window.viewer)
        self.main_window.sidebar_controller.set_target("tracked")
        self.main_window.viewer.load_hybrid_title(
            title,
            owner,
            chapter_sources,
            start_chapter_key=start_chapter_key,
            start_scroll=start_scroll,
        )
    def _on_series_info_loaded(self, request_id: int, track_id: str, series, error: str):
        if request_id != self._series_request_id:
            return
        card = self._cards_by_track_id.get(str(track_id or "").strip())
        if card is None:
            return
        if error:
            if self._looks_like_access_block(error):
                card.set_remote_info("Authorization may be required to check remote chapters.")
            else:
                card.set_remote_info("Could not refresh remote chapter info.")
            return
        track_id_value = str(track_id or "").strip()
        if track_id_value:
            try:
                self.store.touch_checked(track_id_value)
            except Exception:
                logger.warning("Could not update tracked last_checked_at for %s", track_id_value, exc_info=True)
        chapters = list(getattr(series, "chapters", []) or [])
        if not chapters:
            card.set_remote_info("No remote chapters found.")
            return
        row = dict(getattr(card, "_row", {}) or {})
        site_name = str(getattr(series, "site", "") or row.get("site_name") or "").strip()
        series_id = str(getattr(series, "series_id", "") or row.get("series_id") or getattr(series, "url", "") or "").strip()
        last_key = str(row.get("last_read_chapter_key") or "").strip()
        matched_index = None
        if last_key:
            for index, chapter in enumerate(chapters):
                chapter_id = str(getattr(chapter, "id", "") or "").strip()
                chapter_url = str(getattr(chapter, "url", "") or "").strip()
                candidate_key = build_remote_chapter_key(site_name, series_id, chapter_id, chapter_url)
                if candidate_key == last_key:
                    matched_index = index
                    break
        latest = chapters[-1]
        latest_title = str(getattr(latest, "title", "") or getattr(latest, "url", "") or "Latest chapter").strip()
        updated_row = dict(row)
        updated_row["remote_chapter_count"] = len(chapters)
        if matched_index is None:
            updated_row["remote_unread_count"] = len(chapters)
            updated_row["latest_remote_chapter_title"] = latest_title
            card.update_row(updated_row)
            card.set_remote_info(f"{len(chapters)} chapters available | Latest: {latest_title}")
            return
        matched_title = str(getattr(chapters[matched_index], "title", "") or getattr(chapters[matched_index], "url", "") or "").strip()
        if matched_title:
            updated_row["last_read_title"] = matched_title
        matched_key = str(last_key or "").strip()
        matched_progress = self.progress_store.get_by_chapter_key(matched_key) if matched_key else None
        progress_complete = _chapter_progress_complete(matched_progress)
        remaining = max(0, len(chapters) - matched_index - (1 if progress_complete else 0))
        updated_row["remote_unread_count"] = remaining
        updated_row["latest_remote_chapter_title"] = latest_title
        card.update_row(updated_row)
        if remaining > 0:
            next_index = min(len(chapters) - 1, matched_index + 1) if progress_complete else matched_index
            next_title = str(getattr(chapters[next_index], "title", "") or getattr(chapters[next_index], "url", "") or "Next chapter").strip()
            noun = "chapter" if remaining == 1 else "chapters"
            card.set_remote_info(f"{remaining} unread {noun} | Next: {next_title}")
        else:
            card.set_remote_info(f"Up to date | Latest: {latest_title}")
    @staticmethod
    def _looks_like_access_block(error: str) -> bool:
        text = " ".join(str(error or "").casefold().split())
        return "cloudflare" in text or "anti-bot" in text or "authorization" in text

    def add_title_to_library(self, row: dict) -> bool:
        track_id = str(row.get("track_id") or "").strip()
        title = str(row.get("title") or "").strip()
        if not track_id or not title:
            return False
        self.settings_store.save_source_metadata(
            title,
            source_url=str(row.get("source_url") or "").strip() or None,
            source_site=str(row.get("site_name") or "").strip() or None,
            source_series_id=str(row.get("series_id") or "").strip() or None,
            source_title=title or None,
            content_type=str(row.get("content_type") or "webtoon").strip() or None,
        )
        cover_url = str(row.get("cover_url") or "").strip()
        if cover_url and not self.settings_store.get(title):
            try:
                self.settings_store.set_from_url(title, cover_url)
            except Exception:
                logger.warning("Could not cache tracked title thumbnail for %s", title, exc_info=True)
        self.store.add_to_library(track_id)
        self.main_window.library.load_library()
        return True

    def _add_title_to_library(self, row: dict):
        if not self.add_title_to_library(row):
            QMessageBox.warning(self, "Tracked", "Could not add this tracked title to the library.")
            return
        self.refresh_entries()

    def _download_title(self, row: dict):
        source_url = str(row.get("source_url") or "").strip()
        if not source_url:
            QMessageBox.warning(self, "Tracked", "This tracked title does not have a saved source URL.")
            return
        error = self.main_window.downloader.start_download_from_url(
            source_url,
            preferred_name=str(row.get("title") or "").strip() or None,
        )
        if error:
            QMessageBox.warning(self, "Tracked", error)
            return
        self.main_window.open_downloader()

    def _remove_title(self, row: dict):
        title = str(row.get("title") or "Tracked Title").strip()
        result = QMessageBox.question(
            self,
            "Remove from tracked titles",
            f"Remove '{title}' from the tracked list?\n\nThis also clears its cached remote chapter data. Downloaded library chapters are not affected.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if result != QMessageBox.Yes:
            return
        track_id = str(row.get("track_id") or "").strip()
        try:
            self.chapter_ref_store.clear_cache_for_owner("tracked", track_id)
            self.chapter_ref_store.delete_for_owner("tracked", track_id)
            self.store.delete(track_id)
        except Exception:
            logger.exception("Failed to delete tracked title %s", track_id)
        cache_root = data_path("remote_cache", "titles", track_id)
        shutil.rmtree(cache_root, ignore_errors=True)
        self.refresh_entries()

    def open_tracked_title(self, row: dict) -> bool:
        payload = self._build_cached_hybrid_payload(row)
        if payload is None:
            return False
        title, owner, chapter_sources, start_chapter_key, start_scroll = payload
        setattr(owner, "_viewer_return", self._return_from_viewer)
        self.main_window.set_window_context_title(title)
        self.main_window.stack.setCurrentWidget(self.main_window.viewer)
        self.main_window.sidebar_controller.set_target("tracked")
        self.main_window.viewer.load_hybrid_title(
            title,
            owner,
            chapter_sources,
            start_chapter_key=start_chapter_key,
            start_scroll=start_scroll,
        )
        return True

    def _return_from_viewer(self):
        self.main_window.set_window_context_title("Tracked")
        self.main_window.stack.setCurrentWidget(self)
        self.main_window.sidebar_controller.set_target("tracked")
        self.refresh_entries()

    def _build_cached_hybrid_payload(self, row: dict):
        track_id = str(row.get("track_id") or "").strip()
        if not track_id:
            return None
        preferred_key = str(row.get("last_read_chapter_key") or "").strip()
        cached_entries = self._cached_chapter_entries(row)
        if not cached_entries:
            return None

        chapter_sources = []
        start_scroll = 0.0
        start_chapter_key = preferred_key
        for payload in cached_entries:
            chapter_key = str(payload.get("chapter_key") or "").strip()
            if not chapter_key:
                continue
            storage_path = str(payload.get("cache_path") or "").strip()
            if not storage_path:
                folder_name = self._chapter_folder_name(chapter_key)
                storage_path = str((data_path("remote_cache", "titles", track_id, "chapters") / folder_name).resolve())
            chapter_title = str(payload.get("chapter_title") or Path(storage_path).name or chapter_key).strip() or chapter_key
            local_name = str(payload.get("local_chapter_name") or Path(storage_path).name or chapter_key).strip() or chapter_key
            source = ViewerChapterSource(
                chapter_key=chapter_key,
                title=chapter_title,
                number=payload.get("chapter_number"),
                content_type=str(row.get("content_type") or payload.get("content_type") or "webtoon").strip() or "webtoon",
                source_kind="cached_remote",
                storage_path=storage_path,
                remote_url=str(payload.get("remote_url") or "").strip(),
                local_chapter_name=local_name,
            )
            chapter_sources.append(source)
            if preferred_key and chapter_key == preferred_key:
                progress = self.progress_store.get_by_chapter_key(chapter_key) or {}
                start_scroll = float(progress.get("scroll") or 0.0)
        if not chapter_sources:
            return None
        if not start_chapter_key:
            start_chapter_key = str(chapter_sources[0].chapter_key or "").strip()
        title = str(row.get("title") or "Tracked Title").strip() or "Tracked Title"
        owner = SimpleNamespace(
            title=title,
            thumbnail=str(row.get("cover_url") or "").strip(),
            cover_url=str(row.get("cover_url") or "").strip(),
            content_type=str(row.get("content_type") or "webtoon").strip() or "webtoon",
            track_id=track_id,
            source_url=str(row.get("source_url") or "").strip(),
        )
        return title, owner, chapter_sources, start_chapter_key, start_scroll
    def _synthetic_tracked_webtoon(self, row: dict):
        normalized_row = self._enrich_row(row)
        title = str(normalized_row.get("title") or "Tracked Title").strip()
        if not title:
            return None
        thumbnail = str(
            self.settings_store.get(title)
            or normalized_row.get("cover_url")
            or ""
        ).strip()
        return SimpleNamespace(
            name=title,
            path=str(load_library_path()),
            storage_path="",
            chapters=[],
            thumbnail=thumbnail,
            category=None,
            is_bookmarked=False,
            has_new_chapter=False,
            content_type=str(normalized_row.get("content_type") or "webtoon").strip() or "webtoon",
            _tracked_library_placeholder=True,
            _tracked_row=normalized_row,
            _detail_origin="tracked",
        )

    def _enrich_row(self, row: dict) -> dict:
        enriched = dict(row or {})
        cached_entries = self._cached_chapter_entries(enriched)
        enriched["cached_chapter_count"] = len(cached_entries)
        preferred_key = str(enriched.get("last_read_chapter_key") or "").strip()
        if preferred_key:
            for entry in cached_entries:
                if str(entry.get("chapter_key") or "").strip() == preferred_key:
                    chapter_title = str(entry.get("chapter_title") or "").strip()
                    if chapter_title:
                        enriched["last_read_title"] = chapter_title
                    break
        return enriched

    def _cached_chapter_entries(self, row: dict) -> list[dict]:
        track_id = str(row.get("track_id") or "").strip()
        if not track_id:
            return []
        entries = []
        for ref in self.chapter_ref_store.list_cached_for_owner("tracked", track_id):
            cache_path = str(ref.get("cache_path") or "").strip()
            if cache_path and Path(cache_path).exists():
                entries.append(dict(ref))
        if entries:
            entries.sort(
                key=lambda item: (
                    -int(item.get("updated_at") or 0),
                    str(item.get("chapter_title") or item.get("remote_url") or "").casefold(),
                )
            )
            return entries

        chapters_root = data_path("remote_cache", "titles", track_id, "chapters")
        if not chapters_root.exists() or not chapters_root.is_dir():
            return []
        for metadata_path in chapters_root.glob("*/chapter.json"):
            try:
                payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("Could not read tracked chapter metadata %s", metadata_path, exc_info=True)
                continue
            if not isinstance(payload, dict):
                continue
            payload.setdefault("cache_path", str(metadata_path.parent.resolve()))
            entries.append(payload)
        entries.sort(
            key=lambda item: (
                -int(item.get("fetched_at") or 0),
                str(item.get("chapter_title") or item.get("remote_url") or "").casefold(),
            )
        )
        return entries


    @staticmethod
    def _chapter_folder_name(chapter_key: str) -> str:
        import hashlib

        return hashlib.sha1(str(chapter_key or '').encode('utf-8', errors='ignore')).hexdigest()

