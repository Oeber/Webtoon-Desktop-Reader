from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QMessageBox, QStackedWidget, QWidget

from core.app_logging import get_logger
from gui.common.styles import STACK_BG_STYLE
from gui.common.strings import t
from gui.common.browser_html_fetcher import BrowserHtmlFetcher
from gui.discovery.detail_page import DiscoveryDetailPage
from gui.discovery.site_browser_page import SiteBrowserPage
from gui.downloader.downloader_page import DownloaderPage
from gui.downloader.update_page import UpdatePage
from gui.library.detail_page import DetailPage
from gui.library.library_page import LibraryPage
from gui.main_window_controllers import (
    ChapterLoadingOverlayController,
    LibraryUpdateScheduler,
    NotificationCenterController,
    SidebarController,
    SiteAuthorizationController,
    WindowNavigator,
)
from gui.search.global_search import GlobalSearchDialog
from gui.settings.settings_page import SettingsPage
from gui.viewer.viewer_page import ViewerPage
from stores.webtoon_settings_store import get_instance as get_webtoon_settings

logger = get_logger(__name__)
APP_TITLE = t("app.title")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        logger.info("Initializing main window")

        self.settings_store = get_webtoon_settings()
        self.set_window_context_title()
        self.resize(1400, 900)

        self.stack = QStackedWidget()
        self.stack.setStyleSheet(STACK_BG_STYLE)

        self.library = LibraryPage(self)
        self.detail = DetailPage(self)
        self.viewer = ViewerPage(self)
        self.settings = SettingsPage(self)
        self.discovery = SiteBrowserPage(self)
        self.discovery_detail = DiscoveryDetailPage(self)
        self.downloader = DownloaderPage(self)
        self.updates = UpdatePage(self)
        self.browser_fetcher = BrowserHtmlFetcher(self)
        self.notifications = NotificationCenterController(self)

        for page in (
            self.library,
            self.detail,
            self.viewer,
            self.settings,
            self.discovery,
            self.discovery_detail,
            self.downloader,
            self.updates,
        ):
            self.stack.addWidget(page)

        self._attach_shared_services()

        root = QWidget()
        root.setStyleSheet(STACK_BG_STYLE)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar_controller = SidebarController(self)
        self.sidebar_controller.attach_to_layout(layout)
        layout.addWidget(self.stack)
        self.setCentralWidget(root)

        self.chapter_overlay = ChapterLoadingOverlayController(self.stack)
        self.site_authorization = SiteAuthorizationController(self)
        self.navigator = WindowNavigator(self)
        self.library_update_scheduler = LibraryUpdateScheduler(self)

        self.global_search = GlobalSearchDialog(self)
        self.global_search_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self.global_search_shortcut.setContext(Qt.ApplicationShortcut)
        self.global_search_shortcut.activated.connect(self.global_search.open_dialog)

        self._shutdown_done = False
        self.sidebar_controller.connect_download_signals(self.downloader.service, "manual")
        self.sidebar_controller.connect_download_signals(self.updates.service, "updates")
        self.notifications.unread_count_changed.connect(self.sidebar_controller.set_notification_unread_count)
        self.viewer.chapter_loading_started.connect(self._on_viewer_chapter_loading_started)
        self.viewer.chapter_loading_finished.connect(self._on_viewer_chapter_loading_finished)
        self.sidebar_controller.refresh_download_indicator()
        self.notifications.refresh_unread_count()
        self.library_update_scheduler.refresh_schedule()

    def _attach_shared_services(self):
        self.downloader.service.set_browser_fetcher(self.browser_fetcher)
        self.updates.service.set_browser_fetcher(self.browser_fetcher)
        self.library.attach_update_service(self.updates.service)
        self.library.attach_manual_download_service(self.downloader.service)
        self.detail.attach_update_service(self.updates.service)
        self.detail.attach_manual_download_service(self.downloader.service)
        self.discovery.attach_update_service(self.updates.service)
        self.discovery.attach_manual_download_service(self.downloader.service)
        self.downloader.attach_history_service(self.updates.service)
        self.notifications.connect_service(self.downloader.service, history_kind="download")
        self.notifications.connect_service(self.updates.service, history_kind="update")
        self.notifications.connect_update_check_cycle(self.updates)

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

    def open_library(self):
        self.navigator.open_library()

    def open_downloader(self):
        self.navigator.open_downloader()

    def open_discovery(self):
        self.navigator.open_discovery()

    def open_discovery_search(self, query: str = "", scraper: str = "") -> bool:
        return self.navigator.open_discovery_search(query=query, scraper=scraper)

    def open_discovery_detail(self, entry):
        self.navigator.open_discovery_detail(entry)

    def open_site_authorization(self, site_name: str, url: str = "") -> bool:
        return self.site_authorization.open_dialog(site_name, url=url)

    def open_settings(self):
        self.navigator.open_settings()

    def open_notifications(self):
        self.notifications.open_dialog()

    def reload_scraper_availability(self):
        self.navigator.reload_scraper_availability()

    def open_detail(self, webtoon, force: bool = False):
        self.navigator.open_detail(webtoon, force=force)

    def suppress_detail_open(self, seconds: float):
        self.navigator.suppress_detail_open(seconds)

    def open_chapter(self, webtoon, chapter_index: int, scroll_pct: float = 0.0):
        self.navigator.open_chapter(webtoon, chapter_index, scroll_pct=scroll_pct)

    def open_chapter_with_prompt(self, webtoon, chapter_index: int):
        self.navigator.open_chapter_with_prompt(webtoon, chapter_index)

    def open_viewer(self, webtoon):
        self.navigator.open_viewer(webtoon)

    def open_updates(self):
        self.navigator.open_updates()

    def refresh_library_update_schedule(self):
        self.library_update_scheduler.refresh_schedule()

    def run_library_update_check(self, reason: str = "scheduled") -> bool:
        return self.library_update_scheduler.run_check(reason=reason)

    def toggle_sidebar(self):
        self.sidebar_controller.toggle()

    def _on_viewer_chapter_loading_started(self, webtoon_name: str, chapter: str):
        self.chapter_overlay.on_viewer_chapter_loading_started(
            self.stack.currentWidget(),
            self.viewer,
            webtoon_name,
            chapter,
        )

    def _on_viewer_chapter_loading_finished(self, webtoon_name: str, chapter: str):
        self.chapter_overlay.on_viewer_chapter_loading_finished(webtoon_name, chapter)

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
            t("main.close.downloads_title"),
            t("main.close.downloads_message", count=count, detail_text=detail_text),
            QMessageBox.Close | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        return result == QMessageBox.Close

    def closeEvent(self, event):
        if not self._confirm_close_with_active_downloads():
            event.ignore()
            return
        self.shutdown_background_tasks()
        super().closeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.chapter_overlay.position()


