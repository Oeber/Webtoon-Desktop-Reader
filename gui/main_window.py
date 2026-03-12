from PySide6.QtWidgets import (
    QMainWindow, QStackedWidget,
    QWidget, QHBoxLayout, QPushButton, QVBoxLayout
)

from PySide6.QtGui import QShortcut, QKeySequence, Qt
from PySide6.QtCore import QSize
import time

import qtawesome as qta
from app_logging import get_logger

from gui.library.library_page import LibraryPage
from gui.library.detail_page import DetailPage
from gui.viewer.viewer_page import ViewerPage
from gui.settings.settings_page import SettingsPage
from gui.downloader.downloader_page import DownloaderPage
from gui.downloader.update_page import UpdatePage
from gui.search.global_search import GlobalSearchDialog

logger = get_logger(__name__)

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        logger.info("Initializing main window")

        self.resize(1400, 900)
        self._suppress_detail_open_until = 0.0

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
        self.sidebar.setStyleSheet("""
            background-color: #1e1e1e;
        """)
        button_style = """
            QPushButton {
                background-color: transparent;
                color: #cccccc;
                border: none;
                padding: 8px;
                text-align: left;
                border-radius: 6px;
            }

            QPushButton:hover {
                background-color: #2a2a2a;
            }

            QPushButton:pressed {
                background-color: #333333;
            }
            """

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
        self.btn_library.clicked.connect(
            lambda: self.stack.setCurrentWidget(self.library)
        )

        self.toggle_btn.setStyleSheet(button_style)
        self.btn_library.setStyleSheet(button_style)

        if not self.sidebar_open:
            self.btn_library.setText("")

        sidebar_layout.addWidget(self.btn_library)
        self.downloader = DownloaderPage(self)
        self.updates = UpdatePage(self)
        self.library.attach_update_service(self.updates.service)
        self.detail.attach_update_service(self.updates.service)
        self.stack.addWidget(self.downloader)
        self.stack.addWidget(self.updates)
        self.btn_downloader = QPushButton()
        self.btn_downloader.setIcon(qta.icon("fa5s.download", color=icon_color))
        self.btn_downloader.setIconSize(QSize(16, 16))
        self.btn_downloader.setStyleSheet(button_style)
        self.btn_downloader.clicked.connect(
            lambda: self.stack.setCurrentWidget(self.downloader)
        )
        sidebar_layout.addWidget(self.btn_downloader)

        self.btn_updates = QPushButton()
        self.btn_updates.setIcon(qta.icon("fa5s.sync", color=icon_color))
        self.btn_updates.setIconSize(QSize(16, 16))
        self.btn_updates.setStyleSheet(button_style)
        self.btn_updates.clicked.connect(self.open_updates)
        sidebar_layout.addWidget(self.btn_updates)

        sidebar_layout.addStretch()

        self.btn_settings = QPushButton()
        self.btn_settings.setIcon(qta.icon("fa5s.cog", color=icon_color))
        self.btn_settings.setIconSize(QSize(16, 16))
        self.btn_settings.setStyleSheet(button_style)
        self.btn_settings.clicked.connect(
            lambda: self.stack.setCurrentWidget(self.settings)
        )
        sidebar_layout.addWidget(self.btn_settings)
        self.btn_settings.setStyleSheet(button_style)


        layout.addWidget(self.sidebar)
        layout.addWidget(self.stack)

        self.setCentralWidget(root)
        self.global_search = GlobalSearchDialog(self)
        self.global_search_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self.global_search_shortcut.setContext(Qt.ApplicationShortcut)
        self.global_search_shortcut.activated.connect(self.global_search.open_dialog)

    def iconSizeHint(self) -> QSize:
        return QSize(60, 90)

    # ------------------------------------------------------------------ #

    def open_detail(self, webtoon):
        """Show the detail / chapter-list page. Also refreshes progress badges."""
        if time.monotonic() < self._suppress_detail_open_until:
            logger.info("Suppressed detail open for %s", webtoon.name)
            return
        logger.info("Opening detail page for %s", webtoon.name)
        self.library.refresh_progress()
        self.detail.load_webtoon(webtoon, self.library.progress_store)
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
        self.viewer.load_webtoon(webtoon,
                                 start_chapter=chapter_index,
                                 start_scroll=scroll_pct)
        self.stack.setCurrentWidget(self.viewer)

    def open_chapter_with_prompt(self, webtoon, chapter_index: int):
        """
        Open viewer at a specific chapter and let the viewer
        show the continue/restart dialog if progress exists.
        """
        logger.info("Opening chapter with prompt for %s index=%d", webtoon.name, chapter_index)
        webtoon.path = __import__("os").path.abspath(webtoon.path)
        self.viewer.webtoon = webtoon
        self.viewer._apply_webtoon_settings(webtoon)
        self.viewer._repopulate_chapter_selector()
        # This path goes through the prompt logic
        self.viewer._pending_scroll_pct = 0.0
        self.viewer._load_chapter_with_prompt(chapter_index)
        self.stack.setCurrentWidget(self.viewer)

    def open_viewer(self, webtoon):
        """Legacy: open viewer from chapter 0."""
        self.open_chapter(webtoon, 0)

    def open_updates(self):
        logger.info("Opening updates page")
        self.updates.refresh_entries()
        self.stack.setCurrentWidget(self.updates)
    
    def toggle_sidebar(self):
        if self.sidebar_open:
            self.sidebar.setFixedWidth(self.sidebar_collapsed_width)
            self.btn_library.setText("")
            self.btn_settings.setText("")
            self.btn_downloader.setText("")
            self.btn_updates.setText("")
            self.sidebar_open = False
        else:
            self.sidebar.setFixedWidth(self.sidebar_expanded_width)
            self.btn_library.setText("  Library")
            self.btn_settings.setText("  Settings")
            self.btn_downloader.setText("  Download")
            self.btn_updates.setText("  Updates")
            self.sidebar_open = True
        logger.info("Sidebar toggled, open=%s", self.sidebar_open)
