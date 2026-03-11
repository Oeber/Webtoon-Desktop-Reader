from PySide6.QtWidgets import (
    QMainWindow, QStackedWidget, QDialog,
    QVBoxLayout, QLineEdit, QListWidget, QListWidgetItem
)
from PySide6.QtGui import QShortcut, QKeySequence
from rapidfuzz import fuzz

from gui.library.library_page import LibraryPage
from gui.library.detail_page import DetailPage
from gui.viewer.viewer_page import ViewerPage
from gui.settings.settings_page import SettingsPage

class GlobalSearch(QDialog):

    def __init__(self, main_window):
        super().__init__(main_window)

        self.main_window = main_window
        self.setWindowTitle("Search")
        self.resize(500, 400)

        layout = QVBoxLayout(self)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Search webtoons...")
        layout.addWidget(self.input)

        self.results = QListWidget()
        layout.addWidget(self.results)

        self.input.textChanged.connect(self._update_results)
        self.results.itemActivated.connect(self._open)

    def open_dialog(self):
        self.input.clear()
        self.results.clear()
        self.show()
        self.raise_()
        self.input.setFocus()

    def _update_results(self, text):

        text = text.lower()

        self.results.clear()

        webtoons = self.main_window.library._webtoons

        scored = []

        for w in webtoons:

            name = w.name.lower()

            score = fuzz.partial_ratio(text, name) if text else 100

            if score >= 60:
                scored.append((score, w))

        scored.sort(reverse=True, key=lambda x: x[0])

        for score, w in scored[:20]:
            item = QListWidgetItem(w.name)
            item.setData(1, w)
            self.results.addItem(item)

    def _open(self, item):
        webtoon = item.data(1)
        self.main_window.open_detail(webtoon)
        self.close()

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
        #attach ctrl+k to search
        self.global_search = GlobalSearch(self)

        QShortcut(QKeySequence("Ctrl+K"), self).activated.connect(
            self.global_search.open_dialog
        )

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