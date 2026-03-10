import os

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QGridLayout,
    QScrollArea
)

from library_manager import scan_library
from gui.library.webtoon_card import WebtoonCard


LIBRARY_PATH = "webtoons"


class LibraryPage(QWidget):

    def __init__(self, main_window):
        super().__init__()

        self.main_window = main_window

        self.layout = QVBoxLayout(self)

        # scroll container
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)

        self.container = QWidget()
        self.grid = QGridLayout(self.container)

        self.scroll.setWidget(self.container)

        self.layout.addWidget(self.scroll)

        self.load_library()

    def load_library(self):

        webtoons = scan_library(LIBRARY_PATH)

        columns = 4
        row = 0
        col = 0

        for webtoon in webtoons:

            card = WebtoonCard(webtoon)

            # click handler
            card.mousePressEvent = lambda event, w=webtoon: self.open_webtoon(w)

            self.grid.addWidget(card, row, col)

            col += 1
            if col >= columns:
                col = 0
                row += 1

    def open_webtoon(self, webtoon):

        self.main_window.open_viewer(webtoon)