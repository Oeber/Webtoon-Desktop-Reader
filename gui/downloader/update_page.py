import os
import time

from app_logging import get_logger
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QLineEdit

from gui.common.styles import EMPTY_STATE_LABEL_STYLE, SEARCH_INPUT_STYLE
from gui.downloader.download_widgets import UpdateEntry as BaseUpdateEntry
from gui.downloader.page_base import DownloadHistoryPageBase
from gui.search.global_search import rank_webtoons
from gui.settings.settings_page import load_library_path
from library_manager import scan_library
from update_utils import cooldown_remaining
from webtoon_settings_store import get_instance as get_webtoon_settings


logger = get_logger(__name__)


class UpdateEntry(BaseUpdateEntry):

    def cooldown_remaining(self) -> int:
        return cooldown_remaining(self.last_update_at)


class UpdatePage(DownloadHistoryPageBase):

    def __init__(self, main_window):
        super().__init__(main_window, "Updates", "Saved source URLs", history_kind="update")
        self.settings_store = get_webtoon_settings()
        self._candidates = []
        self._pending_search = ""

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search titles...")
        self.search_input.setFixedHeight(36)
        self.search_input.setStyleSheet(SEARCH_INPUT_STYLE)
        self.search_input.textChanged.connect(self._schedule_filter)
        self.layout().insertWidget(3, self.search_input)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_filter)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh_entries)
        self._cooldown_timer = QTimer(self)
        self._cooldown_timer.timeout.connect(self._sync_update_buttons)
        self._cooldown_timer.start(1000)

        self.refresh_entries()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_entries()

    def refresh_entries(self):
        logger.info("Refreshing update entries")
        webtoons = scan_library(load_library_path(), self.settings_store)
        candidates = []
        for webtoon in webtoons:
            if self.settings_store.get_completed(webtoon.name):
                continue
            source_url = self.settings_store.get_source_url(webtoon.name)
            if source_url:
                candidates.append((webtoon, source_url, self.settings_store.get_last_update_at(webtoon.name)))

        self._candidates = candidates
        self._apply_filter()

    def _clear_history(self):
        self._entries_by_name.clear()
        while self.history_layout.count():
            item = self.history_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _apply_filter(self):
        self._clear_history()

        visible_candidates = self._filtered_candidates(self._pending_search)
        if not visible_candidates:
            empty = QLabel("No comics with a saved source URL yet.")
            if self._pending_search.strip():
                empty.setText("No update entries match your search.")
            empty.setStyleSheet(EMPTY_STATE_LABEL_STYLE)
            empty.setAlignment(Qt.AlignCenter)
            self.history_layout.addWidget(empty)
            return

        for webtoon, source_url, last_update_at in visible_candidates:
            entry = UpdateEntry(webtoon.name, source_url, last_update_at, self._start_update)
            self._register_entry(entry)
            if webtoon.thumbnail and os.path.exists(webtoon.thumbnail):
                entry.set_thumbnail(webtoon.thumbnail)
            if self.service.has_active_download(webtoon.name):
                entry.set_status("Downloading")
            else:
                entry.set_status("Ready")
            self.history_layout.addWidget(entry)

        self._sync_update_buttons()

    def _filtered_candidates(self, text: str):
        query = text.strip()
        if not query:
            return list(self._candidates)

        ranked = rank_webtoons([webtoon for webtoon, _, _ in self._candidates], query)
        ranked_names = [webtoon.name for _, webtoon in ranked]
        candidates_by_name = {
            webtoon.name: (webtoon, source_url, last_update_at)
            for webtoon, source_url, last_update_at in self._candidates
        }
        return [
            candidates_by_name[name]
            for name in ranked_names
            if name in candidates_by_name
        ]

    def _schedule_filter(self, text: str):
        logger.info("Scheduling update-page filter for query='%s'", text.strip())
        self._pending_search = text
        self._search_timer.start(150)

    def _start_update(self, entry: UpdateEntry):
        if self.settings_store.get_completed(entry.name):
            logger.info("Update page blocked completed webtoon %s", entry.name)
            self.refresh_entries()
            return
        if entry.cooldown_remaining() > 0:
            logger.info("Update page cooldown blocked %s", entry.name)
            self._sync_update_buttons()
            return

        logger.info("Starting update-page download for %s", entry.name)
        error = self.service.start_download(
            entry.source_url,
            load_library_path(),
            preferred_name=entry.name,
        )
        if error:
            logger.warning("Update-page download rejected for %s: %s", entry.name, error)
            self.set_error_text(error)
            return

        self.set_error_text("")

    def start_update_for_webtoon(self, webtoon_name: str) -> str | None:
        if not webtoon_name:
            return "Please choose a title to update."

        self.refresh_entries()
        entry = self._entry_for(webtoon_name)
        if entry is None:
            error = f"No saved source URL found for '{webtoon_name}'."
            self.set_error_text(error)
            return error

        if self.settings_store.get_completed(entry.name):
            self.refresh_entries()
            error = f"'{entry.name}' is marked completed."
            self.set_error_text(error)
            return error

        if entry.cooldown_remaining() > 0:
            self._sync_update_buttons()
            error = f"'{entry.name}' is still on cooldown."
            self.set_error_text(error)
            return error

        logger.info("Starting update from external trigger for %s", entry.name)
        error = self.service.start_download(
            entry.source_url,
            load_library_path(),
            preferred_name=entry.name,
        )
        self.set_error_text("" if error is None else error)
        self._sync_update_buttons()
        return error

    def _sync_update_buttons(self):
        for index in range(self.history_layout.count()):
            item = self.history_layout.itemAt(index)
            widget = item.widget()
            if isinstance(widget, UpdateEntry):
                if self.service.has_active_download(widget.name):
                    widget.update_btn.setEnabled(False)
                    widget.update_btn.setText("Updating...")
                else:
                    remaining = widget.cooldown_remaining()
                    widget.update_btn.setEnabled(remaining == 0)
                    widget.update_btn.setText(f"Wait {remaining}s" if remaining > 0 else "Update")

    def _on_download_started(self, name: str):
        logger.info("Update-page download started for %s", name)
        self._refresh_timer.stop()
        self._sync_update_buttons()

    def _on_download_finished(self, name: str, status: str):
        logger.info("Update-page download finished for %s with status=%s", name, status)
        if status == "Completed":
            timestamp = int(time.time())
            self.settings_store.set_last_update_at(name, timestamp)
            entry = self._entry_for(name)
            if entry is not None:
                entry.set_last_update_at(timestamp)
        self._sync_update_buttons()
        if not self.service.is_busy():
            self._refresh_timer.start(2500)

    def _on_library_changed(self, name: str):
        logger.info("Update page noticed library_changed")
        if self.isVisible() and not self.service.is_busy():
            self._refresh_timer.stop()
            self._refresh_timer.start(0)
