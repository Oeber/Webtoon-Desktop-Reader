from PySide6.QtWidgets import (
    QMainWindow, QStackedWidget,
    QApplication, QWidget, QHBoxLayout, QPushButton, QVBoxLayout, QMessageBox, QLabel
)

from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPen, QPixmap, QShortcut, Qt
from PySide6.QtCore import QSize, QTimer
import time

import qtawesome as qta
from core.app_logging import get_logger
from stores.webtoon_settings_store import get_instance as get_webtoon_settings
from gui.common.styles import (
    ACCENT,
    LOADING_DETAIL_LABEL_STYLE,
    LOADING_TITLE_LABEL_STYLE,
    MAIN_WINDOW_CHAPTER_OVERLAY_STYLE,
    SIDEBAR_BUTTON_STYLE,
    SIDEBAR_STYLE,
    STACK_BG_STYLE,
    sidebar_button_style,
)

from gui.library.library_page import LibraryPage
from gui.library.detail_page import DetailPage
from gui.viewer.viewer_page import ViewerPage
from gui.settings.settings_page import (
    SettingsPage,
)
from stores.settings_store import (
    LIBRARY_UPDATE_CHECK_ON_STARTUP_KEY,
    LIBRARY_UPDATE_INTERVAL_MINUTES_KEY,
    LIBRARY_UPDATE_LAST_CHECK_AT_KEY,
    LIBRARY_UPDATE_LAST_NOTIFIED_SIGNATURE_KEY,
    LIBRARY_UPDATE_LAST_RESULT_KEY,
    load_setting,
    save_setting,
)
from gui.discovery.site_browser_page import SiteBrowserPage
from gui.discovery.detail_page import DiscoveryDetailPage
from gui.common.site_auth_dialog import SiteAuthDialog
from gui.downloader.downloader_page import DownloaderPage
from gui.downloader.update_page import UpdatePage
from gui.downloader.download_widgets import SpinnerCircle
from gui.search.global_search import GlobalSearchDialog

logger = get_logger(__name__)
APP_TITLE = "Webtoon Desktop Reader"

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        logger.info("Initializing main window")

        self.set_window_context_title()
        self.resize(1400, 900)
        self._suppress_detail_open_until = 0.0
        self.settings_store = get_webtoon_settings()

        self.stack = QStackedWidget()
        self.stack.setStyleSheet(STACK_BG_STYLE)

        self.library  = LibraryPage(self)
        self.detail   = DetailPage(self)
        self.viewer   = ViewerPage(self)
        self.settings = SettingsPage(self)
        self.discovery = SiteBrowserPage(self)
        self.discovery_detail = DiscoveryDetailPage(self)

        self.stack.addWidget(self.library)
        self.stack.addWidget(self.detail)
        self.stack.addWidget(self.viewer)
        self.stack.addWidget(self.settings)
        self.stack.addWidget(self.discovery)
        self.stack.addWidget(self.discovery_detail)

        root = QWidget()
        root.setStyleSheet(STACK_BG_STYLE)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Sidebar
        self.sidebar = QWidget()
        self.sidebar.setStyleSheet(SIDEBAR_STYLE)

        self.sidebar_expanded_width = 200
        self.sidebar_collapsed_width = 50
        self.sidebar_open = False

        self.sidebar.setFixedWidth(self.sidebar_collapsed_width)
        self._sidebar_target = "library"
        self._download_sidebar_active = False

        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(10, 10, 10, 10)
        sidebar_layout.setSpacing(10)

        icon_color = "#d8b7b0"

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
        self.btn_library.clicked.connect(self.open_library)

        self.toggle_btn.setStyleSheet(SIDEBAR_BUTTON_STYLE)
        self.btn_library.setStyleSheet(SIDEBAR_BUTTON_STYLE)

        if not self.sidebar_open:
            self.btn_library.setText("")

        sidebar_layout.addWidget(self.btn_library)
        self.btn_discovery = QPushButton()
        self.btn_discovery.setIcon(qta.icon("fa5s.compass", color=icon_color))
        self.btn_discovery.setIconSize(QSize(16, 16))
        self.btn_discovery.setStyleSheet(SIDEBAR_BUTTON_STYLE)
        self.btn_discovery.clicked.connect(self.open_discovery)
        sidebar_layout.addWidget(self.btn_discovery)
        self.downloader = DownloaderPage(self)
        self.updates = UpdatePage(self)
        self.library.attach_update_service(self.updates.service)
        self.library.attach_manual_download_service(self.downloader.service)
        self.detail.attach_update_service(self.updates.service)
        self.detail.attach_manual_download_service(self.downloader.service)
        self.discovery.attach_update_service(self.updates.service)
        self.discovery.attach_manual_download_service(self.downloader.service)
        self.downloader.attach_history_service(self.updates.service)
        self.stack.addWidget(self.downloader)
        self.stack.addWidget(self.updates)
        self.btn_downloader = QPushButton()
        self.btn_downloader.setIcon(qta.icon("fa5s.download", color=icon_color))
        self.btn_downloader.setIconSize(QSize(16, 16))
        self.btn_downloader.setStyleSheet(SIDEBAR_BUTTON_STYLE)
        self.btn_downloader.clicked.connect(self.open_downloader)
        sidebar_layout.addWidget(self.btn_downloader)

        self.btn_updates = QPushButton()
        self.btn_updates.setIcon(qta.icon("fa5s.sync", color=icon_color))
        self.btn_updates.setIconSize(QSize(16, 16))
        self.btn_updates.setStyleSheet(SIDEBAR_BUTTON_STYLE)
        self.btn_updates.clicked.connect(self.open_updates)
        sidebar_layout.addWidget(self.btn_updates)

        sidebar_layout.addStretch()

        self.btn_settings = QPushButton()
        self.btn_settings.setIcon(qta.icon("fa5s.cog", color=icon_color))
        self.btn_settings.setIconSize(QSize(16, 16))
        self.btn_settings.setStyleSheet(SIDEBAR_BUTTON_STYLE)
        self.btn_settings.clicked.connect(self.open_settings)
        sidebar_layout.addWidget(self.btn_settings)
        self.btn_settings.setStyleSheet(SIDEBAR_BUTTON_STYLE)


        layout.addWidget(self.sidebar)
        layout.addWidget(self.stack)

        self.setCentralWidget(root)
        self._chapter_loading_overlay = QWidget(self.stack)
        self._chapter_loading_overlay.setStyleSheet(MAIN_WINDOW_CHAPTER_OVERLAY_STYLE)
        self._chapter_loading_overlay.hide()
        chapter_overlay_layout = QVBoxLayout(self._chapter_loading_overlay)
        chapter_overlay_layout.setContentsMargins(24, 24, 24, 24)
        chapter_overlay_layout.setSpacing(10)
        chapter_overlay_layout.setAlignment(Qt.AlignCenter)

        self._chapter_loading_spinner = SpinnerCircle(self._chapter_loading_overlay)
        self._chapter_loading_spinner.set_spinning()
        self._chapter_loading_label = QLabel("Loading chapter...")
        self._chapter_loading_label.setAlignment(Qt.AlignCenter)
        self._chapter_loading_label.setStyleSheet(LOADING_TITLE_LABEL_STYLE)
        self._chapter_loading_detail_label = QLabel("")
        self._chapter_loading_detail_label.setAlignment(Qt.AlignCenter)
        self._chapter_loading_detail_label.setStyleSheet(LOADING_DETAIL_LABEL_STYLE)

        chapter_overlay_layout.addWidget(self._chapter_loading_spinner, 0, Qt.AlignCenter)
        chapter_overlay_layout.addWidget(self._chapter_loading_label)
        chapter_overlay_layout.addWidget(self._chapter_loading_detail_label)

        self.global_search = GlobalSearchDialog(self)
        self.global_search_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self.global_search_shortcut.setContext(Qt.ApplicationShortcut)
        self.global_search_shortcut.activated.connect(self.global_search.open_dialog)
        self._shutdown_done = False
        self._download_sidebar_jobs = {}
        self._download_sidebar_icon_state = None
        self._download_sidebar_spin = qta.Spin(self.btn_downloader)
        self._connect_download_sidebar_signals(self.downloader.service, "manual")
        self._connect_download_sidebar_signals(self.updates.service, "updates")
        self.viewer.chapter_loading_started.connect(self._on_viewer_chapter_loading_started)
        self.viewer.chapter_loading_finished.connect(self._on_viewer_chapter_loading_finished)
        self.updates.check_cycle_finished.connect(self._on_library_update_check_finished)
        self._library_update_schedule_timer = QTimer(self)
        self._library_update_schedule_timer.setSingleShot(False)
        self._library_update_schedule_timer.timeout.connect(self._run_interval_library_update_check)
        self._library_update_interval_check_queued = False
        self._refresh_download_sidebar_indicator()
        self._refresh_sidebar_nav_state()
        self._apply_sidebar_button_layout()
        self.refresh_library_update_schedule()
        QTimer.singleShot(1500, self.settings.schedule_startup_update_check)
        QTimer.singleShot(2500, self._run_startup_library_update_check_if_due)

    def iconSizeHint(self) -> QSize:
        return QSize(60, 90)

    def set_window_context_title(self, webtoon_name: str | None = None):
        title = APP_TITLE if not webtoon_name else f"{APP_TITLE} | {webtoon_name}"
        self.setWindowTitle(title)

    def _clear_new_chapter_marker(self, webtoon, chapter_index: int):
        if webtoon is None or chapter_index < 0 or chapter_index >= len(webtoon.chapters):
            return
        chapter = webtoon.chapters[chapter_index]
        if self.settings_store.get_latest_new_chapter(webtoon.name) == chapter:
            self.settings_store.clear_latest_new_chapter(webtoon.name)

    # ------------------------------------------------------------------ #

    def open_library(self):
        self._hide_chapter_loading_overlay()
        self.set_window_context_title()
        self.library.refresh_dynamic_state()
        self.stack.setCurrentWidget(self.library)
        self._set_sidebar_target("library")

    def open_downloader(self):
        self._hide_chapter_loading_overlay()
        self.set_window_context_title()
        self.stack.setCurrentWidget(self.downloader)
        self._set_sidebar_target("downloader")

    def open_discovery(self):
        self._hide_chapter_loading_overlay()
        self.set_window_context_title()
        self.stack.setCurrentWidget(self.discovery)
        self._set_sidebar_target("discovery")

    def open_discovery_search(self, query: str = "", scraper: str = "") -> bool:
        self.open_discovery()
        return self.discovery.apply_command_search(query=query, scraper=scraper)

    def open_discovery_detail(self, entry):
        self._hide_chapter_loading_overlay()
        title = getattr(entry, "title", None) or None
        self.set_window_context_title(title)
        self.discovery_detail.load_entry(entry)
        self.stack.setCurrentWidget(self.discovery_detail)
        self._set_sidebar_target("discovery")

    def open_site_authorization(self, site_name: str, url: str = "") -> bool:
        prior_state = self.windowState()
        dialog = SiteAuthDialog(site_name, url=url, parent=None)
        dialog.setWindowModality(Qt.ApplicationModal)
        result = bool(dialog.exec())
        if self.windowState() != prior_state:
            self.setWindowState(prior_state)
        self.raise_()
        self.activateWindow()
        return result

    def open_settings(self):
        self._hide_chapter_loading_overlay()
        self.set_window_context_title()
        self.stack.setCurrentWidget(self.settings)
        self._set_sidebar_target("settings")

    def reload_scraper_availability(self):
        logger.info("Reloading scrapers across the UI")
        self.discovery.reload_providers(load_catalog=True)
        if self.detail.webtoon is not None:
            self.detail.refresh_remote_state()
        self.updates.refresh_entries()

    def open_detail(self, webtoon, force: bool = False):
        """Show the detail / chapter-list page. Also refreshes progress badges."""
        if not force and time.monotonic() < self._suppress_detail_open_until:
            logger.info("Suppressed detail open for %s", webtoon.name)
            return
        self._hide_chapter_loading_overlay()
        logger.info("Opening detail page for %s", webtoon.name)
        self.library.refresh_progress()
        self.detail.load_webtoon(webtoon, self.library.progress_store)
        self.set_window_context_title(webtoon.name)
        self.stack.setCurrentWidget(self.detail)
        self._set_sidebar_target("library")

    def suppress_detail_open(self, seconds: float):
        logger.info("Suppressing detail open for %.2f seconds", seconds)
        self._suppress_detail_open_until = max(
            self._suppress_detail_open_until,
            time.monotonic() + seconds,
        )

    def open_chapter(self, webtoon, chapter_index: int, scroll_pct: float = 0.0):
        """
        Open viewer at a specific chapter + scroll percentage.
        No continue/restart prompt - caller already decided.
        """
        logger.info(
            "Opening chapter directly for %s index=%d scroll=%.3f",
            webtoon.name,
            chapter_index,
            scroll_pct,
        )
        self._clear_new_chapter_marker(webtoon, chapter_index)
        self.viewer.load_webtoon(webtoon,
                                 start_chapter=chapter_index,
                                 start_scroll=scroll_pct)
        self.set_window_context_title(webtoon.name)
        self.stack.setCurrentWidget(self.viewer)
        self._set_sidebar_target("library")
        self._hide_chapter_loading_overlay()

    def open_chapter_with_prompt(self, webtoon, chapter_index: int):
        """
        Open viewer at a specific chapter and let the viewer
        show the continue/restart dialog if progress exists.
        """
        logger.info("Opening chapter with prompt for %s index=%d", webtoon.name, chapter_index)
        self._clear_new_chapter_marker(webtoon, chapter_index)
        opened = self.viewer.open_chapter_with_prompt(webtoon, chapter_index)
        if not opened:
            self._hide_chapter_loading_overlay()
            return
        self.set_window_context_title(webtoon.name)
        self.stack.setCurrentWidget(self.viewer)
        self._set_sidebar_target("library")
        self._hide_chapter_loading_overlay()

    def open_viewer(self, webtoon):
        """Legacy: open viewer from chapter 0."""
        self.open_chapter(webtoon, 0)

    def open_updates(self):
        logger.info("Opening updates page")
        self._hide_chapter_loading_overlay()
        self.updates.refresh_entries(reason="open")
        self.set_window_context_title()
        self.stack.setCurrentWidget(self.updates)
        self._set_sidebar_target("updates")

    def refresh_library_update_schedule(self):
        interval_minutes = int(load_setting(LIBRARY_UPDATE_INTERVAL_MINUTES_KEY, 60) or 0)
        if interval_minutes <= 0:
            self._library_update_schedule_timer.stop()
            self._library_update_interval_check_queued = False
            return

        interval_ms = max(60_000, interval_minutes * 60 * 1000)
        self._library_update_schedule_timer.start(interval_ms)
        startup_checks_enabled = bool(load_setting(LIBRARY_UPDATE_CHECK_ON_STARTUP_KEY, False))
        if startup_checks_enabled or not self._library_update_check_due():
            self._library_update_interval_check_queued = False
            return
        if self._library_update_interval_check_queued:
            return
        self._library_update_interval_check_queued = True
        QTimer.singleShot(1000, self._run_queued_interval_library_update_check)

    def run_library_update_check(self, reason: str = "scheduled") -> bool:
        if self.updates.service.is_busy():
            logger.info("Skipping library update check because update downloads are active")
            return False
        return self.updates.run_background_check(reason=reason)

    def _library_update_check_due(self, allow_zero_interval: bool = False) -> bool:
        interval_minutes = int(load_setting(LIBRARY_UPDATE_INTERVAL_MINUTES_KEY, 60) or 0)
        if interval_minutes <= 0:
            return bool(allow_zero_interval)

        last_checked_at = int(load_setting(LIBRARY_UPDATE_LAST_CHECK_AT_KEY, 0) or 0)
        if last_checked_at <= 0:
            return True

        return (int(time.time()) - last_checked_at) >= (interval_minutes * 60)

    def _run_startup_library_update_check_if_due(self):
        if not load_setting(LIBRARY_UPDATE_CHECK_ON_STARTUP_KEY, False):
            return
        if not self._library_update_check_due(allow_zero_interval=True):
            return
        self.run_library_update_check(reason="auto_startup")

    def _run_interval_library_update_check(self):
        if not self._library_update_check_due():
            return
        self.run_library_update_check(reason="auto_interval")

    def _run_queued_interval_library_update_check(self):
        self._library_update_interval_check_queued = False
        self._run_interval_library_update_check()

    def _library_update_result_text(self, count: int, errors: int) -> str:
        if count <= 0:
            summary = "No updates found."
        elif count == 1:
            summary = "1 title has updates."
        else:
            summary = f"{count} titles have updates."

        if errors <= 0:
            return summary
        if errors == 1:
            return f"{summary} 1 title could not be checked."
        return f"{summary} {errors} titles could not be checked."

    def _on_library_update_check_finished(self, reason: str, count: int, errors: int):
        checked_at = int(time.time())
        result_text = self._library_update_result_text(int(count), int(errors))
        save_setting(LIBRARY_UPDATE_LAST_CHECK_AT_KEY, checked_at)
        save_setting(LIBRARY_UPDATE_LAST_RESULT_KEY, result_text)
        self.settings.refresh_library_update_status()
        self.library.refresh_dynamic_state()

        if reason == "settings_manual":
            self.settings.notify_library_update_check_completed()

        if reason not in {"auto_startup", "auto_interval"}:
            return

        signature = self.updates.available_update_signature() if count > 0 else ""
        last_notified_signature = str(load_setting(LIBRARY_UPDATE_LAST_NOTIFIED_SIGNATURE_KEY, "") or "")
        if not signature:
            save_setting(LIBRARY_UPDATE_LAST_NOTIFIED_SIGNATURE_KEY, "")
            return
        if signature == last_notified_signature:
            return

        save_setting(LIBRARY_UPDATE_LAST_NOTIFIED_SIGNATURE_KEY, signature)

    def _position_chapter_loading_overlay(self):
        self._chapter_loading_overlay.setGeometry(self.stack.rect())

    def _show_chapter_loading_overlay(self, webtoon_name: str, chapter: str):
        self._position_chapter_loading_overlay()
        self._chapter_loading_spinner.set_spinning()
        self._chapter_loading_label.setText(f"Loading {chapter}...")
        self._chapter_loading_detail_label.setText(webtoon_name)
        self._chapter_loading_overlay.show()
        self._chapter_loading_overlay.raise_()
        QApplication.processEvents()

    def _hide_chapter_loading_overlay(self):
        self._chapter_loading_overlay.hide()

    def _on_viewer_chapter_loading_started(self, webtoon_name: str, chapter: str):
        if self.stack.currentWidget() is self.viewer:
            return
        self._show_chapter_loading_overlay(webtoon_name, chapter)

    def _on_viewer_chapter_loading_finished(self, webtoon_name: str, chapter: str):
        self._hide_chapter_loading_overlay()
    
    def toggle_sidebar(self):
        if self.sidebar_open:
            self.sidebar.setFixedWidth(self.sidebar_collapsed_width)
            self.btn_library.setText("")
            self.btn_discovery.setText("")
            self.btn_settings.setText("")
            self.btn_updates.setText("")
            self.sidebar_open = False
        else:
            self.sidebar.setFixedWidth(self.sidebar_expanded_width)
            self.btn_library.setText("  Library")
            self.btn_discovery.setText("  Discover")
            self.btn_settings.setText("  Settings")
            self.btn_updates.setText("  Updates")
            self.sidebar_open = True
        self._apply_sidebar_button_layout()
        self._refresh_download_sidebar_indicator()
        self._refresh_sidebar_nav_state()
        logger.info("Sidebar toggled, open=%s", self.sidebar_open)

    def _apply_sidebar_button_layout(self):
        button_style = sidebar_button_style(self.sidebar_open)
        for button in (
            self.toggle_btn,
            self.btn_library,
            self.btn_discovery,
            self.btn_downloader,
            self.btn_updates,
            self.btn_settings,
        ):
            button.setStyleSheet(button_style)

    def _set_sidebar_target(self, target: str):
        self._sidebar_target = target
        self._refresh_sidebar_nav_state()

    def _sidebar_icon_color(self, button_name: str) -> str:
        if button_name == self._sidebar_target:
            return ACCENT
        return "#d8b7b0"

    def _refresh_sidebar_nav_state(self):
        button_specs = (
            (self.btn_library, "library", "fa5s.book-open"),
            (self.btn_discovery, "discovery", "fa5s.compass"),
            (self.btn_updates, "updates", "fa5s.sync"),
            (self.btn_settings, "settings", "fa5s.cog"),
        )
        for button, name, icon_name in button_specs:
            active = name == self._sidebar_target
            button.setProperty("active", active)
            button.style().unpolish(button)
            button.style().polish(button)
            button.setIcon(qta.icon(icon_name, color=self._sidebar_icon_color(name)))

        if not self._download_sidebar_active:
            downloader_active = self._sidebar_target == "downloader"
            self.btn_downloader.setProperty("active", downloader_active)
            self.btn_downloader.style().unpolish(self.btn_downloader)
            self.btn_downloader.style().polish(self.btn_downloader)
            self.btn_downloader.setIcon(
                qta.icon("fa5s.download", color=self._sidebar_icon_color("downloader"))
            )

    def shutdown_background_tasks(self):
        if self._shutdown_done:
            return

        self._shutdown_done = True
        logger.info("Stopping background tasks before app exit")

        try:
            self.downloader.service.shutdown()
        except Exception:
            logger.exception("Failed to shut down downloader service")

        try:
            self.updates.service.shutdown()
        except Exception:
            logger.exception("Failed to shut down update service")

        try:
            self.viewer.shutdown()
        except Exception:
            logger.exception("Failed to shut down viewer")

    def _active_download_summaries(self) -> list[str]:
        active = []
        try:
            active.extend(self.downloader.service.active_download_names())
        except Exception:
            logger.exception("Failed to read downloader activity")
        try:
            active.extend(self.updates.service.active_download_names())
        except Exception:
            logger.exception("Failed to read update activity")
        return active

    def _confirm_close_with_active_downloads(self) -> bool:
        active = self._active_download_summaries()
        if not active:
            return True

        count = len(active)
        if count == 1:
            detail_text = active[0]
        elif count == 2:
            detail_text = ", ".join(active)
        else:
            detail_text = f"{active[0]}, {active[1]}, and {count - 2} more"

        result = QMessageBox.warning(
            self,
            "Downloads in Progress",
            "Downloads are still running.\n\n"
            f"Closing now will cancel {count} active download(s): {detail_text}.\n"
            "The source URL will still be saved for later updates.\n\n"
            "Close anyway?",
            QMessageBox.Close | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        return result == QMessageBox.Close

    def _connect_download_sidebar_signals(self, service, prefix: str):
        service.download_started.connect(
            lambda name, prefix=prefix: self._on_sidebar_download_started(prefix, name)
        )
        service.name_resolved.connect(
            lambda old_name, new_name, prefix=prefix: self._on_sidebar_download_renamed(prefix, old_name, new_name)
        )
        service.progress_changed.connect(
            lambda name, current, total, prefix=prefix: self._on_sidebar_download_progress(prefix, name, current, total)
        )
        service.download_finished.connect(
            lambda name, status, prefix=prefix: self._on_sidebar_download_finished(prefix, name)
        )

    def _sidebar_job_key(self, prefix: str, name: str) -> str:
        return f"{prefix}:{name}"

    def _on_sidebar_download_started(self, prefix: str, name: str):
        self._download_sidebar_jobs[self._sidebar_job_key(prefix, name)] = {
            "name": name,
            "current": 0,
            "total": 0,
        }
        self._refresh_download_sidebar_indicator()

    def _on_sidebar_download_renamed(self, prefix: str, old_name: str, new_name: str):
        old_key = self._sidebar_job_key(prefix, old_name)
        state = self._download_sidebar_jobs.pop(old_key, None)
        if state is None:
            state = {"name": new_name, "current": 0, "total": 0}
        state["name"] = new_name
        self._download_sidebar_jobs[self._sidebar_job_key(prefix, new_name)] = state
        self._refresh_download_sidebar_indicator()

    def _on_sidebar_download_progress(self, prefix: str, name: str, current: int, total: int):
        key = self._sidebar_job_key(prefix, name)
        state = self._download_sidebar_jobs.setdefault(
            key,
            {"name": name, "current": 0, "total": 0},
        )
        state["current"] = max(0, int(current))
        state["total"] = max(0, int(total))
        self._refresh_download_sidebar_indicator()

    def _on_sidebar_download_finished(self, prefix: str, name: str):
        self._download_sidebar_jobs.pop(self._sidebar_job_key(prefix, name), None)
        self._refresh_download_sidebar_indicator()

    def _download_sidebar_totals(self) -> tuple[int, int]:
        current = 0
        total = 0
        for state in self._download_sidebar_jobs.values():
            total += max(0, int(state["total"]))
            current += min(max(0, int(state["current"])), max(0, int(state["total"])))
        return current, total

    def _refresh_download_sidebar_indicator(self):
        active_count = len(self._download_sidebar_jobs)
        current, total = self._download_sidebar_totals()

        if active_count > 0:
            remaining = max(0, total - current)
            icon_state = ("progress", "active")
            if icon_state != self._download_sidebar_icon_state:
                self._download_sidebar_active = True
                self.btn_downloader.setIcon(
                    qta.icon("fa5s.spinner", color=ACCENT, animation=self._download_sidebar_spin)
                )
                self._download_sidebar_icon_state = icon_state
            self.btn_downloader.setProperty("active", self._sidebar_target == "downloader")
            self.btn_downloader.style().unpolish(self.btn_downloader)
            self.btn_downloader.style().polish(self.btn_downloader)
            if self.sidebar_open:
                if total > 0:
                    self.btn_downloader.setText(f"  Download {current} done, {remaining} left")
                else:
                    self.btn_downloader.setText(f"  Download ({active_count} active)")
            else:
                self.btn_downloader.setText("")
            if total > 0:
                self.btn_downloader.setToolTip(f"{active_count} active download(s): {current} done, {remaining} left")
            else:
                self.btn_downloader.setToolTip(f"{active_count} active download(s)")
            return

        if self._download_sidebar_icon_state != ("idle", None):
            self._download_sidebar_icon_state = ("idle", None)
        self._download_sidebar_active = False
        self.btn_downloader.setToolTip("Open downloader")
        if self.sidebar_open:
            self.btn_downloader.setText("  Download")
        else:
            self.btn_downloader.setText("")
        self._refresh_sidebar_nav_state()

    def closeEvent(self, event):
        if not self._confirm_close_with_active_downloads():
            event.ignore()
            return
        self.shutdown_background_tasks()
        super().closeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_chapter_loading_overlay()

