import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests
from core.app_logging import get_logger
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
import qtawesome as qta

from core.update_utils import cooldown_remaining
from gui.common.styles import (
    action_button_checked_style,
    ACCENT,
    BATCH_BAR_STYLE,
    BATCH_LABEL_STYLE,
    BUTTON_STYLE,
    CARD_ACTION_BUTTON_STYLE,
    CARD_ACTION_BUTTON_DISABLED_STYLE,
    CARD_INFO_LABEL_STYLE,
    CARD_PROGRESS_OVERLAY_STYLE,
    CARD_TITLE_LABEL_STYLE,
    EMPTY_STATE_LABEL_STYLE,
    NEW_CHIP_STYLE,
    SEARCH_INPUT_STYLE,
    STATUS_LABEL_STYLE,
    TRANSPARENT_BORDERLESS_STYLE,
    card_badge_button_style,
    card_image_border_style,
    status_text_style,
)
from gui.common.card_utils import card_toggle_icon, load_rounded_cover
from gui.downloader.download_widgets import SpinnerCircle, format_last_updated
from gui.downloader.helpers import sanitize_webtoon_name
from gui.downloader.page_base import DownloadHistoryPageBase
from gui.search.global_search import rank_webtoons
from gui.settings.settings_page import load_library_path
from library.library_manager import scan_library
from scrapers.base import ScraperDisabledError, ScraperError
from scrapers.registry import get_scraper, is_scraper_enabled_for_url
from stores.webtoon_settings_store import get_instance as get_webtoon_settings


logger = get_logger(__name__)

DISABLED_CHECK_ERROR = "__disabled_scraper__"
UNSUPPORTED_CHECK_ERROR = "__unsupported_scraper__"
LIBRARY_CARD_WIDTH = 180
LIBRARY_CARD_HEIGHT = 270
LIBRARY_CARD_RADIUS = 12
UPDATE_CARD_WIDTH = LIBRARY_CARD_WIDTH
UPDATE_CARD_SPACING = 16
UPDATE_RESULT_CACHE_TTL_SECONDS = 45
UPDATE_ERROR_CACHE_TTL_SECONDS = 30
SHORTCUT_FALLBACK = object()
UPDATE_STATUS_COLORS = {
    "Ready": "#b18b84",
    "Checking": "#b18b84",
    "Downloading": ACCENT,
    "Completed": "#4caf50",
    "Failed": "#f44336",
    "Cancelled": "#b18b84",
}


class UpdateAvailabilityLoader(QObject):

    checked = Signal(int, object, object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._executor = ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="update-check",
        )
        self._thread_local = threading.local()
        self._result_cache: dict[tuple, tuple[float, dict | None]] = {}
        self._error_cache: dict[str, tuple[float, str]] = {}

    def load(self, request_id: int, candidate: dict):
        cached_result = self._cached_result(candidate)
        if cached_result is not None:
            result, error = cached_result
            self.checked.emit(request_id, candidate, result, error)
            return

        def worker():
            try:
                result = self._check_candidate(candidate)
                self._store_result_cache(candidate, result)
                self.checked.emit(request_id, candidate, result, "")
            except ScraperDisabledError as e:
                logger.info("Skipping disabled scraper update check for %s: %s", candidate.get("name"), e)
                self.checked.emit(request_id, candidate, None, DISABLED_CHECK_ERROR)
            except ValueError as e:
                logger.info("Skipping unsupported scraper update check for %s: %s", candidate.get("name"), e)
                self.checked.emit(request_id, candidate, None, UNSUPPORTED_CHECK_ERROR)
            except ScraperError as e:
                self._store_error_cache(candidate, str(e))
                self.checked.emit(request_id, candidate, None, str(e))
            except Exception as e:
                logger.exception("Unexpected update check failure for %s", candidate.get("name"))
                self._store_error_cache(candidate, str(e))
                self.checked.emit(request_id, candidate, None, str(e))

        self._executor.submit(worker)

    def _check_candidate(self, candidate: dict) -> dict | None:
        source_url = candidate.get("source_url") or ""
        scraper = get_scraper(source_url)
        series_url = source_url
        if scraper.is_chapter_url(series_url):
            series_url = scraper.series_url_from_chapter_url(series_url)
        session = self._get_thread_session()
        shortcut_result = self._check_candidate_shortcut(scraper, series_url, candidate, session)
        if shortcut_result is not SHORTCUT_FALLBACK:
            return shortcut_result

        series = scraper.get_series_info(series_url, session=session)

        local_chapters = set(candidate.get("chapter_names") or [])
        new_remote = []
        seen = set()
        for chapter in getattr(series, "chapters", []) or []:
            local_name = self._format_remote_chapter_dir_name(chapter)
            if not local_name or local_name in local_chapters or local_name in seen:
                continue
            seen.add(local_name)
            new_remote.append(local_name)

        new_chapters = len(new_remote)
        if new_chapters <= 0:
            return None

        return {
            "name": candidate.get("name") or "",
            "source_url": series_url,
            "webtoon": candidate.get("webtoon"),
            "last_update_at": candidate.get("last_update_at"),
            "local_chapters": int(candidate.get("local_chapters", 0) or 0),
            "remote_chapters": len(getattr(series, "chapters", []) or []),
            "new_chapters": new_chapters,
        }

    def _check_candidate_shortcut(self, scraper, series_url: str, candidate: dict, session) -> dict | None:
        get_update_snapshot = getattr(scraper, "get_update_snapshot", None)
        if not callable(get_update_snapshot):
            return SHORTCUT_FALLBACK

        snapshot = get_update_snapshot(
            series_url,
            local_chapter_names=list(candidate.get("chapter_names") or []),
            session=session,
        )
        if snapshot is None:
            return SHORTCUT_FALLBACK

        new_chapters = int(snapshot.get("new_chapters", 0) or 0)
        if new_chapters <= 0:
            return None

        return {
            "name": candidate.get("name") or "",
            "source_url": series_url,
            "webtoon": candidate.get("webtoon"),
            "last_update_at": candidate.get("last_update_at"),
            "local_chapters": int(candidate.get("local_chapters", 0) or 0),
            "remote_chapters": int(snapshot.get("remote_chapters", 0) or 0),
            "new_chapters": new_chapters,
        }

    def _format_remote_chapter_dir_name(self, chapter) -> str:
        number = getattr(chapter, "number", None)
        if number is not None:
            try:
                number_value = float(number)
                if number_value.is_integer():
                    return f"Chapter {int(number_value)}"
                return f"Chapter {format(number_value, 'g')}"
            except Exception:
                pass
        return sanitize_webtoon_name(getattr(chapter, "title", "") or "") or "Chapter"

    def _get_thread_session(self):
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = requests.Session()
            self._thread_local.session = session
        return session

    def _cached_result(self, candidate: dict):
        self._prune_expired_cache_entries()
        error_key = self._error_cache_key(candidate)
        cached_error = self._error_cache.get(error_key)
        if cached_error is not None:
            logger.info("Using cached update-check failure for %s", candidate.get("name"))
            return None, cached_error[1]

        result_key = self._result_cache_key(candidate)
        cached_result = self._result_cache.get(result_key)
        if cached_result is None:
            return None

        logger.info("Using cached update-check result for %s", candidate.get("name"))
        payload = cached_result[1]
        return self._rehydrate_cached_result(candidate, payload), ""

    def _store_result_cache(self, candidate: dict, result: dict | None):
        self._prune_expired_cache_entries()
        self._error_cache.pop(self._error_cache_key(candidate), None)
        self._result_cache[self._result_cache_key(candidate)] = (
            time.monotonic() + UPDATE_RESULT_CACHE_TTL_SECONDS,
            self._dehydrate_result(result),
        )

    def _store_error_cache(self, candidate: dict, error: str):
        self._prune_expired_cache_entries()
        self._result_cache.pop(self._result_cache_key(candidate), None)
        self._error_cache[self._error_cache_key(candidate)] = (
            time.monotonic() + UPDATE_ERROR_CACHE_TTL_SECONDS,
            str(error or ""),
        )

    def _prune_expired_cache_entries(self):
        now = time.monotonic()
        self._result_cache = {
            key: value
            for key, value in self._result_cache.items()
            if value[0] > now
        }
        self._error_cache = {
            key: value
            for key, value in self._error_cache.items()
            if value[0] > now
        }

    def _result_cache_key(self, candidate: dict) -> tuple:
        return (
            str(candidate.get("source_url") or "").strip(),
            tuple(sorted(str(name or "") for name in (candidate.get("chapter_names") or []))),
        )

    def _error_cache_key(self, candidate: dict) -> str:
        return str(candidate.get("source_url") or "").strip()

    def _dehydrate_result(self, result: dict | None):
        if result is None:
            return None
        return {
            "source_url": result.get("source_url") or "",
            "local_chapters": int(result.get("local_chapters", 0) or 0),
            "remote_chapters": int(result.get("remote_chapters", 0) or 0),
            "new_chapters": int(result.get("new_chapters", 0) or 0),
        }

    def _rehydrate_cached_result(self, candidate: dict, payload: dict | None):
        if payload is None:
            return None
        return {
            "name": candidate.get("name") or "",
            "source_url": payload.get("source_url") or "",
            "webtoon": candidate.get("webtoon"),
            "last_update_at": candidate.get("last_update_at"),
            "local_chapters": int(payload.get("local_chapters", 0) or 0),
            "remote_chapters": int(payload.get("remote_chapters", 0) or 0),
            "new_chapters": int(payload.get("new_chapters", 0) or 0),
        }

class UpdateCard(QFrame):

    def __init__(
        self,
        *,
        webtoon,
        source_url: str,
        last_update_at: int | None,
        new_chapters: int,
        remote_chapters: int,
        on_update,
    ):
        super().__init__()
        self.webtoon = webtoon
        self.name = webtoon.name
        self.source_url = source_url
        self.last_update_at = last_update_at
        self.new_chapters = max(0, int(new_chapters))
        self.remote_chapters = max(0, int(remote_chapters))
        self.local_chapters = len(getattr(webtoon, "chapters", []) or [])
        self.on_update = on_update
        self.on_select = None
        self.thumbnail_path = ""
        self.card_width = UPDATE_CARD_WIDTH
        self.card_height = int(self.card_width * (LIBRARY_CARD_HEIGHT / LIBRARY_CARD_WIDTH))
        self._selected = False
        self._show_selection_controls = False

        self.setFixedWidth(self.card_width + 16)
        self.setStyleSheet(TRANSPARENT_BORDERLESS_STYLE)

        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(8, 8, 8, 8)

        self.image_container = QWidget(self)
        self.image_container.setFixedSize(self.card_width, self.card_height)
        self.image_container.setStyleSheet(TRANSPARENT_BORDERLESS_STYLE)

        self.thumb_label = QLabel("No Cover", self.image_container)
        self.thumb_label.setFixedSize(self.card_width, self.card_height)
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self._apply_border_style(hovered=False)

        self.update_btn = QPushButton(self.image_container)
        self.update_btn.setCursor(Qt.PointingHandCursor)
        self.update_btn.setStyleSheet(CARD_ACTION_BUTTON_DISABLED_STYLE)
        self.update_btn.setFixedSize(28, 28)
        self.update_btn.move(6, 6)
        self.update_btn.clicked.connect(lambda: self.on_update(self))
        self.update_btn.setIcon(qta.icon("fa5s.sync", color="#ff8a7a"))
        self.update_btn.setIconSize(self._button_icon_size())

        self.select_btn = QPushButton(self.image_container)
        self.select_btn.setCheckable(True)
        self.select_btn.setFixedSize(28, 28)
        self.select_btn.move(6, self.card_height - 34)
        self.select_btn.setCursor(Qt.PointingHandCursor)
        self.select_btn.clicked.connect(self._toggle_selected_from_button)
        self._apply_select_button_style()
        self._refresh_select_button()
        self._refresh_select_visibility()

        self.progress_overlay = QWidget(self.image_container)
        self.progress_overlay.setFixedSize(84, 84)
        self.progress_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.progress_overlay.setStyleSheet(CARD_PROGRESS_OVERLAY_STYLE)
        overlay_layout = QVBoxLayout(self.progress_overlay)
        overlay_layout.setContentsMargins(0, 0, 0, 0)

        self.spinner = SpinnerCircle(self.progress_overlay)
        overlay_layout.addWidget(self.spinner, alignment=Qt.AlignCenter)
        self.progress_overlay.hide()
        self._center_progress_overlay()

        self.name_label = QLabel(self.name, self)
        self.name_label.setFixedWidth(max(80, self.card_width - 42))
        self.name_label.setWordWrap(False)
        self.name_label.setMaximumHeight(18)
        self.name_label.setToolTip(self.name)
        self.name_label.setStyleSheet(CARD_TITLE_LABEL_STYLE)
        font = QFont("Segoe UI", 10)
        font.setWeight(QFont.Medium)
        self.name_label.setFont(font)
        self._apply_elided_title()

        self.info_label = QLabel(self._summary_text(), self)
        self.info_label.setStyleSheet(CARD_INFO_LABEL_STYLE)
        self.info_label.setVisible(bool(self.info_label.text().strip()))

        self.meta_btn = QPushButton(self)
        self.meta_btn.setFixedHeight(20)
        self.meta_btn.setMinimumWidth(0)
        self.meta_btn.setEnabled(False)
        self.meta_btn.setStyleSheet(card_badge_button_style(False))
        self.meta_btn.setText(self._meta_text())
        self.meta_btn.setToolTip(format_last_updated(self.last_update_at))

        self.new_chip = QLabel(self._count_label(), self)
        self.new_chip.setAlignment(Qt.AlignCenter)
        self.new_chip.setFixedHeight(14)
        self.new_chip.setStyleSheet(NEW_CHIP_STYLE)
        self.new_chip.show()

        latest_row = QHBoxLayout()
        latest_row.setContentsMargins(0, 0, 0, 0)
        latest_row.setSpacing(6)
        latest_row.addWidget(self.meta_btn, 1)
        latest_row.addWidget(self.new_chip, 0, Qt.AlignVCenter)

        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet(status_text_style(UPDATE_STATUS_COLORS["Ready"]))
        self.detail_label = QLabel("Ready to download new chapters")
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet(CARD_INFO_LABEL_STYLE)
        self.status_label.hide()
        self.detail_label.hide()

        layout.addWidget(self.image_container)
        layout.addWidget(self.name_label)
        layout.addWidget(self.info_label)
        layout.addLayout(latest_row)

        if getattr(webtoon, "thumbnail", "") and os.path.exists(webtoon.thumbnail):
            self.set_thumbnail(webtoon.thumbnail)

        self.set_status("Ready")

    def cooldown_remaining(self) -> int:
        return cooldown_remaining(self.last_update_at)

    def set_thumbnail(self, path: str):
        self.thumbnail_path = path or ""
        self._load_thumbnail(self.thumbnail_path)

    def set_progress(self, current: int, total: int):
        total = max(1, int(total))
        current = max(0, min(int(current), total))
        percent = int((current / total) * 100)
        self.progress_overlay.show()
        self.spinner.set_progress(percent)
        self.status_label.setText("Downloading")
        self.status_label.setStyleSheet(status_text_style(UPDATE_STATUS_COLORS["Downloading"]))
        self.detail_label.setText(f"Downloading {current} / {total} chapters")

    def set_status(self, status: str):
        color = UPDATE_STATUS_COLORS.get(status, "#d7b1aa")
        self.status_label.setText(status)
        self.status_label.setStyleSheet(status_text_style(color))

        if status == "Completed":
            self.progress_overlay.hide()
            self.spinner.set_complete(100)
            self.detail_label.setText("Update finished")
        elif status in ("Failed", "Cancelled"):
            self.progress_overlay.hide()
            self.spinner.set_failed()
            self.detail_label.setText("Update did not complete")
        elif status == "Downloading":
            self.progress_overlay.show()
            self.spinner.set_spinning()
            self.detail_label.setText("Downloading new chapters")
        else:
            self.progress_overlay.hide()
            self.spinner.set_idle()
            self.detail_label.clear()
            self.status_label.hide()
            self.detail_label.hide()

    def set_last_update_at(self, timestamp: int):
        self.last_update_at = int(timestamp)
        self.meta_btn.setText(self._meta_text())
        self.meta_btn.setToolTip(format_last_updated(self.last_update_at))

    def set_selected(self, selected: bool):
        self._selected = bool(selected)
        self.select_btn.blockSignals(True)
        self.select_btn.setChecked(self._selected)
        self.select_btn.blockSignals(False)
        self._refresh_select_button()
        self._refresh_select_visibility()
        self._apply_border_style(hovered=self.underMouse())

    def set_selection_controls_visible(self, visible: bool):
        self._show_selection_controls = bool(visible)
        self._refresh_select_visibility()

    def _load_thumbnail(self, path: str):
        load_rounded_cover(
            self.thumb_label,
            path,
            self.card_width,
            self.card_height,
            LIBRARY_CARD_RADIUS,
            fallback_text="No Cover",
        )

    def _center_progress_overlay(self):
        x = (self.card_width - self.progress_overlay.width()) // 2
        y = (self.card_height - self.progress_overlay.height()) // 2
        self.progress_overlay.move(x, y)

    def _apply_border_style(self, hovered: bool):
        if self._selected:
            color = "#ff8a7a"
        else:
            color = "#666666" if hovered else "#2a2a2a"
        self.thumb_label.setStyleSheet(card_image_border_style(color, LIBRARY_CARD_RADIUS))

    def _apply_elided_title(self):
        metrics = QFontMetrics(self.name_label.font())
        width = max(0, self.name_label.contentsRect().width())
        if width <= 0:
            self.name_label.setText(self.name)
            return
        self.name_label.setText(metrics.elidedText(self.name, Qt.ElideRight, width))

    def _count_label(self) -> str:
        if self.new_chapters == 1:
            return "1 New"
        return f"{self.new_chapters} New"

    def _summary_text(self) -> str:
        return ""

    def _meta_text(self) -> str:
        if self.last_update_at is None:
            return "Never updated"
        return f"Updated {datetime.fromtimestamp(int(self.last_update_at)).strftime('%Y-%m-%d')}"

    def _apply_select_button_style(self):
        self.select_btn.setStyleSheet(action_button_checked_style("rgba(255,138,122,0.95)"))

    def _refresh_select_button(self):
        card_toggle_icon(self.select_btn, self._selected, size=12)

    def _button_icon_size(self):
        from PySide6.QtCore import QSize
        return QSize(12, 12)

    def _refresh_select_visibility(self):
        visible = self._selected or self._show_selection_controls or self.underMouse()
        self.select_btn.setVisible(visible)

    def _toggle_selected_from_button(self):
        self._selected = self.select_btn.isChecked()
        self._refresh_select_button()
        self._refresh_select_visibility()
        self._apply_border_style(hovered=self.underMouse())
        if callable(self.on_select):
            self.on_select(self.name, self._selected)

    def enterEvent(self, event):
        self._apply_border_style(hovered=True)
        self._refresh_select_visibility()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._apply_border_style(hovered=False)
        self._refresh_select_visibility()
        super().leaveEvent(event)


class UpdatePage(DownloadHistoryPageBase):
    check_cycle_finished = Signal(str, int, int)

    def __init__(self, main_window):
        super().__init__(main_window, "Updates", "Series with new chapters", history_kind="update")
        self.settings_store = get_webtoon_settings()
        self._candidates = []
        self._available_updates = []
        self._pending_search = ""
        self._empty_message = "Checking saved titles for updates..."
        self._check_request_id = 0
        self._pending_checks = 0
        self._completed_checks = 0
        self._check_errors = 0
        self._cards_container = None
        self._cards_layout = None
        self._entry_widgets = []
        self._selected_titles = set()
        self._has_loaded_once = False
        self._current_check_reason = "manual"
        self._last_check_counts_by_name = {}

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search titles with updates...")
        self.search_input.setFixedHeight(36)
        self.search_input.setStyleSheet(SEARCH_INPUT_STYLE)
        self.search_input.textChanged.connect(self._schedule_filter)
        self.layout().insertWidget(3, self.search_input)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(STATUS_LABEL_STYLE)
        self.status_label.setText("Checking saved titles for updates...")
        self.layout().insertWidget(4, self.status_label)

        self.batch_bar = QWidget(self)
        self.batch_bar.setStyleSheet(BATCH_BAR_STYLE)
        batch_layout = QHBoxLayout(self.batch_bar)
        batch_layout.setContentsMargins(32, 10, 32, 10)
        batch_layout.setSpacing(10)

        self.batch_label = QLabel("")
        self.batch_label.setStyleSheet(BATCH_LABEL_STYLE)
        batch_layout.addWidget(self.batch_label)

        self.update_selected_btn = QPushButton("Update Selected")
        self.update_selected_btn.setStyleSheet(BUTTON_STYLE)
        self.update_selected_btn.clicked.connect(self._update_selected)
        batch_layout.addWidget(self.update_selected_btn)

        self.clear_selection_btn = QPushButton("Clear")
        self.clear_selection_btn.setStyleSheet(BUTTON_STYLE)
        self.clear_selection_btn.clicked.connect(self._clear_selection)
        batch_layout.addWidget(self.clear_selection_btn)
        batch_layout.addStretch()

        self.batch_bar.hide()
        self.layout().addWidget(self.batch_bar)
        self.scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.layout().setStretchFactor(self.scroll, 1)
        self.layout().setStretchFactor(self.batch_bar, 0)
        self._rebuild_page_shell()

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_filter)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh_entries)

        self._cooldown_timer = QTimer(self)
        self._cooldown_timer.timeout.connect(self._sync_update_buttons)
        self._cooldown_timer.start(1000)

        self._checker = UpdateAvailabilityLoader(self)
        self._checker.checked.connect(self._on_candidate_checked)

    def _rebuild_page_shell(self):
        root_layout = self.layout()
        items = []
        while root_layout.count():
            item = root_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                items.append(widget)

        content_widgets = [widget for widget in items if widget is not self.batch_bar]
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        content = QWidget(self)
        content.setStyleSheet(TRANSPARENT_BORDERLESS_STYLE)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(32, 32, 32, 0)
        content_layout.setSpacing(20)

        for widget in content_widgets:
            content_layout.addWidget(widget)

        root_layout.addWidget(content, 1)
        root_layout.addWidget(self.batch_bar, 0)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._has_loaded_once:
            self._has_loaded_once = True
            self.refresh_entries(reason="open")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout_cards()

    def is_check_in_progress(self) -> bool:
        return self._pending_checks > self._completed_checks

    def refresh_entries(self, reason: str = "manual") -> bool:
        if self.is_check_in_progress():
            logger.info("Skipping update refresh because a check is already in progress")
            return False

        logger.info("Refreshing update entries reason=%s", reason)
        self._current_check_reason = str(reason or "manual")
        webtoons = scan_library(load_library_path(), self.settings_store)
        settings_rows = self.settings_store.get_rows(
            [webtoon.name for webtoon in webtoons],
            columns=("completed", "source_url", "last_update_at"),
        )
        candidates = []
        for webtoon in webtoons:
            row = settings_rows.get(webtoon.name, {})
            if bool(row.get("completed", 0)):
                continue
            source_url = row.get("source_url")
            if not source_url or not is_scraper_enabled_for_url(source_url):
                continue
            candidates.append(
                {
                    "name": webtoon.name,
                    "webtoon": webtoon,
                    "source_url": source_url,
                    "last_update_at": row.get("last_update_at"),
                    "local_chapters": len(getattr(webtoon, "chapters", []) or []),
                    "chapter_names": list(getattr(webtoon, "chapters", []) or []),
                }
            )

        self._candidates = candidates
        self._available_updates = []
        self._last_check_counts_by_name = {}
        self._check_request_id += 1
        self._pending_checks = len(candidates)
        self._completed_checks = 0
        self._check_errors = 0
        self._empty_message = (
            "Checking saved titles for updates..."
            if candidates else
            "No comics with a saved source URL yet."
        )
        self.set_error_text("")
        self._apply_filter()

        if not candidates:
            self.status_label.setText("No saved titles available for updates")
            self._finish_check_cycle()
            return True

        self.status_label.setText(f"Checking 0 / {len(candidates)} saved titles...")
        current_request_id = self._check_request_id
        for candidate in candidates:
            self._checker.load(current_request_id, candidate)
        return True

    def run_background_check(self, reason: str = "scheduled") -> bool:
        return self.refresh_entries(reason=reason)

    def available_update_signature(self) -> str:
        parts = []
        for item in sorted(self._available_updates, key=lambda entry: entry["webtoon"].name.lower()):
            parts.append(
                f"{item['webtoon'].name}:{int(item.get('new_chapters', 0) or 0)}"
            )
        return "|".join(parts)

    def _clear_history(self):
        self._entries_by_name.clear()
        self._entry_widgets = []
        self._cards_container = None
        self._cards_layout = None
        while self.history_layout.count():
            item = self.history_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _apply_filter(self):
        self._clear_history()
        self._prune_selection()

        visible_updates = self._filtered_candidates(self._pending_search)
        if not visible_updates:
            empty = QLabel(self._current_empty_message())
            empty.setStyleSheet(EMPTY_STATE_LABEL_STYLE)
            empty.setAlignment(Qt.AlignCenter)
            self.history_layout.addWidget(empty)
            self._sync_batch_actions()
            return

        self._cards_container = QWidget()
        self._cards_container.setStyleSheet(TRANSPARENT_BORDERLESS_STYLE)
        self._cards_layout = QGridLayout(self._cards_container)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setHorizontalSpacing(UPDATE_CARD_SPACING)
        self._cards_layout.setVerticalSpacing(UPDATE_CARD_SPACING)
        self._cards_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.history_layout.addWidget(self._cards_container)

        for update in visible_updates:
            card = UpdateCard(
                webtoon=update["webtoon"],
                source_url=update["source_url"],
                last_update_at=update["last_update_at"],
                new_chapters=update["new_chapters"],
                remote_chapters=update["remote_chapters"],
                on_update=self._start_update,
            )
            card.on_select = self._on_card_selected
            card.set_selected(card.name in self._selected_titles)
            card.set_selection_controls_visible(bool(self._selected_titles))
            self._register_entry(card)
            if self.service.has_active_download(card.name):
                card.set_status("Downloading")
            self._entry_widgets.append(card)

        self._relayout_cards()
        self._sync_update_buttons()
        self._sync_batch_actions()

    def _filtered_candidates(self, text: str):
        query = text.strip()
        if not query:
            return list(self._available_updates)

        ranked = rank_webtoons([item["webtoon"] for item in self._available_updates], query)
        updates_by_name = {
            item["webtoon"].name: item
            for item in self._available_updates
        }
        return [
            updates_by_name[webtoon.name]
            for _, webtoon in ranked
            if webtoon.name in updates_by_name
        ]

    def _schedule_filter(self, text: str):
        logger.info("Scheduling update-page filter for query='%s'", text.strip())
        self._pending_search = text
        self._search_timer.start(150)

    def _on_card_selected(self, webtoon_name: str, selected: bool):
        if selected:
            self._selected_titles.add(webtoon_name)
        else:
            self._selected_titles.discard(webtoon_name)
        self._sync_batch_actions()

    def _sync_batch_actions(self):
        count = len(self._selected_titles)
        self.batch_bar.setVisible(count > 0)
        self.batch_label.setText(f"{count} selected")
        self.update_selected_btn.setEnabled(count > 0)
        self.clear_selection_btn.setEnabled(count > 0)
        for card in self._entry_widgets:
            card.set_selection_controls_visible(count > 0)

    def _clear_selection(self):
        self._selected_titles.clear()
        for card in self._entry_widgets:
            card.set_selected(False)
            card.set_selection_controls_visible(False)
        self._sync_batch_actions()

    def _prune_selection(self):
        valid_names = {item["webtoon"].name for item in self._available_updates}
        self._selected_titles = {
            name for name in self._selected_titles
            if name in valid_names
        }

    def _update_selected(self):
        selected = [
            item["webtoon"].name
            for item in self._available_updates
            if item["webtoon"].name in self._selected_titles
        ]
        if not selected:
            return
        for name in selected:
            self.start_update_for_webtoon(name)
        self._sync_batch_actions()

    def _start_update(self, entry: UpdateCard):
        if self.settings_store.get_completed(entry.name):
            logger.info("Update page blocked completed webtoon %s", entry.name)
            self.refresh_entries()
            return
        if entry.cooldown_remaining() > 0:
            logger.info("Update page cooldown blocked %s", entry.name)
            self._sync_update_buttons()
            return

        logger.info("Starting update-page download for %s", entry.name)
        error = self.service.start_download(
            entry.source_url,
            load_library_path(),
            preferred_name=entry.name,
        )
        if error:
            logger.warning("Update-page download rejected for %s: %s", entry.name, error)
            self.set_error_text(error)
            return

        self.set_error_text("")
        self._sync_update_buttons()

    def start_update_for_webtoon(self, webtoon_name: str) -> str | None:
        if not webtoon_name:
            return "Please choose a title to update."

        source_url = self.settings_store.get_source_url(webtoon_name)
        if not source_url:
            error = f"No saved source URL found for '{webtoon_name}'."
            self.set_error_text(error)
            return error

        if self.settings_store.get_completed(webtoon_name):
            error = f"'{webtoon_name}' is marked completed."
            self.set_error_text(error)
            return error

        remaining = cooldown_remaining(self.settings_store.get_last_update_at(webtoon_name))
        if remaining > 0:
            self._sync_update_buttons()
            error = f"'{webtoon_name}' is still on cooldown."
            self.set_error_text(error)
            return error

        logger.info("Starting update from external trigger for %s", webtoon_name)
        error = self.service.start_download(
            source_url,
            load_library_path(),
            preferred_name=webtoon_name,
        )
        self.set_error_text("" if error is None else error)
        self._sync_update_buttons()
        return error

    def _sync_update_buttons(self):
        for widget in self._entries_by_name.values():
            if not isinstance(widget, UpdateCard):
                continue
            if self.service.has_active_download(widget.name):
                widget.update_btn.setEnabled(False)
                widget.update_btn.setToolTip("Updating...")
                current, total = self.service.get_progress(widget.name)
                if total > 0:
                    widget.set_progress(current, total)
                else:
                    widget.set_status("Downloading")
                continue

            remaining = widget.cooldown_remaining()
            widget.update_btn.setEnabled(remaining == 0)
            widget.update_btn.setToolTip(f"Wait {remaining}s" if remaining > 0 else "Update")
            if widget.status_label.text() != "Ready":
                widget.set_status("Ready")

    def _on_download_started(self, name: str):
        logger.info("Update-page download started for %s", name)
        self._refresh_timer.stop()
        self._sync_update_buttons()

    def _on_download_finished(self, name: str, status: str):
        logger.info("Update-page download finished for %s with status=%s", name, status)
        if status == "Completed":
            timestamp = int(time.time())
            self.settings_store.set_last_update_at(name, timestamp)
            entry = self._entry_for(name)
            if entry is not None:
                entry.set_last_update_at(timestamp)
        self._sync_update_buttons()
        if not self.service.is_busy():
            self._refresh_timer.start(2500)

    def _on_library_changed(self, name: str):
        logger.info("Update page noticed library_changed")
        if self.isVisible() and not self.service.is_busy():
            self._refresh_timer.stop()
            self._refresh_timer.start(0)

    def _on_candidate_checked(self, request_id: int, candidate: dict, result: dict | None, error: str):
        if request_id != self._check_request_id:
            return

        self._completed_checks += 1
        if error in {DISABLED_CHECK_ERROR, UNSUPPORTED_CHECK_ERROR}:
            pass
        elif error:
            self._check_errors += 1
            logger.warning("Update check failed for %s: %s", candidate.get("name"), error)
        elif result is not None:
            self._available_updates.append(result)
            self._last_check_counts_by_name[str(candidate.get("name") or "")] = int(result.get("new_chapters", 0) or 0)
            self._available_updates.sort(key=lambda item: item["webtoon"].name.lower())
            self._apply_filter()

        if self._pending_checks > 0:
            self.status_label.setText(
                f"Checking {self._completed_checks} / {self._pending_checks} saved titles..."
            )

        if self._completed_checks >= self._pending_checks:
            self._finish_check_cycle()

    def _finish_check_cycle(self):
        count = len(self._available_updates)
        if count == 0:
            self.status_label.setText("No updates found")
        elif count == 1:
            self.status_label.setText("1 title has updates")
        else:
            self.status_label.setText(f"{count} titles have updates")

        if self._check_errors:
            suffix = "title" if self._check_errors == 1 else "titles"
            self.set_error_text(f"Could not check {self._check_errors} saved {suffix}.")
        else:
            self.set_error_text("")

        self.settings_store.save_remote_update_counts(
            self._last_check_counts_by_name,
            clear_missing_names=[item["name"] for item in self._candidates],
        )
        self._empty_message = "No updates found right now."
        self._apply_filter()
        self.check_cycle_finished.emit(
            self._current_check_reason,
            count,
            int(self._check_errors),
        )

    def _current_empty_message(self) -> str:
        if self._pending_search.strip():
            return "No update cards match your search."
        if self._completed_checks < self._pending_checks:
            return self._empty_message
        return "No updates found right now."

    def _relayout_cards(self):
        if self._cards_layout is None or not self._entry_widgets:
            return

        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            widget = item.widget()
            if widget is None:
                continue

        viewport_width = max(
            1,
            self.scroll.viewport().width()
            - self.scroll.contentsMargins().left()
            - self.scroll.contentsMargins().right(),
        )
        card_span = UPDATE_CARD_WIDTH + UPDATE_CARD_SPACING
        columns = max(1, viewport_width // max(1, card_span))

        for index, widget in enumerate(self._entry_widgets):
            row = index // columns
            column = index % columns
            self._cards_layout.addWidget(widget, row, column, Qt.AlignTop | Qt.AlignLeft)
