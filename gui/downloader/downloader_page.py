from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget

from gui.downloader.download_service import DownloadService
from gui.downloader.download_widgets import BTN_STYLE, INPUT_STYLE, DownloadEntry
from gui.settings.settings_page import load_library_path


class DownloaderPage(QWidget):

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background-color: #121212;")

        self.service = DownloadService(self)
        self._active_entry = None

        self.service.status_changed.connect(self._on_status_changed)
        self.service.name_resolved.connect(self._on_name_resolved)
        self.service.progress_changed.connect(self._on_progress_changed)
        self.service.thumbnail_resolved.connect(self._on_thumbnail_resolved)
        self.service.download_started.connect(self._on_download_started)
        self.service.download_finished.connect(self._on_download_finished)
        self.service.library_changed.connect(self._on_library_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(20)
        layout.setAlignment(Qt.AlignTop)

        self.title_label = QLabel("Downloader")
        self.title_label.setStyleSheet(
            "color: #ffffff; font-size: 20px; font-weight: bold; background: transparent;"
        )
        layout.addWidget(self.title_label)

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
        layout.addLayout(row)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #f44336; font-size: 12px; background: transparent;")
        layout.addWidget(self.error_label)

        self.history_label = QLabel("History")
        self.history_label.setStyleSheet("color: #aaaaaa; font-size: 12px; background: transparent;")
        layout.addWidget(self.history_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("""
            QScrollArea { border: none; background-color: #121212; }
            QScrollBar:vertical {
                background: #1a1a1a; width: 8px; border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #444; border-radius: 4px; min-height: 30px;
            }
            QScrollBar::handle:vertical:hover { background: #666; }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical { height: 0px; }
        """)

        self.history_container = QWidget()
        self.history_container.setStyleSheet("background-color: #121212;")
        self.history_layout = QVBoxLayout(self.history_container)
        self.history_layout.setContentsMargins(0, 0, 0, 0)
        self.history_layout.setSpacing(8)
        self.history_layout.setAlignment(Qt.AlignTop)

        self.scroll.setWidget(self.history_container)
        layout.addWidget(self.scroll)

    def _start_download(self):
        url = self.url_input.text()
        slug = url.strip().strip("'\"").rstrip("/").split("/")[-1]
        entry_name = slug or "download"

        entry = DownloadEntry(entry_name, on_open=self._open_downloaded_webtoon_detail)
        self.history_layout.insertWidget(0, entry)
        self._active_entry = entry

        error = self.service.start_download(url, load_library_path())
        if error:
            self.history_layout.removeWidget(entry)
            entry.deleteLater()
            self._active_entry = None
            self.error_label.setText(error)
            return

        self.error_label.setText("")
        self.url_input.clear()

    def _on_download_started(self):
        self.download_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)

    def _on_download_finished(self, name: str, status: str):
        self.download_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self._active_entry = None

    def _on_library_changed(self):
        self.main_window.library.load_library()

    def _on_status_changed(self, name: str, status: str):
        if self._active_entry and self._active_entry.name == name:
            self._active_entry.set_status(status)
            if status == "Completed":
                thumb_path = self.service.preferred_thumbnail_for(name)
                if thumb_path:
                    self._active_entry.set_thumbnail(thumb_path)

    def _on_name_resolved(self, name: str):
        if self._active_entry:
            self._active_entry.name = name
            self._active_entry.name_label.setText(name)

            thumb_path = self.service.preferred_thumbnail_for(name)
            if thumb_path:
                self._active_entry.set_thumbnail(thumb_path)

    def _on_progress_changed(self, name: str, current: int, total: int):
        if self._active_entry and self._active_entry.name == name:
            self._active_entry.set_progress(current, total)

    def _on_thumbnail_resolved(self, name: str, path: str):
        if self._active_entry and self._active_entry.name == name and path:
            self._active_entry.set_thumbnail(path)

    def _open_downloaded_webtoon_detail(self, webtoon_name: str):
        webtoon = self.service.build_webtoon_from_folder(load_library_path(), webtoon_name)
        if webtoon is not None:
            self.main_window.open_detail(webtoon)
