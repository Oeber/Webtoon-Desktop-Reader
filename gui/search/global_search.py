from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QDialog, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from app_logging import get_logger
from gui.common.styles import INPUT_STYLE
from rapidfuzz import fuzz

logger = get_logger(__name__)


def rank_webtoons(webtoons: list, query: str) -> list[tuple[int, object]]:
    text = (query or "").strip().lower()
    if not text:
        return [(100, webtoon) for webtoon in webtoons]

    scored = []
    for webtoon in webtoons:
        name = webtoon.name.lower()
        score = max(
            fuzz.WRatio(text, name),
            fuzz.partial_ratio(text, name),
            fuzz.token_set_ratio(text, name),
        )
        if score >= 60:
            scored.append((int(score), webtoon))

    scored.sort(key=lambda item: (-item[0], item[1].name.lower()))
    return scored


class GlobalSearchDialog(QDialog):

    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.setWindowTitle("Search")
        self.resize(500, 400)

        layout = QVBoxLayout(self)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Quick Search (Ctrl+K)")
        self.input.setStyleSheet(INPUT_STYLE)
        layout.addWidget(self.input)

        self.results = QListWidget()
        self.results.setIconSize(self.main_window.iconSizeHint())
        self.results.setSpacing(6)
        layout.addWidget(self.results)

        self.input.textChanged.connect(self._update_results)
        self.results.itemClicked.connect(self._open_selected)
        self.results.itemActivated.connect(self._open_selected)

    def open_dialog(self):
        logger.info("Opening global search dialog")
        self.input.clear()
        self._update_results("")
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def _update_results(self, text: str):
        logger.info("Updating global search results for query='%s'", text.strip())
        self.results.clear()

        for score, webtoon in rank_webtoons(self.main_window.library._webtoons, text)[:20]:
            item = QListWidgetItem(webtoon.name)
            item.setData(Qt.UserRole, webtoon)
            item.setToolTip(f"Match score: {score}")

            thumb_path = webtoon.thumbnail
            if thumb_path:
                pixmap = QPixmap(thumb_path)
                if not pixmap.isNull():
                    item.setIcon(QIcon(pixmap))

            self.results.addItem(item)

    def _open_selected(self, item: QListWidgetItem):
        webtoon = item.data(Qt.UserRole)
        logger.info("Global search selected %s", webtoon.name)
        self.main_window.open_detail(webtoon)
        self.close()
