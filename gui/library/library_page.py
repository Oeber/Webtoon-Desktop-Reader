from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout, QScrollArea, QHBoxLayout, QLabel, QSlider, QPushButton,
)
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox
from PySide6.QtCore import Qt, QTimer
from types import SimpleNamespace
import os
import shutil
import time

from app_logging import get_logger
from gui.common.styles import PAGE_BG_STYLE, SCROLL_AREA_STYLE, SEARCH_INPUT_STYLE
from library_manager import scan_library
from progress_store import get_instance as get_progress_store
from webtoon_settings_store import get_instance as get_webtoon_settings
from gui.library.webtoon_card import WebtoonCard, CARD_WIDTH
from gui.search.global_search import rank_webtoons
from gui.settings.settings_page import load_library_path, load_setting, save_setting


CARD_SPACING  = 16
PAGE_PADDING  = 24
UPDATE_COOLDOWN_SECONDS = 30
CARD_SCALE_MIN = 70
CARD_SCALE_MAX = 140
CARD_SCALE_KEY = "library_card_scale"
logger = get_logger(__name__)


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
        self._active_updates = {}
        self._manual_download_service = None
        self._active_manual_downloads = {}
        self._ignore_open_until = 0.0
        self._block_input_until = 0.0
        self._pending_reload = False
        self._card_scale = int(load_setting(CARD_SCALE_KEY, 100))
        self._pending_card_scale = self._card_scale
        self._selected_webtoons = set()

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(12)

        controls = QHBoxLayout()
        controls.setContentsMargins(PAGE_PADDING, PAGE_PADDING, PAGE_PADDING, 0)
        controls.setSpacing(12)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search webtoons...")
        self.search.setFixedHeight(36)
        self.search.setStyleSheet(SEARCH_INPUT_STYLE)
        controls.addWidget(self.search, 1)

        self.size_label = QLabel("Library size")
        self.size_label.setStyleSheet("color: #aaaaaa; font-size: 12px;")
        controls.addWidget(self.size_label)

        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(CARD_SCALE_MIN, CARD_SCALE_MAX)
        self.size_slider.setValue(self._card_scale)
        self.size_slider.setFixedWidth(140)
        self.size_slider.setToolTip("Smaller cards fit more items per row")
        self.size_slider.valueChanged.connect(self._on_size_slider_changed)
        self.size_slider.sliderReleased.connect(self._apply_pending_card_scale)
        controls.addWidget(self.size_slider)

        self.size_value_label = QLabel(f"{self._card_scale}%")
        self.size_value_label.setFixedWidth(42)
        self.size_value_label.setStyleSheet("color: #cccccc; font-size: 12px;")
        controls.addWidget(self.size_value_label)

        root_layout.addLayout(controls)

        self.batch_bar = QWidget()
        self.batch_bar.setStyleSheet("""
            QWidget {
                background: #171717;
                border-top: 1px solid #242424;
                border-bottom: 1px solid #242424;
            }
        """)
        batch_layout = QHBoxLayout(self.batch_bar)
        batch_layout.setContentsMargins(PAGE_PADDING, 10, PAGE_PADDING, 10)
        batch_layout.setSpacing(10)

        self.batch_label = QLabel("")
        self.batch_label.setStyleSheet("color: #d0d0d0; font-size: 12px;")
        batch_layout.addWidget(self.batch_label)

        self.mark_completed_btn = QPushButton("Mark Completed")
        self.mark_completed_btn.setStyleSheet("""
            QPushButton {
                background: #2a2a2a;
                color: #f0f0f0;
                border: 1px solid #343434;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover { background: #333333; }
        """)
        self.mark_completed_btn.clicked.connect(self._mark_selected_completed)
        batch_layout.addWidget(self.mark_completed_btn)

        self.update_selected_btn = QPushButton("Update Selected")
        self.update_selected_btn.setStyleSheet(self.mark_completed_btn.styleSheet())
        self.update_selected_btn.clicked.connect(self._update_selected)
        batch_layout.addWidget(self.update_selected_btn)

        self.delete_selected_btn = QPushButton("Delete Selected")
        self.delete_selected_btn.setStyleSheet("""
            QPushButton {
                background: #4a1f1f;
                color: #ffffff;
                border: 1px solid #703030;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover { background: #5a2727; }
        """)
        self.delete_selected_btn.clicked.connect(self._delete_selected)
        batch_layout.addWidget(self.delete_selected_btn)

        self.clear_selection_btn = QPushButton("Clear")
        self.clear_selection_btn.setStyleSheet(self.mark_completed_btn.styleSheet())
        self.clear_selection_btn.clicked.connect(self._clear_selection)
        batch_layout.addWidget(self.clear_selection_btn)
        batch_layout.addStretch()

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(SCROLL_AREA_STYLE)

        self.container = QWidget()
        self.container.setStyleSheet(PAGE_BG_STYLE)

        self.grid = QGridLayout(self.container)
        self.grid.setSpacing(CARD_SPACING)
        self.grid.setContentsMargins(PAGE_PADDING, PAGE_PADDING, PAGE_PADDING, PAGE_PADDING)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.scroll.setWidget(self.container)
        root_layout.addWidget(self.scroll, 1)

        self.batch_bar.hide()
        root_layout.addWidget(self.batch_bar)

        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_filter)
        self.search.textChanged.connect(self._schedule_filter)

        self._scale_timer = QTimer(self)
        self._scale_timer.setSingleShot(True)
        self._scale_timer.timeout.connect(self._apply_pending_card_scale)

        self._cooldown_timer = QTimer(self)
        self._cooldown_timer.timeout.connect(self._sync_update_controls)
        self._cooldown_timer.start(1000)

        self._input_blocker = QWidget(self)
        self._input_blocker.hide()
        self._input_blocker.setStyleSheet("background: transparent;")

        self.load_library()
 
    def load_library(self):
        logger.info("Loading library page contents")
        self._pending_reload = False
        self._webtoons = scan_library(load_library_path(), self.settings_store)
        self._prune_selection()
        self._rebuild_grid(self._columns_for_width(self.width()))

    def showEvent(self, event):
        super().showEvent(event)
        if self._pending_reload and (self._update_service is None or not self._update_service.is_busy()):
            self.load_library()

    def refresh_progress(self):
        """Call this when returning from the viewer so badges update."""
        for card in self._cards:
            card._refresh_badges()

    def refresh_dynamic_state(self):
        self._sync_update_controls()
        self._sync_manual_download_cards()

    def _card_width(self) -> int:
        return max(120, int(CARD_WIDTH * (self._card_scale / 100.0)))

    def _columns_for_width(self, width: int) -> int:
        card_width = self._card_width()
        available = max(width - PAGE_PADDING * 2, card_width + 16)
        return max(1, available // (card_width + 16 + CARD_SPACING))

    def _rebuild_grid(self, columns: int):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._cards = []
        self._current_cols = columns

        display_webtoons = self._display_webtoons()

        for index, webtoon in enumerate(display_webtoons):
            row = index // columns
            col = index % columns
            is_download_placeholder = bool(getattr(webtoon, "_download_placeholder", False))

            card = WebtoonCard(
                webtoon,
                settings_store=self.settings_store,
                progress_store=self.progress_store,
                on_open=self._open_detail,
                on_changed=self._reload_after_edit,
                on_update=self._start_update,
                on_delete=self._delete_single_webtoon,
                on_cancel_download=self._cancel_manual_download,
                on_select=self._on_card_selected,
                card_width=self._card_width(),
                download_placeholder=is_download_placeholder,
            )
            if is_download_placeholder:
                state = self._active_manual_downloads.get(webtoon.name, {})
                card.set_download_progress(state.get("current", 0), state.get("total", 0))
            else:
                card.set_selected(webtoon.name in self._selected_webtoons)
            self._cards.append(card)
            self.grid.addWidget(card, row, col, Qt.AlignTop | Qt.AlignLeft)

        self._sync_update_controls()
        self._sync_manual_download_cards()
        self._sync_batch_actions()

    def _relayout_cards(self, columns: int | None = None):
        if columns is None:
            columns = self._columns_for_width(self.width())
        columns = max(1, columns)

        while self.grid.count():
            self.grid.takeAt(0)

        self._current_cols = columns
        visible_cards = []

        for card in self._cards:
            card.update_card_size(self._card_width())
            if not card.isHidden():
                visible_cards.append(card)

        if self._pending_search.strip():
            scores = {
                webtoon.name: score
                for score, webtoon in rank_webtoons(self._display_webtoons(), self._pending_search.strip())
            }
            visible_cards.sort(
                key=lambda card: (-scores.get(card.webtoon.name, 0), card.webtoon.name.lower())
            )

        for index, card in enumerate(visible_cards):
            row = index // columns
            col = index % columns
            self.grid.addWidget(card, row, col, Qt.AlignTop | Qt.AlignLeft)

        self.container.updateGeometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._input_blocker.setGeometry(self.rect())
        if self._input_blocker.isVisible():
            self._input_blocker.raise_()
        new_cols = self._columns_for_width(event.size().width())
        if new_cols != self._current_cols and self._cards:
            self._relayout_cards(new_cols)

    def _open_detail(self, webtoon):
        if time.monotonic() < self._ignore_open_until:
            logger.info("Blocked card open for %s due to cooldown suppression", webtoon.name)
            return
        logger.info("Opening detail from library card for %s", webtoon.name)
        self.main_window.open_detail(webtoon)

    def _reload_after_edit(self):
        logger.info("Reloading library after edit")
        self.load_library()
        self._apply_filter()

    def attach_update_service(self, service):
        if self._update_service is service:
            return

        logger.info("Attaching shared update service to library page")
        self._update_service = service
        self._update_service.status_changed.connect(self._on_update_status_changed)
        self._update_service.progress_changed.connect(self._on_update_progress_changed)
        self._update_service.download_started.connect(self._on_update_started)
        self._update_service.download_finished.connect(self._on_update_finished)
        self._update_service.library_changed.connect(self._on_update_library_changed)
        self._sync_update_controls()

    def attach_manual_download_service(self, service):
        if self._manual_download_service is service:
            return

        logger.info("Attaching manual download service to library page")
        self._manual_download_service = service
        service.download_started.connect(self._on_manual_download_started)
        service.name_resolved.connect(self._on_manual_download_renamed)
        service.progress_changed.connect(self._on_manual_download_progress_changed)
        service.thumbnail_resolved.connect(self._on_manual_download_thumbnail_resolved)
        service.download_finished.connect(self._on_manual_download_finished)
        service.library_changed.connect(self._on_manual_library_changed)

    def _start_update(self, webtoon_name: str):
        if self._update_service is None:
            return
        if self.settings_store.get_completed(webtoon_name):
            logger.info("Update blocked for completed webtoon %s", webtoon_name)
            self._sync_update_controls()
            return

        source_url = self.settings_store.get_source_url(webtoon_name)
        if not source_url:
            return

        remaining = self._cooldown_remaining(webtoon_name)
        if remaining > 0:
            logger.info("Update blocked by cooldown for %s (%ds remaining)", webtoon_name, remaining)
            self._sync_update_controls()
            return

        logger.info("Starting library-triggered update for %s", webtoon_name)
        error = self._update_service.start_download(
            source_url,
            load_library_path(),
            preferred_name=webtoon_name,
        )
        if error:
            logger.warning("Failed to start update for %s: %s", webtoon_name, error)
            self._sync_update_controls()
            return

        self._sync_update_controls()

    def _cooldown_remaining(self, webtoon_name: str) -> int:
        last_update_at = self.settings_store.get_last_update_at(webtoon_name)
        if last_update_at is None:
            return 0
        elapsed = int(time.time()) - int(last_update_at)
        return max(0, UPDATE_COOLDOWN_SECONDS - elapsed)

    def _sync_update_controls(self):
        for card in self._cards:
            if getattr(card, "_download_placeholder", False):
                continue
            if card.webtoon.name in self._active_manual_downloads:
                continue
            active_update = self._active_updates.get(card.webtoon.name)
            has_source = bool(self.settings_store.get_source_url(card.webtoon.name))
            is_completed = self.settings_store.get_completed(card.webtoon.name)
            update_allowed = has_source and not is_completed
            card.set_update_available(has_source)
            if is_completed:
                card.set_update_available(False)
                card.set_update_status("Ready")
                continue
            if not update_allowed:
                continue

            if active_update is not None or (self._update_service is not None and self._update_service.has_active_download(card.webtoon.name)):
                card.set_update_enabled(False, "Update in progress")
                card.set_update_status("Downloading")
                if active_update is not None:
                    current = active_update.get("current", 0)
                    total = active_update.get("total", 0)
                    if total > 0:
                        card.set_update_progress(current, total)
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

    def _sync_manual_download_cards(self):
        for card in self._cards:
            state = self._active_manual_downloads.get(card.webtoon.name)
            if state is None:
                card.clear_download_progress()
                continue
            card.set_download_progress(state.get("current", 0), state.get("total", 0))

    def _display_webtoons(self):
        names_in_library = {webtoon.name for webtoon in self._webtoons}
        placeholders = []
        for name, state in self._active_manual_downloads.items():
            if name in names_in_library:
                continue
            placeholders.append(SimpleNamespace(
                name=name,
                path="",
                thumbnail=state.get("thumbnail", "") or "",
                chapters=[],
                _download_placeholder=True,
            ))
        placeholders.sort(key=lambda item: item.name.lower())
        return placeholders + list(self._webtoons)

    def _on_card_selected(self, webtoon_name: str, selected: bool):
        if selected:
            self._selected_webtoons.add(webtoon_name)
        else:
            self._selected_webtoons.discard(webtoon_name)
        self._sync_batch_actions()

    def _sync_batch_actions(self):
        count = len(self._selected_webtoons)
        self.batch_bar.setVisible(count > 0)
        show_selection_controls = count > 0
        for card in self._cards:
            card.set_selection_controls_visible(show_selection_controls)
        if count <= 0:
            return
        self.batch_label.setText(f"{count} selected")
        all_completed = all(
            self.settings_store.get_completed(name)
            for name in self._selected_webtoons
        )
        self.mark_completed_btn.setText("Mark Ongoing" if all_completed else "Mark Completed")
        updatable = any(
            self.settings_store.get_source_url(name) and not self.settings_store.get_completed(name)
            for name in self._selected_webtoons
        )
        self.update_selected_btn.setEnabled(updatable)

    def _clear_selection(self):
        self._selected_webtoons.clear()
        for card in self._cards:
            card.set_selected(False)
        self._sync_batch_actions()

    def _prune_selection(self):
        valid_names = {webtoon.name for webtoon in self._webtoons}
        self._selected_webtoons = {
            name for name in self._selected_webtoons
            if name in valid_names
        }

    def _mark_selected_completed(self):
        selected = sorted(self._selected_webtoons)
        if not selected:
            return
        all_completed = all(
            self.settings_store.get_completed(name)
            for name in selected
        )
        for name in selected:
            self.settings_store.set_completed(name, not all_completed)
        logger.info(
            "Marked %d selected webtoons as %s",
            len(selected),
            "ongoing" if all_completed else "completed",
        )
        self.load_library()
        self._apply_filter()
        self._clear_selection()

    def _update_selected(self):
        selected = [
            webtoon.name
            for webtoon in self._webtoons
            if webtoon.name in self._selected_webtoons
        ]
        if not selected:
            return
        for name in selected:
            if self.settings_store.get_completed(name):
                continue
            if not self.settings_store.get_source_url(name):
                continue
            self._start_update(name)
        self._clear_selection()

    def _delete_selected(self):
        selected = sorted(self._selected_webtoons)
        if not selected:
            return
        if self._delete_webtoons(selected):
            self._clear_selection()

    def _delete_single_webtoon(self, webtoon_name: str):
        if self._delete_webtoons([webtoon_name]):
            self._selected_webtoons.discard(webtoon_name)
            self._sync_batch_actions()

    def _delete_webtoons(self, names: list[str]) -> bool:
        if not names:
            return False

        if len(names) == 1:
            message = f"Delete '{names[0]}' from the library?\n\nThis removes the folder, progress, thumbnail overrides, and saved settings."
            title = "Delete webtoon"
        else:
            message = (
                f"Delete {len(names)} webtoons from the library?\n\n"
                "This removes their folders, progress, thumbnail overrides, and saved settings."
            )
            title = "Delete selected webtoons"

        answer = QMessageBox.question(
            self,
            title,
            message,
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return False

        library_path = load_library_path()
        deleted_count = 0
        for name in names:
            try:
                webtoon_path = os.path.join(library_path, name)
                if os.path.isdir(webtoon_path):
                    shutil.rmtree(webtoon_path)
                self.progress_store.clear(name)
                self.settings_store.delete_webtoon(name)
                deleted_count += 1
            except Exception as e:
                logger.error("Failed to delete webtoon %s", name, exc_info=e)

        if deleted_count <= 0:
            return False

        logger.info("Deleted %d webtoons", deleted_count)
        self.load_library()
        self._apply_filter()
        return True

    def _on_update_started(self, name: str):
        self._active_updates[name] = {"current": 0, "total": 0}
        self._sync_update_controls()

    def _on_update_finished(self, name: str, status: str):
        logger.info("Library page received update finished for %s with status=%s", name, status)
        self._active_updates.pop(name, None)
        if status == "Completed":
            self.settings_store.set_last_update_at(name, int(time.time()))
        self._sync_update_controls()

    def _on_update_library_changed(self, name: str):
        logger.info("Library page noticed library_changed from update service")
        if self.isVisible():
            if not self._refresh_updated_webtoon(name):
                self.load_library()
        else:
            self._pending_reload = True

    def _on_update_status_changed(self, name: str, status: str):
        card = self._card_for(name)
        if card is None:
            return
        if status != "Downloading":
            self._active_updates.pop(name, None)
        if status == "Completed":
            card.set_update_progress(1, 1)
        card.set_update_status(status)

    def _on_update_progress_changed(self, name: str, current: int, total: int):
        state = self._active_updates.setdefault(name, {"current": 0, "total": 0})
        state["current"] = max(0, int(current))
        state["total"] = max(0, int(total))
        card = self._card_for(name)
        if card is not None:
            card.set_update_progress(current, total)

    def _on_manual_download_started(self, name: str):
        self._active_manual_downloads[name] = {"current": 0, "total": 0, "thumbnail": ""}
        self._rebuild_grid(self._current_cols or self._columns_for_width(self.width()))

    def _on_manual_download_renamed(self, old_name: str, name: str):
        state = self._active_manual_downloads.pop(old_name, {"current": 0, "total": 0, "thumbnail": ""})
        self._active_manual_downloads[name] = state
        self._rebuild_grid(self._current_cols or self._columns_for_width(self.width()))

    def _on_manual_download_progress_changed(self, name: str, current: int, total: int):
        state = self._active_manual_downloads.setdefault(name, {"current": 0, "total": 0, "thumbnail": ""})
        state["current"] = max(0, int(current))
        state["total"] = max(0, int(total))
        card = self._card_for(name)
        if card is not None:
            card.set_download_progress(state["current"], state["total"])

    def _on_manual_download_thumbnail_resolved(self, name: str, path: str):
        state = self._active_manual_downloads.setdefault(name, {"current": 0, "total": 0, "thumbnail": ""})
        state["thumbnail"] = path or ""
        card = self._card_for(name)
        if card is not None:
            card.set_thumbnail(path)

    def _on_manual_download_finished(self, name: str, status: str):
        self._active_manual_downloads.pop(name, None)
        self._rebuild_grid(self._current_cols or self._columns_for_width(self.width()))

    def _on_manual_library_changed(self, name: str):
        logger.info("Library page noticed library_changed from manual downloader")
        if self.isVisible():
            self.load_library()
        else:
            self._pending_reload = True

    def _cancel_manual_download(self, webtoon_name: str):
        if self._manual_download_service is None:
            return
        logger.info("Cancelling manual download from library card for %s", webtoon_name)
        self._manual_download_service.cancel_download(webtoon_name)

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
        logger.info("Scheduling library filter for query='%s'", text.strip())
        self._pending_search = text
        self._search_timer.start(150)

    def _on_size_slider_changed(self, value: int):
        value = max(CARD_SCALE_MIN, min(CARD_SCALE_MAX, value))
        self._pending_card_scale = value
        self.size_value_label.setText(f"{value}%")
        if value == self._card_scale:
            self._scale_timer.stop()
            save_setting(CARD_SCALE_KEY, value)
            return
        if self.size_slider.isSliderDown():
            self._scale_timer.start(24)
            return
        self._scale_timer.start(16)

    def _apply_pending_card_scale(self):
        value = max(CARD_SCALE_MIN, min(CARD_SCALE_MAX, self._pending_card_scale))
        if value == self._card_scale:
            self.size_value_label.setText(f"{value}%")
            save_setting(CARD_SCALE_KEY, value)
            return
        self._card_scale = value
        self.size_value_label.setText(f"{value}%")
        save_setting(CARD_SCALE_KEY, value)
        logger.info("Library card scale changed: %d%%", value)
        if self._cards:
            self._relayout_cards(self._columns_for_width(self.width()))
        else:
            self._rebuild_grid(self._columns_for_width(self.width()))

    def _apply_filter(self):
        text = self._pending_search.strip()
        scores = {
            webtoon.name: score
            for score, webtoon in rank_webtoons(self._display_webtoons(), text)
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
