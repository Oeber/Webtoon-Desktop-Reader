import os
import time

from app_logging import get_logger
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel

from gui.downloader.download_widgets import UpdateEntry as BaseUpdateEntry
from gui.downloader.page_base import DownloadHistoryPageBase
from gui.settings.settings_page import load_library_path
from library_manager import scan_library
from webtoon_settings_store import get_instance as get_webtoon_settings


UPDATE_COOLDOWN_SECONDS = 30
logger = get_logger(__name__)


class UpdateEntry(BaseUpdateEntry):

    def cooldown_remaining(self) -> int:
        if self.last_update_at is None:
            return 0
        elapsed = int(time.time()) - int(self.last_update_at)
        return max(0, UPDATE_COOLDOWN_SECONDS - elapsed)


class UpdatePage(DownloadHistoryPageBase):

    def __init__(self, main_window):
        super().__init__(main_window, "Updates", "Saved source URLs")
        self.settings_store = get_webtoon_settings()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh_entries)
        self._cooldown_timer = QTimer(self)
        self._cooldown_timer.timeout.connect(self._sync_update_buttons)
        self._cooldown_timer.start(1000)

        self.refresh_entries()

    def showEvent(self, event):
        super().showEvent(event)
        if not self.service.is_busy():
            self.refresh_entries()

    def refresh_entries(self):
        logger.info("Refreshing update entries")
        active_name = self._active_entry.name if self._active_entry else None

        while self.history_layout.count():
            item = self.history_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        webtoons = scan_library(load_library_path(), self.settings_store)
        candidates = []
        for webtoon in webtoons:
            source_url = self.settings_store.get_source_url(webtoon.name)
            if source_url:
                candidates.append((webtoon, source_url, self.settings_store.get_last_update_at(webtoon.name)))

        if not candidates:
            empty = QLabel("No comics with a saved source URL yet.")
            empty.setStyleSheet("color: #777777; font-size: 13px; background: transparent;")
            empty.setAlignment(Qt.AlignCenter)
            self.history_layout.addWidget(empty)
            return

        current_active = None
        for webtoon, source_url, last_update_at in candidates:
            entry = UpdateEntry(webtoon.name, source_url, last_update_at, self._start_update)
            if webtoon.thumbnail and os.path.exists(webtoon.thumbnail):
                entry.set_thumbnail(webtoon.thumbnail)
            if self.service.is_busy() and webtoon.name == active_name:
                current_active = entry
                entry.set_status("Downloading")
            else:
                entry.set_status("Ready")
            self.history_layout.addWidget(entry)

        self._active_entry = current_active
        self._sync_update_buttons()

    def _start_update(self, entry: UpdateEntry):
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
            self.error_label.setText(error)
            return

        self.error_label.setText("")
        self._active_entry = entry

    def _sync_update_buttons(self):
        busy = self.service.is_busy()
        active_name = self._active_entry.name if self._active_entry else None

        for index in range(self.history_layout.count()):
            item = self.history_layout.itemAt(index)
            widget = item.widget()
            if isinstance(widget, UpdateEntry):
                if busy and widget.name == active_name:
                    widget.update_btn.setEnabled(False)
                    widget.update_btn.setText("Updating...")
                elif busy:
                    widget.update_btn.setEnabled(False)
                    widget.update_btn.setText("Update")
                else:
                    remaining = widget.cooldown_remaining()
                    widget.update_btn.setEnabled(remaining == 0)
                    widget.update_btn.setText(f"Wait {remaining}s" if remaining > 0 else "Update")

    def _on_download_started(self):
        logger.info("Update-page download started")
        self._refresh_timer.stop()
        self._sync_update_buttons()

    def _on_download_finished(self, name: str, status: str):
        logger.info("Update-page download finished for %s with status=%s", name, status)
        if status == "Completed":
            timestamp = int(time.time())
            self.settings_store.set_last_update_at(name, timestamp)
            if self._active_entry and self._active_entry.name == name:
                self._active_entry.set_last_update_at(timestamp)
        self._sync_update_buttons()
        self._active_entry = None
        self._refresh_timer.start(2500)

    def _on_library_changed(self):
        logger.info("Update page noticed library_changed")
        super()._on_library_changed()
        if self.isVisible():
            self._refresh_timer.stop()
            self._refresh_timer.start(0)
