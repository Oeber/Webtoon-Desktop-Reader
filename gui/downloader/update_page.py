import os
import time
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from gui.downloader.download_service import DownloadService
from gui.downloader.download_widgets import BTN_STYLE, DownloadEntry
from gui.settings.settings_page import load_library_path
from library_manager import scan_library
from webtoon_settings_store import get_instance as get_webtoon_settings


UPDATE_COOLDOWN_SECONDS = 30


class UpdateEntry(DownloadEntry):

    def __init__(self, webtoon_name: str, source_url: str, last_update_at: int | None, on_update):
        super().__init__(webtoon_name)
        self.source_url = source_url
        self.last_update_at = last_update_at
        self.on_update = on_update
        self.setProperty("clickable", False)
        self.setCursor(Qt.ArrowCursor)

        self.sub_label.setWordWrap(True)
        self._refresh_sub_label()
        self.sub_label.show()

        self.update_btn = QPushButton("Update")
        self.update_btn.setStyleSheet(BTN_STYLE)
        self.update_btn.setFixedWidth(100)
        self.update_btn.clicked.connect(lambda: self.on_update(self))

        controls = QWidget()
        controls.setStyleSheet("background: transparent; border: none;")
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(8)
        controls_layout.addWidget(self.status_label, alignment=Qt.AlignRight)
        controls_layout.addWidget(self.update_btn, alignment=Qt.AlignRight)

        self.layout().removeWidget(self.status_label)
        self.layout().addWidget(controls)

    def set_status(self, status: str):
        super().set_status(status)
        self.setProperty("clickable", False)
        self.setCursor(Qt.ArrowCursor)
        self.style().unpolish(self)
        self.style().polish(self)
        self._refresh_sub_label()

    def set_last_update_at(self, timestamp: int):
        self.last_update_at = int(timestamp)
        self._refresh_sub_label()

    def cooldown_remaining(self) -> int:
        if self.last_update_at is None:
            return 0
        elapsed = int(time.time()) - int(self.last_update_at)
        return max(0, UPDATE_COOLDOWN_SECONDS - elapsed)

    def _refresh_sub_label(self):
        if self.last_update_at is None:
            last_updated = "Last updated: Never"
        else:
            stamp = datetime.fromtimestamp(int(self.last_update_at)).strftime("%Y-%m-%d %H:%M:%S")
            last_updated = f"Last updated: {stamp}"
        self.sub_label.setText(f"{self.source_url}\n{last_updated}")
        self.sub_label.show()


class UpdatePage(QWidget):

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.settings_store = get_webtoon_settings()
        self.service = DownloadService(self)
        self._active_entry = None
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh_entries)
        self._cooldown_timer = QTimer(self)
        self._cooldown_timer.timeout.connect(self._sync_update_buttons)
        self._cooldown_timer.start(1000)

        self.service.status_changed.connect(self._on_status_changed)
        self.service.name_resolved.connect(self._on_name_resolved)
        self.service.progress_changed.connect(self._on_progress_changed)
        self.service.thumbnail_resolved.connect(self._on_thumbnail_resolved)
        self.service.download_started.connect(self._on_download_started)
        self.service.download_finished.connect(self._on_download_finished)
        self.service.library_changed.connect(self._on_library_changed)

        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background-color: #121212;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(20)
        layout.setAlignment(Qt.AlignTop)

        title = QLabel("Updates")
        title.setStyleSheet("color: #ffffff; font-size: 20px; font-weight: bold; background: transparent;")
        layout.addWidget(title)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #f44336; font-size: 12px; background: transparent;")
        layout.addWidget(self.error_label)

        history_label = QLabel("Saved source URLs")
        history_label.setStyleSheet("color: #aaaaaa; font-size: 12px; background: transparent;")
        layout.addWidget(history_label)

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

        self.refresh_entries()

    def showEvent(self, event):
        super().showEvent(event)
        if not self.service.is_busy():
            self.refresh_entries()

    def refresh_entries(self):
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
            self._sync_update_buttons()
            return

        error = self.service.start_download(
            entry.source_url,
            load_library_path(),
            preferred_name=entry.name,
        )
        if error:
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
        self._refresh_timer.stop()
        self._sync_update_buttons()

    def _on_download_finished(self, name: str, status: str):
        if status == "Completed":
            timestamp = int(time.time())
            self.settings_store.set_last_update_at(name, timestamp)
            if self._active_entry and self._active_entry.name == name:
                self._active_entry.set_last_update_at(timestamp)
        self._sync_update_buttons()
        self._active_entry = None
        self._refresh_timer.start(2500)

    def _on_library_changed(self):
        if self.isVisible():
            self._refresh_timer.stop()
            self._refresh_timer.start(0)

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
