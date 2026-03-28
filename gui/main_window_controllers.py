import time
from typing import TYPE_CHECKING

import qtawesome as qta
from PySide6.QtCore import QObject, Signal, QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.app_logging import get_logger
from gui.common.notification_center import NotificationCenterDialog
from gui.common.styles import (
    ACCENT,
    LOADING_DETAIL_LABEL_STYLE,
    LOADING_TITLE_LABEL_STYLE,
    MAIN_WINDOW_CHAPTER_OVERLAY_STYLE,
    SIDEBAR_STYLE,
    sidebar_button_style,
)
from gui.downloader.download_widgets import SpinnerCircle
from library.library_manager import build_webtoon_from_folder
from stores.download_history_store import get_instance as get_download_history
from stores.notification_store import get_instance as get_notification_store
from stores.settings_store import (
    LIBRARY_UPDATE_CHECK_ON_STARTUP_KEY,
    LIBRARY_UPDATE_INTERVAL_MINUTES_KEY,
    LIBRARY_UPDATE_LAST_CHECK_AT_KEY,
    LIBRARY_UPDATE_LAST_NOTIFIED_SIGNATURE_KEY,
    LIBRARY_UPDATE_LAST_RESULT_KEY,
    load_setting,
    load_library_path,
    save_setting,
)

if TYPE_CHECKING:
    from gui.main_window import MainWindow


logger = get_logger(__name__)
SIDEBAR_ICON_COLOR = "#d8b7b0"
STARTUP_LIBRARY_UPDATE_DISCOVERY_RETRY_MS = 15000


class NotificationCenterController(QObject):
    unread_count_changed = Signal(int)

    def __init__(self, window: "MainWindow"):
        super().__init__(window)
        self.window = window
        self.store = get_notification_store()
        self.history_store = get_download_history()
        self.dialog = NotificationCenterDialog(self, parent=window)
        self._last_auth_notification_at: dict[tuple[str, str, str], float] = {}
        self.refresh_unread_count()

    def connect_service(self, service, *, history_kind: str):
        service.download_finished.connect(
            lambda name, status, kind=history_kind: self._on_download_finished(kind, name, status)
        )
        service.auth_required.connect(
            lambda site_name, url, preferred_name, chapter_urls, kind=history_kind:
                self._on_auth_required(kind, site_name, url, preferred_name, chapter_urls)
        )

    def connect_update_check_cycle(self, updates_page):
        updates_page.check_cycle_finished.connect(self._on_check_cycle_finished)

    def open_dialog(self):
        self.refresh_dialog()
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def refresh_dialog(self):
        self.dialog.refresh_entries(self.store.list_entries(), self.store.unread_count())

    def refresh_unread_count(self):
        count = self.store.unread_count()
        self.unread_count_changed.emit(count)
        if self.dialog.isVisible():
            self.refresh_dialog()

    def mark_all_read(self):
        self.store.mark_all_read()
        self.refresh_unread_count()

    def clear_read(self):
        self.store.clear_read()
        self.refresh_unread_count()

    def toggle_read(self, entry: dict):
        if bool(entry.get("is_read", False)):
            self.store.mark_unread(int(entry.get("id", 0)))
        else:
            self.store.mark_read(int(entry.get("id", 0)))
        self.refresh_unread_count()

    def actions_for(self, entry: dict) -> list[tuple[str, str]]:
        kind = str(entry.get("kind") or "").strip()
        payload = entry.get("action_payload") or {}
        actions: list[tuple[str, str]] = []
        if kind in {"download_completed", "update_completed"} and str(entry.get("webtoon_name") or "").strip():
            actions.append(("open_title", "Open"))
        if kind in {"download_failed", "download_cancelled"} and str(entry.get("source_url") or "").strip():
            actions.append(("retry_download", "Retry"))
        if kind in {"update_failed", "update_cancelled"} and str(entry.get("webtoon_name") or "").strip():
            actions.append(("retry_update", "Retry"))
            actions.append(("open_updates", "Updates"))
        if kind == "update_check_summary":
            actions.append(("open_updates", "Open Updates"))
        if kind == "auth_required" and str(entry.get("site_name") or "").strip():
            actions.append(("authorize", "Authorize"))
        elif kind in {"download_failed", "update_failed"} and str(payload.get("site_name") or entry.get("site_name") or "").strip():
            actions.append(("authorize", "Authorize"))
        return actions

    def execute_action(self, entry: dict, action_key: str):
        notification_id = int(entry.get("id", 0) or 0)
        if notification_id > 0:
            self.store.mark_read(notification_id)

        payload = entry.get("action_payload") or {}
        webtoon_name = str(entry.get("webtoon_name") or payload.get("webtoon_name") or "").strip()
        source_url = str(entry.get("source_url") or payload.get("source_url") or "").strip()
        site_name = str(entry.get("site_name") or payload.get("site_name") or "").strip()

        if action_key == "open_title" and webtoon_name:
            webtoon = build_webtoon_from_folder(load_library_path(), webtoon_name, self.window.settings_store)
            if webtoon is not None:
                self.window.open_detail(webtoon)
        elif action_key == "retry_download" and source_url:
            self.window.open_downloader()
            self.window.downloader.start_download_from_url(source_url, preferred_name=webtoon_name or None)
        elif action_key == "retry_update" and webtoon_name:
            self.window.open_updates()
            self.window.updates.start_update_for_webtoon(webtoon_name)
        elif action_key == "open_updates":
            self.window.open_updates()
        elif action_key == "authorize" and site_name:
            authorized = self.window.open_site_authorization(site_name, url=source_url)
            if authorized:
                retry_kind = str(payload.get("retry_kind") or "").strip()
                if retry_kind == "download" and source_url:
                    self.window.open_downloader()
                    self.window.downloader.start_download_from_url(
                        source_url,
                        preferred_name=webtoon_name or None,
                        chapter_urls=list(payload.get("chapter_urls") or []),
                    )
                elif retry_kind == "update" and webtoon_name:
                    self.window.open_updates()
                    self.window.updates.start_update_for_webtoon(webtoon_name)

        self.refresh_unread_count()

    def _on_download_finished(self, history_kind: str, name: str, status: str):
        entry = self.history_store.get_entry(history_kind, name) or {}
        source_url = str(entry.get("source_url") or "").strip()
        last_error = str(entry.get("last_error") or "").strip()
        site_name = str(self._site_name_for_url(source_url) or "").strip()

        if history_kind == "update":
            if status == "Completed":
                self._add_notification(
                    kind="update_completed",
                    title=f"{name} updated",
                    message="The update finished successfully.",
                    severity="success",
                    webtoon_name=name,
                    source_url=source_url,
                )
                return
            if status in {"Failed", "Cancelled"}:
                message = last_error or ("The update was cancelled." if status == "Cancelled" else "The update failed.")
                self._add_notification(
                    kind="update_cancelled" if status == "Cancelled" else "update_failed",
                    title=f"{name} update {status.lower()}",
                    message=message,
                    severity="warning" if status == "Cancelled" else "error",
                    webtoon_name=name,
                    source_url=source_url,
                    site_name=site_name,
                    action_payload={
                        "retry_kind": "update",
                        "webtoon_name": name,
                        "source_url": source_url,
                        "site_name": site_name,
                    },
                )
                return

        if status == "Completed":
            self._add_notification(
                kind="download_completed",
                title=f"{name} downloaded",
                message="The manual download finished successfully.",
                severity="success",
                webtoon_name=name,
                source_url=source_url,
            )
            return
        if status in {"Failed", "Cancelled"}:
            message = last_error or ("The download was cancelled." if status == "Cancelled" else "The download failed.")
            self._add_notification(
                kind="download_cancelled" if status == "Cancelled" else "download_failed",
                title=f"{name} download {status.lower()}",
                message=message,
                severity="warning" if status == "Cancelled" else "error",
                webtoon_name=name,
                source_url=source_url,
                site_name=site_name,
                action_payload={
                    "retry_kind": "download",
                    "webtoon_name": name,
                    "source_url": source_url,
                    "site_name": site_name,
                },
            )

    def _on_auth_required(self, history_kind: str, site_name: str, url: str, preferred_name, chapter_urls):
        normalized_site = str(site_name or "").strip()
        normalized_url = str(url or "").strip()
        normalized_name = str(preferred_name or "").strip()
        if not normalized_site:
            return

        dedupe_key = (history_kind, normalized_site, normalized_url)
        now = time.monotonic()
        if now - self._last_auth_notification_at.get(dedupe_key, 0.0) < 20.0:
            return
        self._last_auth_notification_at[dedupe_key] = now

        title_target = normalized_name or normalized_site
        message = f"{normalized_site} needs authorization before {title_target} can continue."
        self._add_notification(
            kind="auth_required",
            title=f"Authorization needed for {normalized_site}",
            message=message,
            severity="warning",
            webtoon_name=normalized_name,
            source_url=normalized_url,
            site_name=normalized_site,
            action_payload={
                "retry_kind": str(history_kind or "").strip(),
                "webtoon_name": normalized_name,
                "source_url": normalized_url,
                "site_name": normalized_site,
                "chapter_urls": list(chapter_urls or []),
            },
        )

    def _on_check_cycle_finished(self, reason: str, count: int, errors: int):
        total_count = int(count or 0)
        error_count = int(errors or 0)
        if total_count <= 0 and error_count <= 0:
            return
        if total_count > 0 and error_count > 0:
            message = f"{total_count} title(s) have updates, and {error_count} title(s) could not be checked."
            severity = "warning"
        elif total_count > 0:
            message = f"{total_count} title(s) have updates."
            severity = "info"
        else:
            message = f"{error_count} title(s) could not be checked for updates."
            severity = "warning"
        self._add_notification(
            kind="update_check_summary",
            title="Library update check finished",
            message=message,
            severity=severity,
            action_payload={"reason": str(reason or "")},
        )

    def _add_notification(self, **kwargs):
        self.store.add(**kwargs)
        self.refresh_unread_count()

    def _site_name_for_url(self, source_url: str) -> str:
        if not source_url:
            return ""
        try:
            from scrapers.registry import get_scraper
            return str(getattr(get_scraper(source_url), "site_name", "") or "").strip()
        except Exception:
            return ""


class WindowNavigator:
    def __init__(self, window: "MainWindow"):
        self.window = window
        self._suppress_detail_open_until = 0.0

    def open_library(self):
        self.window.chapter_overlay.hide()
        self.window.set_window_context_title()
        self.window.library.refresh_dynamic_state()
        self.window.stack.setCurrentWidget(self.window.library)
        self.window.sidebar_controller.set_target("library")

    def open_downloader(self):
        self.window.chapter_overlay.hide()
        self.window.set_window_context_title()
        self.window.stack.setCurrentWidget(self.window.downloader)
        self.window.downloader.schedule_open_refresh()
        self.window.sidebar_controller.set_target("downloader")

    def open_discovery(self):
        self.window.chapter_overlay.hide()
        self.window.set_window_context_title()
        self.window.stack.setCurrentWidget(self.window.discovery)
        self.window.discovery.ensure_initial_catalog_loaded()
        self.window.sidebar_controller.set_target("discovery")

    def open_discovery_search(self, query: str = "", scraper: str = "") -> bool:
        self.open_discovery()
        return self.window.discovery.apply_command_search(query=query, scraper=scraper)

    def open_discovery_detail(self, entry):
        self.window.chapter_overlay.hide()
        title = getattr(entry, "title", None) or None
        self.window.set_window_context_title(title)
        self.window.discovery_detail.load_entry(entry)
        self.window.stack.setCurrentWidget(self.window.discovery_detail)
        self.window.sidebar_controller.set_target("discovery")

    def open_settings(self):
        self.window.chapter_overlay.hide()
        self.window.set_window_context_title()
        self.window.stack.setCurrentWidget(self.window.settings)
        self.window.settings.schedule_open_refresh()
        self.window.sidebar_controller.set_target("settings")

    def reload_scraper_availability(self):
        logger.info("Reloading scrapers across the UI")
        self.window.settings.refresh_scraper_reliability()
        self.window.discovery.reload_providers(load_catalog=True)
        if self.window.detail.webtoon is not None:
            self.window.detail.refresh_remote_state()
        self.window.updates.refresh_entries()

    def open_detail(self, webtoon, force: bool = False):
        if not force and time.monotonic() < self._suppress_detail_open_until:
            logger.info("Suppressed detail open for %s", webtoon.name)
            return
        self.window.chapter_overlay.hide()
        logger.info("Opening detail page for %s", webtoon.name)
        self.window.library.refresh_progress()
        self.window.detail.load_webtoon(webtoon, self.window.library.progress_store)
        self.window.set_window_context_title(webtoon.name)
        self.window.stack.setCurrentWidget(self.window.detail)
        self.window.sidebar_controller.set_target("library")

    def suppress_detail_open(self, seconds: float):
        logger.info("Suppressing detail open for %.2f seconds", seconds)
        self._suppress_detail_open_until = max(
            self._suppress_detail_open_until,
            time.monotonic() + seconds,
        )

    def open_chapter(self, webtoon, chapter_index: int, scroll_pct: float = 0.0):
        logger.info(
            "Opening chapter directly for %s index=%d scroll=%.3f",
            webtoon.name,
            chapter_index,
            scroll_pct,
        )
        self.window.detail.suspend_remote_state()
        self.window._clear_new_chapter_marker(webtoon, chapter_index)
        self.window.set_window_context_title(webtoon.name)
        self.window.stack.setCurrentWidget(self.window.viewer)
        self.window.sidebar_controller.set_target("library")
        self.window.viewer.load_webtoon(
            webtoon,
            start_chapter=chapter_index,
            start_scroll=scroll_pct,
        )
        self.window.chapter_overlay.hide()

    def open_chapter_with_prompt(self, webtoon, chapter_index: int):
        logger.info("Opening chapter with prompt for %s index=%d", webtoon.name, chapter_index)
        self.window.detail.suspend_remote_state()
        self.window._clear_new_chapter_marker(webtoon, chapter_index)
        self.window.set_window_context_title(webtoon.name)
        self.window.stack.setCurrentWidget(self.window.viewer)
        self.window.sidebar_controller.set_target("library")
        opened = self.window.viewer.open_chapter_with_prompt(webtoon, chapter_index)
        if not opened:
            self.window.set_window_context_title(webtoon.name)
            self.window.stack.setCurrentWidget(self.window.detail)
            self.window.sidebar_controller.set_target("library")
            self.window.chapter_overlay.hide()
            return
        self.window.chapter_overlay.hide()

    def open_viewer(self, webtoon):
        self.open_chapter(webtoon, 0)

    def open_updates(self):
        logger.info("Opening updates page")
        self.window.chapter_overlay.hide()
        self.window.set_window_context_title()
        self.window.stack.setCurrentWidget(self.window.updates)
        self.window.updates.schedule_open_refresh(reason="open")
        self.window.sidebar_controller.set_target("updates")


class SiteAuthorizationController:
    def __init__(self, window: "MainWindow"):
        self.window = window
        self._dialog_open = False
        self._launch_pending = False

    def open_dialog(self, site_name: str, url: str = "") -> bool:
        if self._dialog_open or self._launch_pending:
            logger.info("Ignoring duplicate site authorization request for %s", site_name)
            return False

        self._launch_pending = True
        dialog = None
        try:
            from gui.common.site_auth_dialog import SiteAuthDialog

            dialog = SiteAuthDialog(site_name, url=url, parent=None)
            dialog.setAttribute(Qt.WA_DeleteOnClose, True)
            dialog.setModal(True)
            self._dialog_open = True
            self._launch_pending = False
            return bool(dialog.exec())
        except Exception as exc:
            self._launch_pending = False
            logger.exception("Failed to open site authorization dialog for %s", site_name)
            QMessageBox.warning(
                self.window,
                "Source Authorization",
                f"Could not open the authorization window for {site_name}: {exc}",
            )
            return False
        finally:
            self._dialog_open = False


class ChapterLoadingOverlayController:
    def __init__(self, stack: QWidget):
        self.stack = stack
        self.overlay = QWidget(stack)
        self.overlay.setStyleSheet(MAIN_WINDOW_CHAPTER_OVERLAY_STYLE)
        self.overlay.hide()

        layout = QVBoxLayout(self.overlay)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignCenter)

        self.spinner = SpinnerCircle(self.overlay)
        self.spinner.set_spinning()

        self.title_label = QLabel("Loading chapter...")
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setStyleSheet(LOADING_TITLE_LABEL_STYLE)

        self.detail_label = QLabel("")
        self.detail_label.setAlignment(Qt.AlignCenter)
        self.detail_label.setStyleSheet(LOADING_DETAIL_LABEL_STYLE)

        layout.addWidget(self.spinner, 0, Qt.AlignCenter)
        layout.addWidget(self.title_label)
        layout.addWidget(self.detail_label)

    def position(self):
        self.overlay.setGeometry(self.stack.rect())

    def show(self, webtoon_name: str, chapter: str):
        self.position()
        self.spinner.set_spinning()
        self.title_label.setText(f"Loading {chapter}...")
        self.detail_label.setText(webtoon_name)
        self.overlay.show()
        self.overlay.raise_()
        QApplication.processEvents()

    def hide(self):
        self.overlay.hide()

    def on_viewer_chapter_loading_started(self, current_widget: QWidget, viewer: QWidget, webtoon_name: str, chapter: str):
        if current_widget is viewer:
            return
        self.show(webtoon_name, chapter)

    def on_viewer_chapter_loading_finished(self, webtoon_name: str, chapter: str):
        self.hide()


class LibraryUpdateScheduler:
    def __init__(self, window: "MainWindow"):
        self.window = window
        self._interval_check_queued = False
        self._schedule_timer = QTimer(window)
        self._schedule_timer.setSingleShot(False)
        self._schedule_timer.timeout.connect(self._run_interval_library_update_check)
        self.window.updates.check_cycle_finished.connect(self.on_check_finished)

        QTimer.singleShot(10000, self.window.settings.schedule_startup_update_check)
        QTimer.singleShot(12000, self._run_startup_library_update_check_if_due)

    def refresh_schedule(self):
        interval_minutes = int(load_setting(LIBRARY_UPDATE_INTERVAL_MINUTES_KEY, 60) or 0)
        if interval_minutes <= 0:
            self._schedule_timer.stop()
            self._interval_check_queued = False
            return

        interval_ms = max(60_000, interval_minutes * 60 * 1000)
        self._schedule_timer.start(interval_ms)

        startup_checks_enabled = bool(load_setting(LIBRARY_UPDATE_CHECK_ON_STARTUP_KEY, False))
        if startup_checks_enabled or not self._check_due():
            self._interval_check_queued = False
            return
        if self._interval_check_queued:
            return
        self._interval_check_queued = True
        QTimer.singleShot(1000, self._run_queued_interval_library_update_check)

    def run_check(self, reason: str = "scheduled") -> bool:
        if self.window.updates.service.is_busy():
            logger.info("Skipping library update check because update downloads are active")
            return False
        if self.window.downloader.service.is_busy():
            logger.info("Skipping library update check because manual downloads are active")
            return False
        return self.window.updates.run_background_check(reason=reason)

    def _check_due(self, allow_zero_interval: bool = False) -> bool:
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
        if not self._check_due(allow_zero_interval=True):
            return
        current_widget = self.window.stack.currentWidget()
        if current_widget is self.window.discovery and self.window.discovery.is_catalog_busy():
            logger.info(
                "Deferring startup library update check because discovery catalog loading is active"
            )
            QTimer.singleShot(
                STARTUP_LIBRARY_UPDATE_DISCOVERY_RETRY_MS,
                self._run_startup_library_update_check_if_due,
            )
            return
        self.run_check(reason="auto_startup")

    def _run_interval_library_update_check(self):
        if not self._check_due():
            return
        self.run_check(reason="auto_interval")

    def _run_queued_interval_library_update_check(self):
        self._interval_check_queued = False
        self._run_interval_library_update_check()

    def _result_text(self, count: int, errors: int) -> str:
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

    def on_check_finished(self, reason: str, count: int, errors: int):
        checked_at = int(time.time())
        result_text = self._result_text(int(count), int(errors))
        save_setting(LIBRARY_UPDATE_LAST_CHECK_AT_KEY, checked_at)
        save_setting(LIBRARY_UPDATE_LAST_RESULT_KEY, result_text)
        self.window.settings.refresh_library_update_status()
        self.window.library.refresh_dynamic_state()

        if reason == "settings_manual":
            self.window.settings.notify_library_update_check_completed()

        if reason not in {"auto_startup", "auto_interval"}:
            return

        signature = self.window.updates.available_update_signature() if count > 0 else ""
        last_notified_signature = str(
            load_setting(LIBRARY_UPDATE_LAST_NOTIFIED_SIGNATURE_KEY, "") or ""
        )
        if not signature:
            save_setting(LIBRARY_UPDATE_LAST_NOTIFIED_SIGNATURE_KEY, "")
            return
        if signature == last_notified_signature:
            return

        save_setting(LIBRARY_UPDATE_LAST_NOTIFIED_SIGNATURE_KEY, signature)


class SidebarController:
    def __init__(self, window: "MainWindow"):
        self.window = window
        self.sidebar_expanded_width = 200
        self.sidebar_collapsed_width = 50
        self.sidebar_open = False
        self._target = "library"
        self._download_active = False
        self._download_jobs = {}
        self._download_icon_state = None

        self.widget = QWidget()
        self.widget.setStyleSheet(SIDEBAR_STYLE)
        self.widget.setFixedWidth(self.sidebar_collapsed_width)

        layout = QVBoxLayout(self.widget)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        self.toggle_btn = self._make_button("fa5s.bars", window.toggle_sidebar)
        layout.addWidget(self.toggle_btn)

        self.btn_library = self._make_button("fa5s.book-open", window.open_library)
        layout.addWidget(self.btn_library)

        self.btn_discovery = self._make_button("fa5s.compass", window.open_discovery)
        layout.addWidget(self.btn_discovery)

        self.btn_downloader = self._make_button("fa5s.download", window.open_downloader)
        layout.addWidget(self.btn_downloader)

        self.btn_updates = self._make_button("fa5s.sync", window.open_updates)
        layout.addWidget(self.btn_updates)

        self.btn_notifications = self._make_button("fa5s.bell", window.open_notifications)
        self._notification_badge = QLabel("", self.btn_notifications)
        self._notification_badge.setAlignment(Qt.AlignCenter)
        self._notification_badge.setStyleSheet(
            "background: #ff8a7a; color: #140d0d; border-radius: 8px; font-size: 10px; font-weight: 700; padding: 0 4px;"
        )
        self._notification_badge.hide()
        layout.addWidget(self.btn_notifications)

        layout.addStretch()

        self.btn_settings = self._make_button("fa5s.cog", window.open_settings)
        layout.addWidget(self.btn_settings)

        self._download_spin = qta.Spin(self.btn_downloader)
        self.apply_button_layout()
        self.refresh_nav_state()
        self.refresh_download_indicator()

    def _make_button(self, icon_name: str, callback) -> QPushButton:
        button = QPushButton()
        button.setIcon(qta.icon(icon_name, color=SIDEBAR_ICON_COLOR))
        button.setIconSize(QSize(16, 16))
        button.clicked.connect(callback)
        return button

    def attach_to_layout(self, layout: QHBoxLayout):
        layout.addWidget(self.widget)

    def connect_download_signals(self, service, prefix: str):
        service.download_started.connect(
            lambda name, prefix=prefix: self._on_download_started(prefix, name)
        )
        service.name_resolved.connect(
            lambda old_name, new_name, prefix=prefix: self._on_download_renamed(
                prefix,
                old_name,
                new_name,
            )
        )
        service.progress_changed.connect(
            lambda name, current, total, prefix=prefix: self._on_download_progress(
                prefix,
                name,
                current,
                total,
            )
        )
        service.download_finished.connect(
            lambda name, status, prefix=prefix: self._on_download_finished(prefix, name)
        )

    def toggle(self):
        if self.sidebar_open:
            self.widget.setFixedWidth(self.sidebar_collapsed_width)
            self.btn_library.setText("")
            self.btn_discovery.setText("")
            self.btn_settings.setText("")
            self.btn_updates.setText("")
            self.btn_notifications.setText("")
            self.sidebar_open = False
        else:
            self.widget.setFixedWidth(self.sidebar_expanded_width)
            self.btn_library.setText("  Library")
            self.btn_discovery.setText("  Discover")
            self.btn_settings.setText("  Settings")
            self.btn_updates.setText("  Updates")
            self.btn_notifications.setText("  Notifications")
            self.sidebar_open = True
        self.apply_button_layout()
        self.refresh_download_indicator()
        self.refresh_nav_state()
        logger.info("Sidebar toggled, open=%s", self.sidebar_open)

    def apply_button_layout(self):
        button_style = sidebar_button_style(self.sidebar_open)
        for button in (
            self.toggle_btn,
            self.btn_library,
            self.btn_discovery,
            self.btn_downloader,
            self.btn_updates,
            self.btn_notifications,
            self.btn_settings,
        ):
            button.setStyleSheet(button_style)

    def set_target(self, target: str):
        self._target = target
        self.refresh_nav_state()

    def _icon_color(self, button_name: str) -> str:
        if button_name == self._target:
            return ACCENT
        return SIDEBAR_ICON_COLOR

    def refresh_nav_state(self):
        button_specs = (
            (self.btn_library, "library", "fa5s.book-open"),
            (self.btn_discovery, "discovery", "fa5s.compass"),
            (self.btn_updates, "updates", "fa5s.sync"),
            (self.btn_notifications, "notifications", "fa5s.bell"),
            (self.btn_settings, "settings", "fa5s.cog"),
        )
        for button, name, icon_name in button_specs:
            active = name == self._target
            button.setProperty("active", active)
            button.style().unpolish(button)
            button.style().polish(button)
            button.setIcon(qta.icon(icon_name, color=self._icon_color(name)))

        if not self._download_active:
            downloader_active = self._target == "downloader"
            self.btn_downloader.setProperty("active", downloader_active)
            self.btn_downloader.style().unpolish(self.btn_downloader)
            self.btn_downloader.style().polish(self.btn_downloader)
            self.btn_downloader.setIcon(
                qta.icon("fa5s.download", color=self._icon_color("downloader"))
            )

    def _job_key(self, prefix: str, name: str) -> str:
        return f"{prefix}:{name}"

    def _on_download_started(self, prefix: str, name: str):
        self._download_jobs[self._job_key(prefix, name)] = {
            "name": name,
            "current": 0,
            "total": 0,
        }
        self.refresh_download_indicator()

    def _on_download_renamed(self, prefix: str, old_name: str, new_name: str):
        old_key = self._job_key(prefix, old_name)
        state = self._download_jobs.pop(old_key, None)
        if state is None:
            state = {"name": new_name, "current": 0, "total": 0}
        state["name"] = new_name
        self._download_jobs[self._job_key(prefix, new_name)] = state
        self.refresh_download_indicator()

    def _on_download_progress(self, prefix: str, name: str, current: int, total: int):
        key = self._job_key(prefix, name)
        state = self._download_jobs.setdefault(
            key,
            {"name": name, "current": 0, "total": 0},
        )
        state["current"] = max(0, int(current))
        state["total"] = max(0, int(total))
        self.refresh_download_indicator()

    def _on_download_finished(self, prefix: str, name: str):
        self._download_jobs.pop(self._job_key(prefix, name), None)
        self.refresh_download_indicator()

    def _download_totals(self) -> tuple[int, int]:
        current = 0
        total = 0
        for state in self._download_jobs.values():
            total += max(0, int(state["total"]))
            current += min(max(0, int(state["current"])), max(0, int(state["total"])))
        return current, total

    def refresh_download_indicator(self):
        active_count = len(self._download_jobs)
        current, total = self._download_totals()

        if active_count > 0:
            remaining = max(0, total - current)
            icon_state = ("progress", "active")
            if icon_state != self._download_icon_state:
                self._download_active = True
                self.btn_downloader.setIcon(
                    qta.icon("fa5s.spinner", color=ACCENT, animation=self._download_spin)
                )
                self._download_icon_state = icon_state
            self.btn_downloader.setProperty("active", self._target == "downloader")
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
                self.btn_downloader.setToolTip(
                    f"{active_count} active download(s): {current} done, {remaining} left"
                )
            else:
                self.btn_downloader.setToolTip(f"{active_count} active download(s)")
            return

        if self._download_icon_state != ("idle", None):
            self._download_icon_state = ("idle", None)
        self._download_active = False
        self.btn_downloader.setToolTip("Open downloader")
        if self.sidebar_open:
            self.btn_downloader.setText("  Download")
        else:
            self.btn_downloader.setText("")
        self.refresh_nav_state()

    def set_notification_unread_count(self, count: int):
        unread = max(0, int(count))
        if unread > 0:
            self._notification_badge.setText("99+" if unread > 99 else str(unread))
            self._notification_badge.adjustSize()
            badge_w = max(16, self._notification_badge.width() + 6)
            self._notification_badge.resize(badge_w, 16)
            self._notification_badge.move(max(12, self.btn_notifications.width() - badge_w - 6), 4)
            self._notification_badge.show()
            self.btn_notifications.setToolTip(f"{unread} unread notification(s)")
        else:
            self._notification_badge.hide()
            self.btn_notifications.setToolTip("Open notifications")

