from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from core.app_logging import get_logger
from stores.download_history_store import get_instance as get_download_history
from gui.common.styles import SECTION_LABEL_STYLE
from gui.downloader.download_widgets import BTN_STYLE, INPUT_STYLE, CancellableDownloadEntry, HistoryDownloadEntry
from gui.downloader.page_base import DownloadHistoryPageBase
from gui.settings.settings_page import load_library_path

logger = get_logger(__name__)


class DownloaderPage(DownloadHistoryPageBase):

    def __init__(self, main_window):
        super().__init__(main_window, "Downloader", "History", history_kind="download")
        self.history_store = get_download_history()

        row = QHBoxLayout()
        row.setSpacing(8)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Paste URL...")
        self.url_input.setStyleSheet(INPUT_STYLE)
        self.url_input.returnPressed.connect(self._start_download)

        self.download_btn = QPushButton("Download")
        self.download_btn.setStyleSheet(BTN_STYLE)
        self.download_btn.setFixedWidth(100)
        self.download_btn.clicked.connect(self._start_download)

        self.cancel_btn = QPushButton("Cancel Active")
        self.cancel_btn.setStyleSheet(BTN_STYLE)
        self.cancel_btn.setFixedWidth(110)
        self.cancel_btn.clicked.connect(self._cancel_active_downloads)
        self.cancel_btn.setEnabled(False)

        row.addWidget(self.url_input)
        row.addWidget(self.download_btn)
        row.addWidget(self.cancel_btn)
        self.layout().insertLayout(1, row)

        self.activity_label = QLabel("Recent activity")
        self.activity_label.setStyleSheet(SECTION_LABEL_STYLE)
        self.history_layout.addWidget(self.activity_label)

        self.activity_section = QWidget()
        self.activity_section.setStyleSheet("background: transparent;")
        self.activity_list = QVBoxLayout(self.activity_section)
        self.activity_list.setContentsMargins(0, 0, 0, 0)
        self.activity_list.setSpacing(8)
        self.history_layout.addWidget(self.activity_section)

        self.refresh_recent_activity()
        self._sync_controls()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_recent_activity()

    def _start_download(self):
        url = self.url_input.text()
        logger.info("Manual download requested for url=%s", url.strip())
        self.start_download_from_url(url)

    def start_download_from_url(
        self,
        url: str,
        preferred_name: str | None = None,
        chapter_urls: list[str] | None = None,
    ) -> str | None:
        url = (url or "").strip()
        entry_name = self._next_entry_name(preferred_name or url)

        entry = CancellableDownloadEntry(
            entry_name,
            on_open=self._open_downloaded_webtoon_detail,
            on_cancel=self._cancel_entry_download,
        )
        self.history_layout.insertWidget(0, entry)
        self._register_entry(entry)

        error = self.service.start_download(
            url,
            load_library_path(),
            preferred_name=preferred_name,
            job_name=entry_name,
            chapter_urls=chapter_urls,
        )
        if error:
            logger.warning("Manual download rejected: %s", error)
            self.history_layout.removeWidget(entry)
            entry.deleteLater()
            self._remove_entry(entry)
            self.set_error_text(error)
            self.refresh_recent_activity()
            return error

        self.set_error_text("")
        self.url_input.clear()
        return None

    def _next_entry_name(self, url: str) -> str:
        base = url.strip().strip("'\"").rstrip("/").split("/")[-1] or "download"
        candidate = base
        counter = 2
        while self._entry_for(candidate) is not None:
            candidate = f"{base} ({counter})"
            counter += 1
        return candidate

    def _on_download_started(self, name: str):
        logger.info("Manual download started for %s", name)
        self._sync_controls()

    def _on_download_finished(self, name: str, status: str):
        logger.info("Manual download finished for %s with status=%s", name, status)
        entry = self._entry_for(name)
        if entry is not None:
            self.history_layout.removeWidget(entry)
            entry.deleteLater()
            self._remove_entry(entry)
        self.refresh_recent_activity()
        self._sync_controls()

    def _open_downloaded_webtoon_detail(self, webtoon_name: str):
        logger.info("Opening detail for manually downloaded webtoon %s", webtoon_name)
        webtoon = self.service.build_webtoon_from_folder(load_library_path(), webtoon_name)
        if webtoon is not None:
            self.main_window.open_detail(webtoon)

    def _cancel_entry_download(self, entry: CancellableDownloadEntry):
        logger.info("Cancelling manual download for %s", entry.name)
        self.service.cancel_download(entry.name)

    def _cancel_active_downloads(self):
        logger.info("Cancelling active manual downloads")
        self.service.cancel_download()
        self._sync_controls()

    def _sync_controls(self):
        self.cancel_btn.setEnabled(self.service.is_busy())

    def attach_history_service(self, service):
        service.download_started.connect(lambda _name: self.refresh_recent_activity())
        service.name_resolved.connect(lambda _old_name, _new_name: self.refresh_recent_activity())
        service.download_finished.connect(lambda _name, _status: self.refresh_recent_activity())
        service.status_changed.connect(lambda _name, _status: self.refresh_recent_activity())

    def refresh_recent_activity(self):
        while self.activity_list.count():
            item = self.activity_list.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for entry_data in self.history_store.list_entries():
            if (
                entry_data.get("kind") == "download"
                and self.service.has_active_download(entry_data.get("name", ""))
            ):
                continue
            entry = HistoryDownloadEntry(
                entry_data.get("name", ""),
                entry_data.get("kind", "download"),
                entry_data.get("status", "Ready"),
                entry_data.get("updated_at"),
                entry_data.get("source_url", ""),
                on_open=self._open_downloaded_webtoon_detail,
            )
            thumb_path = self.service.preferred_thumbnail_for(entry.name)
            if thumb_path:
                entry.set_thumbnail(thumb_path)
            self.activity_list.addWidget(entry)
