from PySide6.QtWidgets import (
    QMainWindow, QStackedWidget, QDialog,
    QVBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
    QWidget, QHBoxLayout, QPushButton
)

from PySide6.QtGui import QShortcut, QKeySequence, QPixmap, QIcon, Qt
from PySide6.QtCore import QSize

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
        self.input.setPlaceholderText("Quick Search (CTRL + K)")
        layout.addWidget(self.input)

        self.results = QListWidget()
        self.results.setIconSize(QSize(60, 90))
        self.results.setSpacing(6)
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
        thumb_store = self.main_window.library.thumb_store

        scored = []

        for w in webtoons:

            name = w.name.lower()

            score = fuzz.partial_ratio(text, name) if text else 100

            if score >= 60:
                scored.append((score, w))

        scored.sort(reverse=True, key=lambda x: x[0])

        for score, w in scored[:20]:

            item = QListWidgetItem(w.name)

            # store the real object correctly
            item.setData(Qt.UserRole, w)

            thumb = thumb_store.get(w.name)

            if thumb:
                pixmap = QPixmap(thumb)
                item.setIcon(QIcon(pixmap))

            self.results.addItem(item)

    def _open(self, item):

        webtoon = item.data(Qt.UserRole)

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

        # Toggle button
        self.toggle_btn = QPushButton("☰")
        self.toggle_btn.clicked.connect(self.toggle_sidebar)
        sidebar_layout.addWidget(self.toggle_btn)

        # Library button
        self.btn_library = QPushButton("Library")
        self.btn_library.clicked.connect(
            lambda: self.stack.setCurrentWidget(self.library)
        ) 

        self.toggle_btn.setStyleSheet(button_style)
        self.btn_library.setStyleSheet(button_style)

        if not self.sidebar_open:
            self.btn_library.setText("")

        sidebar_layout.addWidget(self.btn_library)
        sidebar_layout.addStretch()

        layout.addWidget(self.sidebar)
        layout.addWidget(self.stack)

        self.setCentralWidget(root)
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

    def toggle_sidebar(self):
        if self.sidebar_open:
            self.sidebar.setFixedWidth(self.sidebar_collapsed_width)
            self.btn_library.setText("")  # hide text
            self.sidebar_open = False
        else:
            self.sidebar.setFixedWidth(self.sidebar_expanded_width)
            self.btn_library.setText("Library")
            self.sidebar_open = True