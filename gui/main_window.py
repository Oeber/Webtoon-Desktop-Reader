from PySide6.QtWidgets import (
    QMainWindow, QStackedWidget,
    QApplication, QWidget, QHBoxLayout, QPushButton, QVBoxLayout, QMessageBox, QLabel
)

from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPen, QPixmap, QShortcut, Qt
from PySide6.QtCore import QSize
import time

import qtawesome as qta
from app_logging import get_logger
from webtoon_settings_store import get_instance as get_webtoon_settings
from gui.common.styles import SIDEBAR_BUTTON_STYLE, SIDEBAR_STYLE

from gui.library.library_page import LibraryPage
from gui.library.detail_page import DetailPage
from gui.viewer.viewer_page import ViewerPage
from gui.settings.settings_page import SettingsPage
from gui.downloader.downloader_page import DownloaderPage
from gui.downloader.update_page import UpdatePage
from gui.downloader.download_widgets import SpinnerCircle
from gui.search.global_search import GlobalSearchDialog

logger = get_logger(__name__)
APP_TITLE = "Webtoon Desktop Reader"

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        logger.info("Initializing main window")

        self.set_window_context_title()
        self.resize(1400, 900)
        self._suppress_detail_open_until = 0.0
        self.settings_store = get_webtoon_settings()

        self.stack = QStackedWidget()

        self.library  = LibraryPage(self)
        self.detail   = DetailPage(self)
        self.viewer   = ViewerPage(self)
        self.settings = SettingsPage(self)

        self.stack.addWidget(self.library)
        self.stack.addWidget(self.detail)
        self.stack.addWidget(self.viewer)
        self.stack.addWidget(self.settings)

        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Sidebar
        self.sidebar = QWidget()
        self.sidebar.setStyleSheet(SIDEBAR_STYLE)

        self.sidebar_expanded_width = 200
        self.sidebar_collapsed_width = 50
        self.sidebar_open = False

        self.sidebar.setFixedWidth(self.sidebar_collapsed_width)

        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(10, 10, 10, 10)
        sidebar_layout.setSpacing(10)

        icon_color = "#cccccc"

        # Toggle button
        self.toggle_btn = QPushButton()
        self.toggle_btn.setIcon(qta.icon("fa5s.bars", color=icon_color))
        self.toggle_btn.setIconSize(QSize(16, 16))
        self.toggle_btn.clicked.connect(self.toggle_sidebar)
        sidebar_layout.addWidget(self.toggle_btn)

        # Library button
        self.btn_library = QPushButton()
        self.btn_library.setIcon(qta.icon("fa5s.book-open", color=icon_color))
        self.btn_library.setIconSize(QSize(16, 16))
        self.btn_library.clicked.connect(self.open_library)

        self.toggle_btn.setStyleSheet(SIDEBAR_BUTTON_STYLE)
        self.btn_library.setStyleSheet(SIDEBAR_BUTTON_STYLE)

        if not self.sidebar_open:
            self.btn_library.setText("")

        sidebar_layout.addWidget(self.btn_library)
        self.downloader = DownloaderPage(self)
        self.updates = UpdatePage(self)
        self.library.attach_update_service(self.updates.service)
        self.library.attach_manual_download_service(self.downloader.service)
        self.detail.attach_update_service(self.updates.service)
        self.stack.addWidget(self.downloader)
        self.stack.addWidget(self.updates)
        self.btn_downloader = QPushButton()
        self.btn_downloader.setIcon(qta.icon("fa5s.download", color=icon_color))
        self.btn_downloader.setIconSize(QSize(16, 16))
        self.btn_downloader.setStyleSheet(SIDEBAR_BUTTON_STYLE)
        self.btn_downloader.clicked.connect(self.open_downloader)
        sidebar_layout.addWidget(self.btn_downloader)

        self.btn_updates = QPushButton()
        self.btn_updates.setIcon(qta.icon("fa5s.sync", color=icon_color))
        self.btn_updates.setIconSize(QSize(16, 16))
        self.btn_updates.setStyleSheet(SIDEBAR_BUTTON_STYLE)
        self.btn_updates.clicked.connect(self.open_updates)
        sidebar_layout.addWidget(self.btn_updates)

        sidebar_layout.addStretch()

        self.btn_settings = QPushButton()
        self.btn_settings.setIcon(qta.icon("fa5s.cog", color=icon_color))
        self.btn_settings.setIconSize(QSize(16, 16))
        self.btn_settings.setStyleSheet(SIDEBAR_BUTTON_STYLE)
        self.btn_settings.clicked.connect(self.open_settings)
        sidebar_layout.addWidget(self.btn_settings)
        self.btn_settings.setStyleSheet(SIDEBAR_BUTTON_STYLE)


        layout.addWidget(self.sidebar)
        layout.addWidget(self.stack)

        self.setCentralWidget(root)
        self._chapter_loading_overlay = QWidget(self.stack)
        self._chapter_loading_overlay.setStyleSheet("background-color: rgba(0, 0, 0, 140);")
        self._chapter_loading_overlay.hide()
        chapter_overlay_layout = QVBoxLayout(self._chapter_loading_overlay)
        chapter_overlay_layout.setContentsMargins(24, 24, 24, 24)
        chapter_overlay_layout.setSpacing(10)
        chapter_overlay_layout.setAlignment(Qt.AlignCenter)

        self._chapter_loading_spinner = SpinnerCircle(self._chapter_loading_overlay)
        self._chapter_loading_spinner.set_spinning()
        self._chapter_loading_label = QLabel("Loading chapter...")
        self._chapter_loading_label.setAlignment(Qt.AlignCenter)
        self._chapter_loading_label.setStyleSheet("color: #f2f2f2; font-size: 16px; font-weight: 600;")
        self._chapter_loading_detail_label = QLabel("")
        self._chapter_loading_detail_label.setAlignment(Qt.AlignCenter)
        self._chapter_loading_detail_label.setStyleSheet("color: #bdbdbd; font-size: 12px;")

        chapter_overlay_layout.addWidget(self._chapter_loading_spinner, 0, Qt.AlignCenter)
        chapter_overlay_layout.addWidget(self._chapter_loading_label)
        chapter_overlay_layout.addWidget(self._chapter_loading_detail_label)

        self.global_search = GlobalSearchDialog(self)
        self.global_search_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self.global_search_shortcut.setContext(Qt.ApplicationShortcut)
        self.global_search_shortcut.activated.connect(self.global_search.open_dialog)
        self._shutdown_done = False
        self._download_sidebar_jobs = {}
        self._download_sidebar_icon_state = None
        self._download_sidebar_spin = qta.Spin(self.btn_downloader)
        self._connect_download_sidebar_signals(self.downloader.service, "manual")
        self._connect_download_sidebar_signals(self.updates.service, "updates")
        self.viewer.chapter_loading_started.connect(self._on_viewer_chapter_loading_started)
        self.viewer.chapter_loading_finished.connect(self._on_viewer_chapter_loading_finished)
        self._refresh_download_sidebar_indicator()

    def iconSizeHint(self) -> QSize:
        return QSize(60, 90)

    def set_window_context_title(self, webtoon_name: str | None = None):
        title = APP_TITLE if not webtoon_name else f"{APP_TITLE} | {webtoon_name}"
        self.setWindowTitle(title)

    def _clear_new_chapter_marker(self, webtoon, chapter_index: int):
        if webtoon is None or chapter_index < 0 or chapter_index >= len(webtoon.chapters):
            return
        chapter = webtoon.chapters[chapter_index]
        if self.settings_store.get_latest_new_chapter(webtoon.name) == chapter:
            self.settings_store.clear_latest_new_chapter(webtoon.name)

    # ------------------------------------------------------------------ #

    def open_library(self):
        self._hide_chapter_loading_overlay()
        self.set_window_context_title()
        self.library.refresh_dynamic_state()
        self.stack.setCurrentWidget(self.library)

    def open_downloader(self):
        self._hide_chapter_loading_overlay()
        self.set_window_context_title()
        self.stack.setCurrentWidget(self.downloader)

    def open_settings(self):
        self._hide_chapter_loading_overlay()
        self.set_window_context_title()
        self.stack.setCurrentWidget(self.settings)

    def open_detail(self, webtoon, force: bool = False):
        """Show the detail / chapter-list page. Also refreshes progress badges."""
        if not force and time.monotonic() < self._suppress_detail_open_until:
            logger.info("Suppressed detail open for %s", webtoon.name)
            return
        self._hide_chapter_loading_overlay()
        logger.info("Opening detail page for %s", webtoon.name)
        self.library.refresh_progress()
        self.detail.load_webtoon(webtoon, self.library.progress_store)
        self.set_window_context_title(webtoon.name)
        self.stack.setCurrentWidget(self.detail)

    def suppress_detail_open(self, seconds: float):
        logger.info("Suppressing detail open for %.2f seconds", seconds)
        self._suppress_detail_open_until = max(
            self._suppress_detail_open_until,
            time.monotonic() + seconds,
        )

    def open_chapter(self, webtoon, chapter_index: int, scroll_pct: float = 0.0):
        """
        Open viewer at a specific chapter + scroll percentage.
        No continue/restart prompt — caller already decided.
        """
        logger.info(
            "Opening chapter directly for %s index=%d scroll=%.3f",
            webtoon.name,
            chapter_index,
            scroll_pct,
        )
        self._clear_new_chapter_marker(webtoon, chapter_index)
        self.viewer.load_webtoon(webtoon,
                                 start_chapter=chapter_index,
                                 start_scroll=scroll_pct)
        self.set_window_context_title(webtoon.name)
        self.stack.setCurrentWidget(self.viewer)
        self._hide_chapter_loading_overlay()

    def open_chapter_with_prompt(self, webtoon, chapter_index: int):
        """
        Open viewer at a specific chapter and let the viewer
        show the continue/restart dialog if progress exists.
        """
        logger.info("Opening chapter with prompt for %s index=%d", webtoon.name, chapter_index)
        webtoon.path = __import__("os").path.abspath(webtoon.path)
        self._clear_new_chapter_marker(webtoon, chapter_index)
        self.viewer.webtoon = webtoon
        self.viewer._apply_webtoon_settings(webtoon)
        self.viewer._repopulate_chapter_selector()
        # This path goes through the prompt logic
        self.viewer._pending_scroll_pct = 0.0
        opened = self.viewer._load_chapter_with_prompt(chapter_index)
        if not opened:
            self._hide_chapter_loading_overlay()
            return
        self.set_window_context_title(webtoon.name)
        self.stack.setCurrentWidget(self.viewer)
        self._hide_chapter_loading_overlay()

    def open_viewer(self, webtoon):
        """Legacy: open viewer from chapter 0."""
        self.open_chapter(webtoon, 0)

    def open_updates(self):
        logger.info("Opening updates page")
        self._hide_chapter_loading_overlay()
        self.updates.refresh_entries()
        self.set_window_context_title()
        self.stack.setCurrentWidget(self.updates)

    def _position_chapter_loading_overlay(self):
        self._chapter_loading_overlay.setGeometry(self.stack.rect())

    def _show_chapter_loading_overlay(self, webtoon_name: str, chapter: str):
        self._position_chapter_loading_overlay()
        self._chapter_loading_spinner.set_spinning()
        self._chapter_loading_label.setText(f"Loading {chapter}...")
        self._chapter_loading_detail_label.setText(webtoon_name)
        self._chapter_loading_overlay.show()
        self._chapter_loading_overlay.raise_()
        QApplication.processEvents()

    def _hide_chapter_loading_overlay(self):
        self._chapter_loading_overlay.hide()

    def _on_viewer_chapter_loading_started(self, webtoon_name: str, chapter: str):
        if self.stack.currentWidget() is self.viewer:
            return
        self._show_chapter_loading_overlay(webtoon_name, chapter)

    def _on_viewer_chapter_loading_finished(self, webtoon_name: str, chapter: str):
        self._hide_chapter_loading_overlay()
    
    def toggle_sidebar(self):
        if self.sidebar_open:
            self.sidebar.setFixedWidth(self.sidebar_collapsed_width)
            self.btn_library.setText("")
            self.btn_settings.setText("")
            self.btn_updates.setText("")
            self.sidebar_open = False
        else:
            self.sidebar.setFixedWidth(self.sidebar_expanded_width)
            self.btn_library.setText("  Library")
            self.btn_settings.setText("  Settings")
            self.btn_updates.setText("  Updates")
            self.sidebar_open = True
        self._refresh_download_sidebar_indicator()
        logger.info("Sidebar toggled, open=%s", self.sidebar_open)

    def shutdown_background_tasks(self):
        if self._shutdown_done:
            return

        self._shutdown_done = True
        logger.info("Stopping background tasks before app exit")

        try:
            self.downloader.service.shutdown()
        except Exception:
            logger.exception("Failed to shut down downloader service")

        try:
            self.updates.service.shutdown()
        except Exception:
            logger.exception("Failed to shut down update service")

        try:
            self.viewer.shutdown()
        except Exception:
            logger.exception("Failed to shut down viewer")

    def _active_download_summaries(self) -> list[str]:
        active = []
        try:
            active.extend(self.downloader.service.active_download_names())
        except Exception:
            logger.exception("Failed to read downloader activity")
        try:
            active.extend(self.updates.service.active_download_names())
        except Exception:
            logger.exception("Failed to read update activity")
        return active

    def _confirm_close_with_active_downloads(self) -> bool:
        active = self._active_download_summaries()
        if not active:
            return True

        count = len(active)
        if count == 1:
            detail_text = active[0]
        elif count == 2:
            detail_text = ", ".join(active)
        else:
            detail_text = f"{active[0]}, {active[1]}, and {count - 2} more"

        result = QMessageBox.warning(
            self,
            "Downloads in Progress",
            "Downloads are still running.\n\n"
            f"Closing now will cancel {count} active download(s): {detail_text}.\n"
            "The source URL will still be saved for later updates.\n\n"
            "Close anyway?",
            QMessageBox.Close | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        return result == QMessageBox.Close

    def _connect_download_sidebar_signals(self, service, prefix: str):
        service.download_started.connect(
            lambda name, prefix=prefix: self._on_sidebar_download_started(prefix, name)
        )
        service.name_resolved.connect(
            lambda old_name, new_name, prefix=prefix: self._on_sidebar_download_renamed(prefix, old_name, new_name)
        )
        service.progress_changed.connect(
            lambda name, current, total, prefix=prefix: self._on_sidebar_download_progress(prefix, name, current, total)
        )
        service.download_finished.connect(
            lambda name, status, prefix=prefix: self._on_sidebar_download_finished(prefix, name)
        )

    def _sidebar_job_key(self, prefix: str, name: str) -> str:
        return f"{prefix}:{name}"

    def _on_sidebar_download_started(self, prefix: str, name: str):
        self._download_sidebar_jobs[self._sidebar_job_key(prefix, name)] = {
            "name": name,
            "current": 0,
            "total": 0,
        }
        self._refresh_download_sidebar_indicator()

    def _on_sidebar_download_renamed(self, prefix: str, old_name: str, new_name: str):
        old_key = self._sidebar_job_key(prefix, old_name)
        state = self._download_sidebar_jobs.pop(old_key, None)
        if state is None:
            state = {"name": new_name, "current": 0, "total": 0}
        state["name"] = new_name
        self._download_sidebar_jobs[self._sidebar_job_key(prefix, new_name)] = state
        self._refresh_download_sidebar_indicator()

    def _on_sidebar_download_progress(self, prefix: str, name: str, current: int, total: int):
        key = self._sidebar_job_key(prefix, name)
        state = self._download_sidebar_jobs.setdefault(
            key,
            {"name": name, "current": 0, "total": 0},
        )
        state["current"] = max(0, int(current))
        state["total"] = max(0, int(total))
        self._refresh_download_sidebar_indicator()

    def _on_sidebar_download_finished(self, prefix: str, name: str):
        self._download_sidebar_jobs.pop(self._sidebar_job_key(prefix, name), None)
        self._refresh_download_sidebar_indicator()

    def _download_sidebar_totals(self) -> tuple[int, int]:
        current = 0
        total = 0
        for state in self._download_sidebar_jobs.values():
            total += max(0, int(state["total"]))
            current += min(max(0, int(state["current"])), max(0, int(state["total"])))
        return current, total

    def _refresh_download_sidebar_indicator(self):
        active_count = len(self._download_sidebar_jobs)
        current, total = self._download_sidebar_totals()

        if active_count > 0:
            remaining = max(0, total - current)
            icon_state = ("progress", "active")
            if icon_state != self._download_sidebar_icon_state:
                self.btn_downloader.setIcon(
                    qta.icon("fa5s.spinner", color="#f0a500", animation=self._download_sidebar_spin)
                )
                self._download_sidebar_icon_state = icon_state
            if self.sidebar_open:
                if total > 0:
                    self.btn_downloader.setText(f"  Download {current} done, {remaining} left")
                else:
                    self.btn_downloader.setText(f"  Download ({active_count} active)")
            else:
                self.btn_downloader.setText("")
            if total > 0:
                self.btn_downloader.setToolTip(f"{active_count} active download(s): {current} done, {remaining} left")
            else:
                self.btn_downloader.setToolTip(f"{active_count} active download(s)")
            return

        if self._download_sidebar_icon_state != ("idle", None):
            self.btn_downloader.setIcon(qta.icon("fa5s.download", color="#cccccc"))
            self._download_sidebar_icon_state = ("idle", None)
        self.btn_downloader.setToolTip("Open downloader")
        if self.sidebar_open:
            self.btn_downloader.setText("  Download")
        else:
            self.btn_downloader.setText("")

    def closeEvent(self, event):
        if not self._confirm_close_with_active_downloads():
            event.ignore()
            return
        self.shutdown_background_tasks()
        super().closeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_chapter_loading_overlay()
