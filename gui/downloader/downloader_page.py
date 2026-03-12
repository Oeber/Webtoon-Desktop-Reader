from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton

from app_logging import get_logger
from gui.downloader.download_widgets import BTN_STYLE, INPUT_STYLE, DownloadEntry
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

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setStyleSheet(BTN_STYLE)
        self.cancel_btn.setFixedWidth(80)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.service.cancel_download)

        row.addWidget(self.url_input)
        row.addWidget(self.download_btn)
        row.addWidget(self.cancel_btn)
        self.layout().insertLayout(1, row)

    def _start_download(self):
        url = self.url_input.text()
        logger.info("Manual download requested for url=%s", url.strip())
        slug = url.strip().strip("'\"").rstrip("/").split("/")[-1]
        entry_name = slug or "download"

        entry = DownloadEntry(entry_name, on_open=self._open_downloaded_webtoon_detail)
        self.history_layout.insertWidget(0, entry)
        self._active_entry = entry

        error = self.service.start_download(url, load_library_path())
        if error:
            logger.warning("Manual download rejected: %s", error)
            self.history_layout.removeWidget(entry)
            entry.deleteLater()
            self._active_entry = None
            self.error_label.setText(error)
            return

        self.error_label.setText("")
        self.url_input.clear()

    def _on_download_started(self):
        logger.info("Manual download started")
        self.download_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)

    def _on_download_finished(self, name: str, status: str):
        logger.info("Manual download finished for %s with status=%s", name, status)
        self.download_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self._active_entry = None

    def _open_downloaded_webtoon_detail(self, webtoon_name: str):
        logger.info("Opening detail for manually downloaded webtoon %s", webtoon_name)
        webtoon = self.service.build_webtoon_from_folder(load_library_path(), webtoon_name)
        if webtoon is not None:
            self.main_window.open_detail(webtoon)
