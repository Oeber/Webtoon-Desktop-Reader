from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton

from app_logging import get_logger
from gui.downloader.download_widgets import BTN_STYLE, INPUT_STYLE, CancellableDownloadEntry
from gui.downloader.page_base import DownloadHistoryPageBase
from gui.settings.settings_page import load_library_path

logger = get_logger(__name__)


class DownloaderPage(DownloadHistoryPageBase):

    def __init__(self, main_window):
        super().__init__(main_window, "Downloader", "History")

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
        self._sync_controls()

    def _start_download(self):
        url = self.url_input.text()
        logger.info("Manual download requested for url=%s", url.strip())
        entry_name = self._next_entry_name(url)

        entry = CancellableDownloadEntry(
            entry_name,
            on_open=self._open_downloaded_webtoon_detail,
            on_cancel=self._cancel_entry_download,
        )
        self.history_layout.insertWidget(0, entry)
        self._register_entry(entry)

        error = self.service.start_download(url, load_library_path(), job_name=entry_name)
        if error:
            logger.warning("Manual download rejected: %s", error)
            self.history_layout.removeWidget(entry)
            entry.deleteLater()
            self._remove_entry(entry)
            self.set_error_text(error)
            return

        self.set_error_text("")
        self.url_input.clear()

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
