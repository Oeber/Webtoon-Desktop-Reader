from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout, QScrollArea,
)
from PySide6.QtWidgets import QApplication, QLineEdit
from PySide6.QtCore import Qt, QTimer
import time

from library_manager import scan_library
from progress_store import get_instance as get_progress_store
from webtoon_settings_store import get_instance as get_webtoon_settings
from gui.library.webtoon_card import WebtoonCard, CARD_WIDTH
from gui.search.global_search import rank_webtoons
from gui.settings.settings_page import load_library_path


CARD_SPACING  = 16
PAGE_PADDING  = 24
UPDATE_COOLDOWN_SECONDS = 30


class LibraryPage(QWidget):

    def __init__(self, main_window):
        super().__init__()

        self.main_window   = main_window
        self.progress_store = get_progress_store()
        self.settings_store = get_webtoon_settings()
        self._webtoons     = []
        self._cards        = []
        self._current_cols = 0
        self._pending_search = ""
        self._update_service = None
        self._active_update_name = None
        self._ignore_open_until = 0.0
        self._block_input_until = 0.0
        self._pending_reload = False

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

        root_layout.addWidget(self.search)
        #Debounce for search
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_filter)
        self.search.textChanged.connect(self._schedule_filter)

        self._cooldown_timer = QTimer(self)
        self._cooldown_timer.timeout.connect(self._sync_update_controls)
        self._cooldown_timer.start(1000)

        self._input_blocker = QWidget(self)
        self._input_blocker.hide()
        self._input_blocker.setStyleSheet("background: transparent;")

        self.load_library()
 
    def load_library(self):
        self._pending_reload = False
        self._webtoons = scan_library(load_library_path(), self.settings_store)
        self._rebuild_grid(self._columns_for_width(self.width()))

    def showEvent(self, event):
        super().showEvent(event)
        if self._pending_reload and (self._update_service is None or not self._update_service.is_busy()):
            self.load_library()

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
                item.widget().deleteLater()

        self._cards = []
        self._current_cols = columns

        for index, webtoon in enumerate(self._webtoons):
            row = index // columns
            col = index % columns

            card = WebtoonCard(
                webtoon,
                settings_store=self.settings_store,
                progress_store=self.progress_store,
                on_open=self._open_detail,
                on_changed=self._reload_after_edit,
                on_update=self._start_update,
            )
            self._cards.append(card)
            self.grid.addWidget(card, row, col, Qt.AlignTop | Qt.AlignLeft)

        self._sync_update_controls()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._input_blocker.setGeometry(self.rect())
        if self._input_blocker.isVisible():
            self._input_blocker.raise_()
        new_cols = self._columns_for_width(event.size().width())
        if new_cols != self._current_cols and self._webtoons:
            self._rebuild_grid(new_cols)

    def _open_detail(self, webtoon):
        if time.monotonic() < self._ignore_open_until:
            return
        self.main_window.open_detail(webtoon)

    def _reload_after_edit(self):
        self.load_library()
        self._apply_filter()

    def attach_update_service(self, service):
        if self._update_service is service:
            return

        self._update_service = service
        self._update_service.status_changed.connect(self._on_update_status_changed)
        self._update_service.progress_changed.connect(self._on_update_progress_changed)
        self._update_service.download_started.connect(self._on_update_started)
        self._update_service.download_finished.connect(self._on_update_finished)
        self._update_service.library_changed.connect(self._on_update_library_changed)
        self._sync_update_controls()

    def _start_update(self, webtoon_name: str):
        if self._update_service is None:
            return

        source_url = self.settings_store.get_source_url(webtoon_name)
        if not source_url:
            return

        remaining = self._cooldown_remaining(webtoon_name)
        if remaining > 0:
            self._sync_update_controls()
            return

        error = self._update_service.start_download(
            source_url,
            load_library_path(),
            preferred_name=webtoon_name,
        )
        if error:
            self._sync_update_controls()
            return

        self._active_update_name = webtoon_name
        self._suppress_card_open(1.0)
        self._block_library_input(1.0)
        self.main_window.suppress_detail_open(1.0)
        self._sync_update_controls()

    def _cooldown_remaining(self, webtoon_name: str) -> int:
        last_update_at = self.settings_store.get_last_update_at(webtoon_name)
        if last_update_at is None:
            return 0
        elapsed = int(time.time()) - int(last_update_at)
        return max(0, UPDATE_COOLDOWN_SECONDS - elapsed)

    def _sync_update_controls(self):
        busy = self._update_service.is_busy() if self._update_service is not None else False

        for card in self._cards:
            has_source = bool(self.settings_store.get_source_url(card.webtoon.name))
            card.set_update_available(has_source)
            if not has_source:
                continue

            if busy and card.webtoon.name == self._active_update_name:
                card.set_update_enabled(False, "Update in progress")
                card.set_update_status("Downloading")
                continue

            if busy:
                card.set_update_enabled(False, "Another update is already running")
                card.set_update_status("Ready")
                continue

            remaining = self._cooldown_remaining(card.webtoon.name)
            if remaining > 0:
                card.set_update_enabled(
                    False,
                    f"Wait {remaining}s before updating again",
                    cooldown_text=f"{remaining}s",
                )
            else:
                card.set_update_enabled(True, "Update this webtoon")
            card.set_update_status("Ready")

    def _on_update_started(self):
        self._sync_update_controls()

    def _on_update_finished(self, name: str, status: str):
        if status == "Completed":
            self.settings_store.set_last_update_at(name, int(time.time()))
        self._suppress_card_open(2.0)
        self._block_library_input(2.0)
        self.main_window.suppress_detail_open(2.0)
        self._active_update_name = None
        self._sync_update_controls()

    def _on_update_library_changed(self):
        self._suppress_card_open(2.0)
        self._block_library_input(2.0)
        self.main_window.suppress_detail_open(2.0)
        if self.isVisible() and self._active_update_name:
            if not self._refresh_updated_webtoon(self._active_update_name):
                self.load_library()
        else:
            self._pending_reload = True

    def _on_update_status_changed(self, name: str, status: str):
        card = self._card_for(name)
        if card is None:
            return
        if status == "Completed":
            card.set_update_progress(1, 1)
        card.set_update_status(status)

    def _on_update_progress_changed(self, name: str, current: int, total: int):
        card = self._card_for(name)
        if card is not None:
            card.set_update_progress(current, total)

    def _card_for(self, webtoon_name: str) -> WebtoonCard | None:
        for card in self._cards:
            if card.webtoon.name == webtoon_name:
                return card
        return None

    def _refresh_updated_webtoon(self, webtoon_name: str) -> bool:
        if self._update_service is None:
            return False

        updated = self._update_service.build_webtoon_from_folder(load_library_path(), webtoon_name)
        if updated is None:
            return False

        for index, webtoon in enumerate(self._webtoons):
            if webtoon.name != webtoon_name:
                continue
            self._webtoons[index] = updated
            card = self._card_for(webtoon_name)
            if card is not None:
                card.refresh_webtoon(updated)
            self._sync_update_controls()
            return True
        return False

    def _suppress_card_open(self, seconds: float):
        self._ignore_open_until = max(self._ignore_open_until, time.monotonic() + seconds)

    def _block_library_input(self, seconds: float):
        self._block_input_until = max(self._block_input_until, time.monotonic() + seconds)
        self._input_blocker.setGeometry(self.rect())
        self._input_blocker.show()
        self._input_blocker.raise_()
        QTimer.singleShot(int(seconds * 1000) + 50, self._release_library_input_if_due)

    def _release_library_input_if_due(self):
        if time.monotonic() < self._block_input_until:
            QTimer.singleShot(100, self._release_library_input_if_due)
            return
        if QApplication.mouseButtons() != Qt.NoButton:
            QTimer.singleShot(100, self._release_library_input_if_due)
            return
        self._input_blocker.hide()

    def _schedule_filter(self, text):
        self._pending_search = text
        self._search_timer.start(150)

    def _apply_filter(self):
        text = self._pending_search.strip()
        scores = {
            webtoon.name: score
            for score, webtoon in rank_webtoons(self._webtoons, text)
        }
        visible_cards = []

        for card in self._cards:
            visible = card.webtoon.name in scores if text else True
            card.setVisible(visible)
            if visible:
                visible_cards.append(card)

        if text:
            visible_cards.sort(
                key=lambda card: (-scores.get(card.webtoon.name, 0), card.webtoon.name.lower())
            )

        for i, card in enumerate(visible_cards):
            row = i // self._current_cols
            col = i % self._current_cols
            self.grid.addWidget(card, row, col)
