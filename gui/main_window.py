from PySide6.QtWidgets import QMainWindow, QStackedWidget

from gui.library.library_page import LibraryPage
from gui.library.detail_page import DetailPage
from gui.viewer.viewer_page import ViewerPage
from gui.settings.settings_page import SettingsPage


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()

        self.resize(1400, 900)

        self.stack = QStackedWidget()

        self.library  = LibraryPage(self)
        self.detail   = DetailPage(self)
        self.viewer   = ViewerPage(self)
        self.settings = SettingsPage(self)

        self.stack.addWidget(self.library)
        self.stack.addWidget(self.detail)
        self.stack.addWidget(self.viewer)
        self.stack.addWidget(self.settings)

        self.setCentralWidget(self.stack)

    # ------------------------------------------------------------------ #

    def open_detail(self, webtoon):
        """Show the detail / chapter-list page. Also refreshes progress badges."""
        self.library.refresh_progress()
        self.detail.load_webtoon(webtoon, self.library.progress_store)
        self.stack.setCurrentWidget(self.detail)

    def open_chapter(self, webtoon, chapter_index: int, scroll_pct: float = 0.0):
        """
        Open viewer at a specific chapter + scroll percentage.
        No continue/restart prompt — caller already decided.
        """
        self.viewer.load_webtoon(webtoon,
                                 start_chapter=chapter_index,
                                 start_scroll=scroll_pct)
        self.stack.setCurrentWidget(self.viewer)

    def open_chapter_with_prompt(self, webtoon, chapter_index: int):
        """
        Open viewer at a specific chapter and let the viewer
        show the continue/restart dialog if progress exists.
        """
        webtoon.path = __import__("os").path.abspath(webtoon.path)
        self.viewer.webtoon = webtoon
        self.viewer.chapter_selector.blockSignals(True)
        self.viewer.chapter_selector.clear()
        self.viewer.chapter_selector.addItems(webtoon.chapters)
        self.viewer.chapter_selector.blockSignals(False)
        # This path goes through the prompt logic
        self.viewer._pending_scroll_pct = 0.0
        self.viewer._load_chapter_with_prompt(chapter_index)
        self.stack.setCurrentWidget(self.viewer)

    def open_viewer(self, webtoon):
        """Legacy: open viewer from chapter 0."""
        self.open_chapter(webtoon, 0)