from __future__ import annotations

import json
import shutil
from types import SimpleNamespace

from PySide6.QtCore import Qt
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
from stores.progress_store import get_instance as get_progress_store
from stores.tracked_titles_store import get_instance as get_tracked_titles_store
from stores.webtoon_settings_store import get_instance as get_webtoon_settings


logger = get_logger(__name__)


class TrackedTitleCard(QFrame):
    def __init__(self, row: dict, on_continue, on_add_to_library, on_download, on_remove, parent=None):
        super().__init__(parent)
        self._row = dict(row or {})
        self._on_continue = on_continue
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

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 4, 0, 0)
        button_row.setSpacing(8)

        self.continue_btn = QPushButton("Continue")
        self.continue_btn.setStyleSheet(BUTTON_STYLE)
        self.continue_btn.clicked.connect(lambda: self._on_continue(dict(self._row)))
        button_row.addWidget(self.continue_btn)

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
        self.continue_btn.setStyleSheet(BUTTON_STYLE)
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

        self.title_label.setText(title)
        self.meta_label.setText(f"{site_name} | {content_type} | cache: {cache_status}")
        status = str(self._row.get("status") or "tracked").strip()
        if local_name:
            self.status_label.setText(f"Bound local title: {local_name}")
        elif status == "library":
            self.status_label.setText("Shown in library")
        elif last_read:
            self.status_label.setText("Remote progress saved")
        else:
            self.status_label.setText("Tracked for remote reading")
        self.add_to_library_btn.setVisible(not local_name and status != "library")


class TrackedPage(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.store = get_tracked_titles_store()
        self.progress_store = get_progress_store()
        self.settings_store = get_webtoon_settings()
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
        rows = self.store.list_titles()
        if not rows:
            empty = QLabel("No tracked remote titles yet.")
            empty.setStyleSheet(TEXT_MUTED_LABEL_STYLE)
            self.content_layout.addWidget(empty)
            return
        for row in rows:
            card = TrackedTitleCard(row, self._continue_title, self._add_title_to_library, self._download_title, self._remove_title, self.content)
            self.content_layout.addWidget(card)

    def _continue_title(self, row: dict):
        if self.open_tracked_title(row):
            return
        QMessageBox.information(self, "Tracked", "No cached chapter is available for this title yet.")

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
            self.store.delete(track_id)
        except Exception:
            logger.exception("Failed to delete tracked title %s", track_id)
        cache_root = data_path("remote_cache", "titles", track_id)
        shutil.rmtree(cache_root, ignore_errors=True)
        self.refresh_entries()

    def open_tracked_title(self, row: dict) -> bool:
        payload = self._build_cached_webtoon(row)
        if payload is None:
            return False
        webtoon, chapter_index, start_scroll = payload
        setattr(webtoon, "_viewer_return", self._return_from_viewer)
        self.main_window.set_window_context_title(str(row.get("title") or webtoon.name))
        self.main_window.stack.setCurrentWidget(self.main_window.viewer)
        self.main_window.sidebar_controller.set_target("tracked")
        self.main_window.viewer.load_webtoon(webtoon, start_chapter=chapter_index, start_scroll=start_scroll)
        return True

    def _return_from_viewer(self):
        self.main_window.set_window_context_title("Tracked")
        self.main_window.stack.setCurrentWidget(self)
        self.main_window.sidebar_controller.set_target("tracked")
        self.refresh_entries()

    def _build_cached_webtoon(self, row: dict):
        track_id = str(row.get("track_id") or "").strip()
        if not track_id:
            return None
        chapters_root = data_path("remote_cache", "titles", track_id, "chapters")
        if not chapters_root.exists() or not chapters_root.is_dir():
            return None
        preferred_key = str(row.get("last_read_chapter_key") or "").strip()
        selected = None
        candidates = []
        for metadata_path in chapters_root.glob("*/chapter.json"):
            try:
                payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("Could not read tracked chapter metadata %s", metadata_path, exc_info=True)
                continue
            chapter_key = str(payload.get("chapter_key") or "").strip()
            folder_name = metadata_path.parent.name
            chapter_title = str(payload.get("chapter_title") or folder_name).strip() or folder_name
            progress = self.progress_store.get_by_chapter_key(chapter_key) or {}
            start_scroll = float(progress.get("scroll") or 0.0)
            candidate = (folder_name, chapter_title, chapter_key, start_scroll)
            candidates.append(candidate)
            if preferred_key and chapter_key == preferred_key:
                selected = candidate
        if selected is None and candidates:
            selected = candidates[0]
        if selected is None:
            return None
        folder_name, chapter_title, chapter_key, start_scroll = selected
        title = str(row.get("title") or chapter_title or "Tracked Title").strip()
        webtoon = SimpleNamespace(
            name=f"{title} [Remote]",
            path=str(chapters_root),
            chapters=[folder_name],
            chapter_keys={folder_name: chapter_key},
            chapter_display_names={folder_name: chapter_title},
            thumbnail=str(row.get("cover_url") or "").strip(),
            content_type=str(row.get("content_type") or "webtoon").strip() or "webtoon",
            is_remote=True,
            remote_track_id=track_id,
            remote_series_url=str(row.get("source_url") or "").strip(),
        )
        return webtoon, 0, start_scroll
