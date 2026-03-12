from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from core.app_logging import get_logger
from gui.common.styles import (
    ERROR_LABEL_STYLE,
    PAGE_BG_STYLE,
    PAGE_TITLE_STYLE,
    SCROLL_AREA_STYLE,
    SECTION_LABEL_STYLE,
)
from gui.downloader.download_service import DownloadService

logger = get_logger(__name__)


class DownloadHistoryPageBase(QWidget):

    def __init__(self, main_window, title_text: str, section_text: str, history_kind: str):
        super().__init__()
        logger.info("Initializing download history page base: %s", title_text)
        self.main_window = main_window
        self.service = DownloadService(self, history_kind=history_kind)
        self._entries_by_name = {}

        self._connect_service_signals()
        self._build_page_shell(title_text, section_text)

    def _connect_service_signals(self):
        self.service.status_changed.connect(self._on_status_changed)
        self.service.name_resolved.connect(self._on_name_resolved)
        self.service.progress_changed.connect(self._on_progress_changed)
        self.service.thumbnail_resolved.connect(self._on_thumbnail_resolved)
        self.service.download_started.connect(self._on_download_started)
        self.service.download_finished.connect(self._on_download_finished)
        self.service.library_changed.connect(self._on_library_changed)

    def _build_page_shell(self, title_text: str, section_text: str):
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(PAGE_BG_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(20)
        layout.setAlignment(Qt.AlignTop)

        title = QLabel(title_text)
        title.setStyleSheet(PAGE_TITLE_STYLE)
        layout.addWidget(title)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet(ERROR_LABEL_STYLE)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        section_label = QLabel(section_text)
        section_label.setStyleSheet(SECTION_LABEL_STYLE)
        layout.addWidget(section_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet(SCROLL_AREA_STYLE)

        self.history_container = QWidget()
        self.history_container.setStyleSheet(PAGE_BG_STYLE)
        self.history_layout = QVBoxLayout(self.history_container)
        self.history_layout.setContentsMargins(0, 0, 0, 0)
        self.history_layout.setSpacing(8)
        self.history_layout.setAlignment(Qt.AlignTop)

        self.scroll.setWidget(self.history_container)
        layout.addWidget(self.scroll)

    def set_error_text(self, text: str):
        self.error_label.setText(text)
        self.error_label.setVisible(bool(text.strip()))

    def _register_entry(self, entry):
        self._entries_by_name[entry.name] = entry

    def _remove_entry(self, entry):
        if entry is None:
            return
        if self._entries_by_name.get(entry.name) is entry:
            self._entries_by_name.pop(entry.name, None)

    def _entry_for(self, name: str):
        return self._entries_by_name.get(name)

    def _on_status_changed(self, name: str, status: str):
        entry = self._entry_for(name)
        if entry is not None:
            entry.set_status(status)
            if status == "Completed":
                thumb_path = self.service.preferred_thumbnail_for(name)
                if thumb_path:
                    entry.set_thumbnail(thumb_path)

    def _on_name_resolved(self, old_name: str, name: str):
        entry = self._entries_by_name.pop(old_name, None)
        if entry is None:
            for current_name, current_entry in list(self._entries_by_name.items()):
                if current_entry.name == old_name:
                    entry = self._entries_by_name.pop(current_name, None)
                    break
        if entry is not None:
            entry.name = name
            entry.name_label.setText(name)
            self._entries_by_name[name] = entry

            thumb_path = self.service.preferred_thumbnail_for(name)
            if thumb_path:
                entry.set_thumbnail(thumb_path)

    def _on_progress_changed(self, name: str, current: int, total: int):
        entry = self._entry_for(name)
        if entry is not None:
            entry.set_progress(current, total)

    def _on_thumbnail_resolved(self, name: str, path: str):
        entry = self._entry_for(name)
        if entry is not None and path:
            entry.set_thumbnail(path)

    def _on_library_changed(self, name: str):
        logger.info("Download service reported library_changed for %s", name)

    def _on_download_started(self, name: str):
        raise NotImplementedError

    def _on_download_finished(self, name: str, status: str):
        raise NotImplementedError
