from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout, QScrollArea,
)
from PySide6.QtWidgets import QLineEdit
from PySide6.QtCore import Qt, QTimer

from rapidfuzz import fuzz

from library_manager import scan_library
from thumbnail_store import ThumbnailStore
from progress_store import get_instance as get_progress_store
from gui.library.webtoon_card import WebtoonCard, CARD_WIDTH


LIBRARY_PATH  = "webtoons"
CARD_SPACING  = 16
PAGE_PADDING  = 24


class LibraryPage(QWidget):

    def __init__(self, main_window):
        super().__init__()

        self.main_window   = main_window
        self.thumb_store   = ThumbnailStore()
        self.progress_store = get_progress_store()
        self._webtoons     = []
        self._cards        = []
        self._current_cols = 0

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
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

        self.container = QWidget()
        self.container.setStyleSheet("background-color: #121212;")

        self.grid = QGridLayout(self.container)
        self.grid.setSpacing(CARD_SPACING)
        self.grid.setContentsMargins(PAGE_PADDING, PAGE_PADDING, PAGE_PADDING, PAGE_PADDING)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        #Search
        self.scroll.setWidget(self.container)
        root_layout.addWidget(self.scroll)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search webtoons...")
        self.search.setFixedHeight(36)

        self.search.setStyleSheet("""
        QLineEdit {
            background: #1a1a1a;
            border: 1px solid #333;
            border-radius: 6px;
            padding-left: 10px;
            color: #eee;
        }
        QLineEdit:focus {
            border: 1px solid #666;
        }
        """)

        self.search.textChanged.connect(self._filter_cards)

        root_layout.addWidget(self.search)
        #Debounce for search
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_filter)
        self.search.textChanged.connect(self._schedule_filter)

        self.load_library()

    def load_library(self):
        self._webtoons = scan_library(LIBRARY_PATH, self.thumb_store)
        self._rebuild_grid(self._columns_for_width(self.width()))

    def refresh_progress(self):
        """Call this when returning from the viewer so badges update."""
        for card in self._cards:
            card._refresh_badges()

    def _columns_for_width(self, width: int) -> int:
        available = max(width - PAGE_PADDING * 2, CARD_WIDTH + 16)
        return max(1, available // (CARD_WIDTH + 16 + CARD_SPACING))

    def _rebuild_grid(self, columns: int):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        self._cards = []
        self._current_cols = columns

        for index, webtoon in enumerate(self._webtoons):
            row = index // columns
            col = index % columns

            card = WebtoonCard(
                webtoon,
                thumb_store=self.thumb_store,
                progress_store=self.progress_store,
                on_open=self._open_detail,
            )
            self._cards.append(card)
            self.grid.addWidget(card, row, col, Qt.AlignTop | Qt.AlignLeft)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        new_cols = self._columns_for_width(event.size().width())
        if new_cols != self._current_cols and self._webtoons:
            self._rebuild_grid(new_cols)

    def _open_detail(self, webtoon):
        self.main_window.open_detail(webtoon)

    def _filter_cards(self, text: str):

        text = text.strip().lower()

        visible_cards = []

        for card in self._cards:

            name = card.webtoon.name.lower()

            if not text:
                score = 100
            else:
                score = fuzz.partial_ratio(text, name)

            visible = score >= 60

            card.setVisible(visible)

            if visible:
                visible_cards.append(card)

        # re-pack visible cards
        for i, card in enumerate(visible_cards):
            row = i // self._current_cols
            col = i % self._current_cols
            self.grid.addWidget(card, row, col)

    def _filter_cards(self, text: str):

        text = text.strip().lower()

        visible_cards = []

        for card in self._cards:

            name = card.webtoon.name.lower()

            if not text:
                score = 100
            else:
                score = fuzz.partial_ratio(text, name)

            visible = score >= 60

            card.setVisible(visible)

            if visible:
                visible_cards.append(card)

        # re-pack visible cards
        for i, card in enumerate(visible_cards):
            row = i // self._current_cols
            col = i % self._current_cols
            self.grid.addWidget(card, row, col)

    def _schedule_filter(self, text):
        self._pending_search = text
        self._search_timer.start(150)

    def _apply_filter(self):
        text = self._pending_search.strip().lower()

        visible_cards = []

        for card in self._cards:

            name = card.webtoon.name.lower()

            if not text:
                score = 100
            else:
                score = fuzz.partial_ratio(text, name)

            visible = score >= 60

            card.setVisible(visible)

            if visible:
                visible_cards.append(card)

        for i, card in enumerate(visible_cards):
            row = i // self._current_cols
            col = i % self._current_cols
            self.grid.addWidget(card, row, col)