import html
import os
import re
import time
from pathlib import Path

import qtawesome as qta

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QFont, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QListView,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.app_update import (
    APP_NAME,
    APP_VERSION,
    GITHUB_RELEASES_URL,
    ReleaseAsset,
    UpdateCheckResult,
    can_self_update,
    download_release_asset,
    display_version,
    fetch_latest_release,
    format_check_time,
    is_self_update_supported,
    launch_windows_update_installer,
    load_last_update_error,
)
from core.app_logging import archived_log_paths, current_log_path, get_logger
from core.library_layout import move_library_contents
from scrapers.discovery_registry import get_all_discovery_providers, get_all_discovery_providers_including_disabled
from scrapers.registry import get_all_scrapers_including_disabled
from scrapers.site_availability import (
    MODE_ALL_DISABLED,
    MODE_DISCOVERY_DISABLED,
    MODE_ENABLED,
    get_site_availability_mode,
    is_download_enabled,
    save_site_availability,
    set_site_availability_mode,
)
from scrapers.site_reliability import badge_for_site, record_site_check
from core.site_session import site_base_url
from gui.settings.scraper_config_dialog import ScraperConfigDialog
from stores.scraper_settings_store import (
    load_scraper_default_config,
    reset_scraper_default_config,
    save_scraper_default_config,
)
from stores.settings_store import (
    APP_LOCALE_KEY,
    APP_UPDATE_CHECK_ON_STARTUP_KEY,
    APP_UPDATE_LAST_ASSET_URL_KEY,
    APP_UPDATE_LAST_CHECK_AT_KEY,
    APP_UPDATE_LAST_ERROR_KEY,
    APP_UPDATE_LAST_NOTIFIED_VERSION_KEY,
    APP_UPDATE_LAST_STATUS_KEY,
    APP_UPDATE_LAST_URL_KEY,
    APP_UPDATE_LAST_VERSION_KEY,
    DEFAULT_LIBRARY_PATH,
    DEFAULT_LIBRARY_CONTENT_PATHS,
    LIBRARY_SHOW_DOWNLOADS_SECTION_KEY,
    LIBRARY_SHOW_NEW_SECTION_KEY,
    LIBRARY_SHOW_BOOKMARKED_SECTION_KEY,
    LIBRARY_SHOW_CONTINUE_SECTION_KEY,
    LIBRARY_SHOW_UPDATES_SECTION_KEY,
    LIBRARY_SHOW_COMPLETED_SECTION_KEY,
    LIBRARY_UPDATE_CHECK_ON_STARTUP_KEY,
    LIBRARY_UPDATE_INTERVAL_MINUTES_KEY,
    LIBRARY_UPDATE_INTERVAL_OPTIONS,
    LIBRARY_UPDATE_LAST_CHECK_AT_KEY,
    LIBRARY_UPDATE_LAST_NOTIFIED_SIGNATURE_KEY,
    LIBRARY_UPDATE_LAST_RESULT_KEY,
    LIBRARY_USE_CATEGORIES_KEY,
    VIEWER_AUTO_SKIP_KEY,
    VIEWER_FOCUS_MODE_KEY,
    VIEWER_MINIMAP_VISIBLE_KEY,
    VIEWER_SCENE_ANCHORS_VISIBLE_KEY,
    VIEWER_TEXT_PROGRESS_VISIBLE_KEY,
    VIEWER_TEXT_SIZE_KEY,
    VIEWER_TEXT_PAGE_COLOR_KEY,
    VIEWER_TEXT_COLOR_KEY,
    VIEWER_MANGA_LAYOUT_KEY,
    VIEWER_MANGA_SPREAD_PARITY_KEY,
    VIEWER_MANGA_FIT_MODE_KEY,
    VIEWER_NAV_DIRECTION_KEY,
    VIEWER_ZOOM_KEY,
    load_library_content_paths,
    load_default_discovery_provider,
    load_library_path,
    load_setting,
    save_default_discovery_provider,
    save_library_content_paths,
    save_library_path,
    save_setting,
    save_settings,
)
from gui.settings.library_health_dialog import LibraryHealthDialog
from gui.common.strings import available_locales, get_locale, set_locale, t
from gui.common.styles import (
    APP_UPDATE_PROGRESS_STYLE,
    BUTTON_STYLE,
    CHECKBOX_STYLE,
    INPUT_STYLE,
    LOG_META_STYLE,
    LOG_VIEW_STYLE,
    PAGE_BG_STYLE,
    PAGE_TITLE_LARGE_STYLE,
    PAGE_TITLE_STYLE,
    PILL_LABEL_STYLE,
    SECTION_LABEL_EMPHASIS_STYLE,
    SECTION_LABEL_STYLE,
    SECTION_LABEL_TRANSPARENT_STYLE,
    SLIDER_STYLE,
    STARTUP_UPDATE_DIALOG_STYLE,
    STATUS_LABEL_STYLE,
    SURFACE_PANEL_STYLE,
    reliability_badge_button_style,
    TAB_STYLE,
    TEXT_MUTED_BODY_STYLE,
    TEXT_MUTED_LABEL_STYLE,
    TEXT_MUTED_TRANSPARENT_STYLE,
    TRANSPARENT_BG_STYLE,
    TRANSPARENT_SCROLL_AREA_STYLE,
)


logger = get_logger(__name__)

_LEVEL_RE = re.compile(r"\[(DEBUG|INFO|WARNING|ERROR|CRITICAL)\]")

class _AppUpdateWorker(QThread):
    result_ready = Signal(object)

    def run(self):
        self.result_ready.emit(fetch_latest_release())


class _AppUpdateInstallWorker(QThread):
    progress_changed = Signal(int, int)
    result_ready = Signal(object)

    def __init__(self, asset: ReleaseAsset):
        super().__init__()
        self._asset = asset

    def run(self):
        try:
            path = download_release_asset(self._asset, progress_callback=self._emit_progress)
            self.result_ready.emit((True, str(path), ""))
        except Exception as exc:
            self.result_ready.emit((False, "", str(exc)))

    def _emit_progress(self, current: int, total: int):
        self.progress_changed.emit(int(current), int(total))


class _SiteReliabilityTestWorker(QThread):
    result_ready = Signal(str, bool, str, int)

    def __init__(self, site_name: str):
        super().__init__()
        self._site_name = str(site_name or "").strip()

    def run(self):
        started_at = time.perf_counter()
        ok = False
        error = "No self-test available for this source yet."
        try:
            provider = None
            for current in get_all_discovery_providers_including_disabled():
                if getattr(current, "site_name", "") == self._site_name:
                    provider = current
                    break
            if provider is None:
                raise RuntimeError(error)
            provider.get_catalog_page(page=1)
            ok = True
            error = ""
        except Exception as exc:
            error = str(exc) or error
        duration_ms = int((time.perf_counter() - started_at) * 1000.0)
        self.result_ready.emit(self._site_name, ok, error, duration_ms)


class _LibraryMoveWorker(QThread):
    result_ready = Signal(object)

    def __init__(
        self,
        source_root: str,
        destination_root: str,
        source_content_paths: dict[str, str],
        destination_content_paths: dict[str, str],
    ):
        super().__init__()
        self._source_root = str(source_root or "").strip()
        self._destination_root = str(destination_root or "").strip()
        self._source_content_paths = dict(source_content_paths or {})
        self._destination_content_paths = dict(destination_content_paths or {})

    def run(self):
        try:
            move_library_contents(
                self._source_root,
                self._destination_root,
                source_content_paths=self._source_content_paths,
                destination_content_paths=self._destination_content_paths,
            )
            self.result_ready.emit((True, ""))
        except Exception as exc:
            self.result_ready.emit((False, str(exc) or "Could not move the library contents."))


class _StartupUpdateDialog(QDialog):
    def __init__(
        self,
        release_version: str,
        current_version: str,
        can_install: bool,
        release_notes: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._install_started = False
        self._can_install = bool(can_install)

        self.setWindowTitle(t("settings.startup_update.window"))
        self.setModal(True)
        self.setMinimumWidth(500)
        self.setStyleSheet(STARTUP_UPDATE_DIALOG_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(0)

        panel = QWidget()
        panel.setObjectName("updateDialogPanel")
        panel.setStyleSheet(SURFACE_PANEL_STYLE)
        layout.addWidget(panel)

        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(24, 22, 24, 22)
        panel_layout.setSpacing(14)

        eyebrow = QLabel(t("settings.startup_update.eyebrow"))
        eyebrow.setStyleSheet(SECTION_LABEL_EMPHASIS_STYLE)
        panel_layout.addWidget(eyebrow)

        title = QLabel(t("settings.startup_update.title", version=display_version(release_version)))
        title.setStyleSheet(PAGE_TITLE_LARGE_STYLE)
        title.setWordWrap(True)
        panel_layout.addWidget(title)

        version_row = QHBoxLayout()
        version_row.setContentsMargins(0, 0, 0, 0)
        version_row.setSpacing(8)

        current_pill = QLabel(t("settings.startup_update.current", version=display_version(current_version)))
        current_pill.setStyleSheet(PILL_LABEL_STYLE)
        version_row.addWidget(current_pill)

        latest_pill = QLabel(t("settings.startup_update.latest", version=display_version(release_version)))
        latest_pill.setStyleSheet(PILL_LABEL_STYLE)
        version_row.addWidget(latest_pill)
        version_row.addStretch()
        panel_layout.addLayout(version_row)

        self.message_label = QLabel(
            t("settings.startup_update.message_install")
            if self._can_install
            else t("settings.startup_update.message_view")
        )
        self.message_label.setWordWrap(True)
        self.message_label.setStyleSheet(TEXT_MUTED_BODY_STYLE)
        panel_layout.addWidget(self.message_label)

        patch_notes_label = QLabel(t("settings.startup_update.patch_notes"))
        patch_notes_label.setStyleSheet(SECTION_LABEL_TRANSPARENT_STYLE)
        panel_layout.addWidget(patch_notes_label)

        self.patch_notes_view = QTextEdit()
        self.patch_notes_view.setReadOnly(True)
        self.patch_notes_view.setStyleSheet(LOG_VIEW_STYLE)
        self.patch_notes_view.setMinimumHeight(220)
        self.patch_notes_view.setMarkdown(self._normalize_release_notes(release_notes))
        panel_layout.addWidget(self.patch_notes_view)

        self.progress_label = QLabel("")
        self.progress_label.setWordWrap(True)
        self.progress_label.setStyleSheet(STATUS_LABEL_STYLE)
        self.progress_label.hide()
        panel_layout.addWidget(self.progress_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet(APP_UPDATE_PROGRESS_STYLE)
        self.progress_bar.hide()
        panel_layout.addWidget(self.progress_bar)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch()

        self.close_btn = QPushButton(t("settings.startup_update.close"))
        self.close_btn.setStyleSheet(BUTTON_STYLE)
        self.close_btn.clicked.connect(self.reject)
        actions.addWidget(self.close_btn)

        self.install_btn = QPushButton(t("settings.startup_update.update_app") if self._can_install else t("settings.startup_update.view_releases"))
        self.install_btn.setStyleSheet(BUTTON_STYLE)
        self.install_btn.setDefault(True)
        actions.addWidget(self.install_btn)

        panel_layout.addLayout(actions)

    @staticmethod
    def _normalize_release_notes(release_notes: str) -> str:
        text = str(release_notes or "").strip()
        if not text:
            return t("settings.startup_update.no_notes")
        return text

    def begin_install(self):
        self._install_started = True
        self.install_btn.setEnabled(False)
        self.close_btn.setEnabled(False)
        self.message_label.setText(t("settings.startup_update.downloading"))
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_label.setText(t("settings.startup_update.preparing"))
        self.progress_bar.show()
        self.progress_label.show()

    def set_progress(self, current: int, total: int, format_bytes):
        self.progress_bar.show()
        self.progress_label.show()
        if total > 0:
            percent = int((max(0, current) / max(1, total)) * 100)
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(percent)
            self.progress_label.setText(
                t("settings.startup_update.downloaded_of", current=format_bytes(current), total=format_bytes(total), percent=percent)
            )
            self.install_btn.setText(t("settings.startup_update.downloading_percent", percent=percent))
            return

        self.progress_bar.setRange(0, 0)
        self.progress_label.setText(t("settings.startup_update.downloaded", current=format_bytes(current)))
        self.install_btn.setText(t("settings.startup_update.downloading_simple"))

    def install_failed(self, error: str):
        self._install_started = False
        self.message_label.setText(t("settings.startup_update.failed", error=error))
        self.progress_bar.hide()
        self.progress_label.hide()
        self.install_btn.setEnabled(True)
        self.install_btn.setText("Update App" if self._can_install else "View Releases")
        self.close_btn.setEnabled(True)

    def install_launching(self):
        self.progress_bar.show()
        self.progress_label.show()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.progress_label.setText(t("settings.startup_update.complete"))
        self.message_label.setText(t("settings.startup_update.installing_message"))
        self.install_btn.setText(t("settings.startup_update.installing"))

    def reject(self):
        if self._install_started:
            return
        super().reject()


class SettingsPage(QWidget):

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self._last_log_stamp = None
        self._last_log_path = None
        self._last_log_size = 0
        self._logs_loaded = False
        self._source_mode_boxes = {}
        self._source_reliability_widgets = {}
        self._default_discovery_provider_checkboxes = {}
        self._reliability_test_workers = {}
        self._pending_reliability_popup_site = ""
        self._pending_source_authorization = None
        self._library_move_worker = None
        self._pending_library_layout_save = None
        self._update_worker = None
        self._update_install_worker = None
        self._latest_update_result = None
        self._latest_release_url = GITHUB_RELEASES_URL
        self._latest_asset_url = ""
        self._pending_update_check_mode = "manual"
        self._startup_update_dialog = None
        self._open_refresh_timer = QTimer(self)
        self._open_refresh_timer.setSingleShot(True)
        self._open_refresh_timer.timeout.connect(self._run_open_refresh)

        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(PAGE_BG_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(18)
        layout.setAlignment(Qt.AlignTop)

        title = QLabel(t("settings.page.title"))
        title.setStyleSheet(PAGE_TITLE_STYLE)
        layout.addWidget(title)

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(TAB_STYLE)
        self.tabs.addTab(self._build_general_tab(), t("settings.tab.general"))
        self.tabs.addTab(self._build_reader_tab(), t("settings.tab.reader"))
        self.tabs.addTab(self._build_scrapers_tab(), t("settings.tab.scrapers"))
        self.tabs.addTab(self._build_logs_tab(), t("settings.tab.logs"))
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs, 1)
        self._log_refresh_timer = QTimer(self)
        self._log_refresh_timer.timeout.connect(self._refresh_logs_if_changed)
        self._log_refresh_timer.start(1500)
        self._load_saved_update_state()
        self.refresh_library_update_status()

    def schedule_open_refresh(self):
        self._open_refresh_timer.start(0)

    def _run_open_refresh(self):
        self._refresh_default_discovery_provider_checkboxes()
        self.refresh_scraper_reliability()

    def open_logs_tab(self):
        self.tabs.setCurrentWidget(self.logs_tab)
        if not self._logs_loaded:
            QTimer.singleShot(0, lambda: self._refresh_logs(force=True))
        else:
            self._refresh_logs(force=False)

    def _build_general_tab(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(TRANSPARENT_BG_STYLE)

        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignTop)

        locale_card, locale_layout = self._build_card()
        locale_header = QHBoxLayout()
        locale_header.setContentsMargins(0, 0, 0, 0)
        locale_header.setSpacing(10)

        locale_label = QLabel(t("settings.general.language"))
        locale_label.setStyleSheet(SECTION_LABEL_TRANSPARENT_STYLE)
        locale_header.addWidget(locale_label)
        locale_header.addStretch()
        locale_layout.addLayout(locale_header)

        locale_help = QLabel(t("settings.general.language_help"))
        locale_help.setWordWrap(True)
        locale_help.setStyleSheet(TEXT_MUTED_TRANSPARENT_STYLE)
        locale_layout.addWidget(locale_help)

        locale_row = QHBoxLayout()
        locale_row.setSpacing(10)

        locale_value_label = QLabel(t("settings.general.language"))
        locale_value_label.setStyleSheet(TEXT_MUTED_TRANSPARENT_STYLE)
        locale_value_label.setFixedWidth(100)

        self.locale_combo = QComboBox()
        self.locale_combo.setStyleSheet(INPUT_STYLE)
        self.locale_combo.setFont(QFont("Segoe UI", 10))
        locale_view = QListView(self.locale_combo)
        locale_view.setFont(QFont("Segoe UI", 10))
        self.locale_combo.setView(locale_view)
        self._populate_locale_combo()
        self.locale_combo.currentIndexChanged.connect(self._on_app_locale_changed)

        locale_row.addWidget(locale_value_label)
        locale_row.addWidget(self.locale_combo, 1)
        locale_layout.addLayout(locale_row)

        layout.addWidget(locale_card)

        library_card, library_layout = self._build_card()
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(10)

        folder_label = QLabel(t("settings.general.library"))
        folder_label.setStyleSheet(SECTION_LABEL_TRANSPARENT_STYLE)
        header_row.addWidget(folder_label)
        header_row.addStretch()
        library_layout.addLayout(header_row)

        content_folders_label = QLabel(t("settings.general.content_paths"))
        content_folders_label.setStyleSheet(SECTION_LABEL_EMPHASIS_STYLE)
        library_layout.addWidget(content_folders_label)

        content_folders_help = QLabel(
            t("settings.general.content_paths_help")
        )
        content_folders_help.setWordWrap(True)
        content_folders_help.setStyleSheet(TEXT_MUTED_TRANSPARENT_STYLE)
        library_layout.addWidget(content_folders_help)

        self.webtoon_folder_input = self._build_library_path_input("webtoon")
        library_layout.addLayout(self._build_library_path_row("Webtoons", self.webtoon_folder_input, "webtoon"))

        self.manga_folder_input = self._build_library_path_input("manga")
        library_layout.addLayout(self._build_library_path_row("Manga", self.manga_folder_input, "manga"))

        self.webnovel_folder_input = self._build_library_path_input("webnovel")
        library_layout.addLayout(self._build_library_path_row("Webnovels", self.webnovel_folder_input, "webnovel"))

        apply_folders_btn = QPushButton(t("settings.general.apply_content_paths"))
        apply_folders_btn.setStyleSheet(BUTTON_STYLE)
        apply_folders_btn.setMinimumHeight(34)
        apply_folders_btn.clicked.connect(self._apply_library_content_paths)
        library_layout.addWidget(apply_folders_btn)

        self._load_library_content_path_inputs()

        self.use_categories_checkbox = QCheckBox(t("settings.general.enable_categories"))
        self.use_categories_checkbox.setChecked(load_setting(LIBRARY_USE_CATEGORIES_KEY, True))
        self.use_categories_checkbox.setStyleSheet(CHECKBOX_STYLE)
        self.use_categories_checkbox.toggled.connect(self._on_use_categories_changed)
        library_layout.addWidget(self.use_categories_checkbox)

        self.show_new_section_checkbox = QCheckBox(t("settings.general.show_new"))
        self.show_new_section_checkbox.setChecked(load_setting(LIBRARY_SHOW_NEW_SECTION_KEY, True))
        self.show_new_section_checkbox.setStyleSheet(CHECKBOX_STYLE)
        self.show_new_section_checkbox.toggled.connect(self._on_show_new_section_changed)
        library_layout.addWidget(self.show_new_section_checkbox)

        self.show_downloads_section_checkbox = QCheckBox(t("settings.general.show_downloads"))
        self.show_downloads_section_checkbox.setChecked(load_setting(LIBRARY_SHOW_DOWNLOADS_SECTION_KEY, True))
        self.show_downloads_section_checkbox.setStyleSheet(CHECKBOX_STYLE)
        self.show_downloads_section_checkbox.toggled.connect(self._on_show_downloads_section_changed)
        library_layout.addWidget(self.show_downloads_section_checkbox)

        self.show_bookmarked_section_checkbox = QCheckBox(t("settings.general.show_bookmarked"))
        self.show_bookmarked_section_checkbox.setChecked(load_setting(LIBRARY_SHOW_BOOKMARKED_SECTION_KEY, True))
        self.show_bookmarked_section_checkbox.setStyleSheet(CHECKBOX_STYLE)
        self.show_bookmarked_section_checkbox.toggled.connect(self._on_show_bookmarked_section_changed)
        library_layout.addWidget(self.show_bookmarked_section_checkbox)

        self.show_continue_section_checkbox = QCheckBox(t("settings.general.show_continue"))
        self.show_continue_section_checkbox.setChecked(load_setting(LIBRARY_SHOW_CONTINUE_SECTION_KEY, True))
        self.show_continue_section_checkbox.setStyleSheet(CHECKBOX_STYLE)
        self.show_continue_section_checkbox.toggled.connect(self._on_show_continue_section_changed)
        library_layout.addWidget(self.show_continue_section_checkbox)

        self.show_updates_smart_section_checkbox = QCheckBox(t("settings.general.show_updates"))
        self.show_updates_smart_section_checkbox.setChecked(load_setting(LIBRARY_SHOW_UPDATES_SECTION_KEY, True))
        self.show_updates_smart_section_checkbox.setStyleSheet(CHECKBOX_STYLE)
        self.show_updates_smart_section_checkbox.toggled.connect(self._on_show_updates_smart_section_changed)
        library_layout.addWidget(self.show_updates_smart_section_checkbox)

        self.show_completed_section_checkbox = QCheckBox(t("settings.general.show_completed"))
        self.show_completed_section_checkbox.setChecked(load_setting(LIBRARY_SHOW_COMPLETED_SECTION_KEY, True))
        self.show_completed_section_checkbox.setStyleSheet(CHECKBOX_STYLE)
        self.show_completed_section_checkbox.toggled.connect(self._on_show_completed_section_changed)
        library_layout.addWidget(self.show_completed_section_checkbox)

        layout.addWidget(library_card)

        updates_card, updates_layout = self._build_card()
        updates_header = QHBoxLayout()
        updates_header.setContentsMargins(0, 0, 0, 0)
        updates_header.setSpacing(10)

        updates_label = QLabel(t("settings.general.app_updates"))
        updates_label.setStyleSheet(SECTION_LABEL_TRANSPARENT_STYLE)
        updates_header.addWidget(updates_label)
        updates_header.addStretch()
        updates_layout.addLayout(updates_header)

        current_version_label = QLabel(t("settings.general.current_version", version=display_version(APP_VERSION)))
        current_version_label.setStyleSheet(TEXT_MUTED_TRANSPARENT_STYLE)
        updates_layout.addWidget(current_version_label)

        self.update_status_label = QLabel(t("settings.general.latest_not_checked"))
        self.update_status_label.setWordWrap(True)
        self.update_status_label.setStyleSheet(TEXT_MUTED_TRANSPARENT_STYLE)
        updates_layout.addWidget(self.update_status_label)

        self.update_meta_label = QLabel(t("settings.general.last_checked_never"))
        self.update_meta_label.setWordWrap(True)
        self.update_meta_label.setStyleSheet(STATUS_LABEL_STYLE)
        updates_layout.addWidget(self.update_meta_label)

        self.update_diagnostic_label = QLabel("")
        self.update_diagnostic_label.setWordWrap(True)
        self.update_diagnostic_label.setStyleSheet(STATUS_LABEL_STYLE)
        self.update_diagnostic_label.hide()
        updates_layout.addWidget(self.update_diagnostic_label)

        self.update_progress_label = QLabel("")
        self.update_progress_label.setWordWrap(True)
        self.update_progress_label.setStyleSheet(STATUS_LABEL_STYLE)
        self.update_progress_label.hide()
        updates_layout.addWidget(self.update_progress_label)

        self.update_progress_bar = QProgressBar()
        self.update_progress_bar.setRange(0, 100)
        self.update_progress_bar.setValue(0)
        self.update_progress_bar.setTextVisible(False)
        self.update_progress_bar.setStyleSheet(APP_UPDATE_PROGRESS_STYLE)
        self.update_progress_bar.hide()
        updates_layout.addWidget(self.update_progress_bar)

        update_actions_row = QHBoxLayout()
        update_actions_row.setSpacing(8)

        self.check_updates_btn = QPushButton(t("settings.general.check_updates"))
        self.check_updates_btn.setStyleSheet(BUTTON_STYLE)
        self.check_updates_btn.setMinimumWidth(140)
        self.check_updates_btn.setMinimumHeight(34)
        self.check_updates_btn.clicked.connect(self._check_for_app_updates)
        update_actions_row.addWidget(self.check_updates_btn)

        self.download_update_btn = QPushButton(t("settings.general.update_app"))
        self.download_update_btn.setStyleSheet(BUTTON_STYLE)
        self.download_update_btn.setMinimumWidth(140)
        self.download_update_btn.setMinimumHeight(34)
        self.download_update_btn.clicked.connect(self._open_latest_release_download)
        self.download_update_btn.setEnabled(False)
        update_actions_row.addWidget(self.download_update_btn)

        self.view_releases_btn = QPushButton(t("settings.general.view_releases"))
        self.view_releases_btn.setStyleSheet(BUTTON_STYLE)
        self.view_releases_btn.setMinimumWidth(120)
        self.view_releases_btn.setMinimumHeight(34)
        self.view_releases_btn.clicked.connect(self._open_releases_page)
        update_actions_row.addWidget(self.view_releases_btn)

        update_actions_row.addStretch()
        updates_layout.addLayout(update_actions_row)

        self.check_updates_on_startup_checkbox = QCheckBox(t("settings.general.check_startup"))
        self.check_updates_on_startup_checkbox.setChecked(load_setting(APP_UPDATE_CHECK_ON_STARTUP_KEY, True))
        self.check_updates_on_startup_checkbox.setStyleSheet(CHECKBOX_STYLE)
        self.check_updates_on_startup_checkbox.toggled.connect(self._on_check_updates_on_startup_changed)
        updates_layout.addWidget(self.check_updates_on_startup_checkbox)

        layout.addWidget(updates_card)

        library_updates_card, library_updates_layout = self._build_card()
        library_updates_header = QHBoxLayout()
        library_updates_header.setContentsMargins(0, 0, 0, 0)
        library_updates_header.setSpacing(10)

        library_updates_label = QLabel(t("settings.general.library_update_checks"))
        library_updates_label.setStyleSheet(SECTION_LABEL_TRANSPARENT_STYLE)
        library_updates_header.addWidget(library_updates_label)
        library_updates_header.addStretch()
        library_updates_layout.addLayout(library_updates_header)

        library_updates_help = QLabel(
            t("settings.general.library_update_help")
        )
        library_updates_help.setWordWrap(True)
        library_updates_help.setStyleSheet(TEXT_MUTED_TRANSPARENT_STYLE)
        library_updates_layout.addWidget(library_updates_help)

        self.library_update_check_on_startup_checkbox = QCheckBox(t("settings.general.check_saved_startup"))
        self.library_update_check_on_startup_checkbox.setChecked(
            load_setting(LIBRARY_UPDATE_CHECK_ON_STARTUP_KEY, False)
        )
        self.library_update_check_on_startup_checkbox.setStyleSheet(CHECKBOX_STYLE)
        self.library_update_check_on_startup_checkbox.toggled.connect(
            self._on_library_update_check_on_startup_changed
        )
        library_updates_layout.addWidget(self.library_update_check_on_startup_checkbox)

        library_update_interval_row = QHBoxLayout()
        library_update_interval_row.setSpacing(10)

        library_update_interval_label = QLabel(t("settings.general.background_interval"))
        library_update_interval_label.setStyleSheet(TEXT_MUTED_TRANSPARENT_STYLE)
        library_update_interval_label.setFixedWidth(140)

        self.library_update_interval_combo = QComboBox()
        self.library_update_interval_combo.setStyleSheet(INPUT_STYLE)
        self.library_update_interval_combo.setFont(QFont("Segoe UI", 10))
        interval_view = QListView(self.library_update_interval_combo)
        interval_view.setFont(QFont("Segoe UI", 10))
        self.library_update_interval_combo.setView(interval_view)
        for minutes, label in LIBRARY_UPDATE_INTERVAL_OPTIONS:
            self.library_update_interval_combo.addItem(label, minutes)
        self._set_library_update_interval_selection(
            int(load_setting(LIBRARY_UPDATE_INTERVAL_MINUTES_KEY, 60))
        )
        self.library_update_interval_combo.currentIndexChanged.connect(
            self._on_library_update_interval_changed
        )

        library_update_interval_row.addWidget(library_update_interval_label)
        library_update_interval_row.addWidget(self.library_update_interval_combo, 1)
        library_updates_layout.addLayout(library_update_interval_row)

        self.library_update_status_label = QLabel(t("settings.general.latest_result_not_checked"))
        self.library_update_status_label.setWordWrap(True)
        self.library_update_status_label.setStyleSheet(TEXT_MUTED_TRANSPARENT_STYLE)
        library_updates_layout.addWidget(self.library_update_status_label)

        self.library_update_meta_label = QLabel(t("settings.general.last_checked_never"))
        self.library_update_meta_label.setWordWrap(True)
        self.library_update_meta_label.setStyleSheet(STATUS_LABEL_STYLE)
        library_updates_layout.addWidget(self.library_update_meta_label)

        self.library_health_btn = QPushButton(t("settings.general.library_health_tools"))
        self.library_health_btn.setStyleSheet(BUTTON_STYLE)
        self.library_health_btn.setMinimumWidth(180)
        self.library_health_btn.setMinimumHeight(34)
        self.library_health_btn.clicked.connect(self._open_library_health_report)
        library_updates_layout.addWidget(self.library_health_btn)

        library_update_actions = QHBoxLayout()
        library_update_actions.setSpacing(8)

        self.check_library_updates_btn = QPushButton(t("settings.general.check_saved_now"))
        self.check_library_updates_btn.setStyleSheet(BUTTON_STYLE)
        self.check_library_updates_btn.setMinimumWidth(180)
        self.check_library_updates_btn.setMinimumHeight(34)
        self.check_library_updates_btn.clicked.connect(self._check_library_updates_now)
        library_update_actions.addWidget(self.check_library_updates_btn)
        library_update_actions.addStretch()
        library_updates_layout.addLayout(library_update_actions)

        layout.addWidget(library_updates_card)

        actions_row = QHBoxLayout()
        actions_row.setContentsMargins(0, 2, 0, 0)
        actions_row.setSpacing(12)

        reset_btn = QPushButton(t("settings.general.reset_defaults"))
        reset_btn.setStyleSheet(BUTTON_STYLE)
        reset_btn.setFixedWidth(148)
        reset_btn.clicked.connect(self._reset)
        actions_row.addWidget(reset_btn)
        actions_row.addStretch()
        layout.addLayout(actions_row)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(STATUS_LABEL_STYLE)
        layout.addWidget(self.status_label)
        layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(TRANSPARENT_SCROLL_AREA_STYLE)
        scroll.setWidget(page)
        return scroll

    def _build_reader_tab(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(TRANSPARENT_BG_STYLE)

        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignTop)

        reader_card, reader_layout = self._build_card()
        reader_header = QHBoxLayout()
        reader_header.setContentsMargins(0, 0, 0, 0)
        reader_header.setSpacing(10)

        reader_label = QLabel(t("settings.reader.title"))
        reader_label.setStyleSheet(SECTION_LABEL_TRANSPARENT_STYLE)
        reader_header.addWidget(reader_label)
        reader_header.addStretch()
        reader_layout.addLayout(reader_header)

        reader_help = QLabel(t("settings.reader.help"))
        reader_help.setWordWrap(True)
        reader_help.setStyleSheet(TEXT_MUTED_TRANSPARENT_STYLE)
        reader_layout.addWidget(reader_help)

        self.auto_skip_checkbox = QCheckBox(t("settings.reader.auto_skip"))
        self.auto_skip_checkbox.setChecked(load_setting(VIEWER_AUTO_SKIP_KEY, True))
        self.auto_skip_checkbox.setStyleSheet(CHECKBOX_STYLE)
        self.auto_skip_checkbox.toggled.connect(self._on_auto_skip_changed)
        reader_layout.addWidget(self.auto_skip_checkbox)

        self.focus_mode_checkbox = QCheckBox(t("settings.reader.focus_mode"))
        self.focus_mode_checkbox.setChecked(load_setting(VIEWER_FOCUS_MODE_KEY, False))
        self.focus_mode_checkbox.setStyleSheet(CHECKBOX_STYLE)
        self.focus_mode_checkbox.toggled.connect(self._on_focus_mode_changed)
        reader_layout.addWidget(self.focus_mode_checkbox)

        self.minimap_checkbox = QCheckBox(t("settings.reader.minimap"))
        self.minimap_checkbox.setChecked(load_setting(VIEWER_MINIMAP_VISIBLE_KEY, True))
        self.minimap_checkbox.setStyleSheet(CHECKBOX_STYLE)
        self.minimap_checkbox.toggled.connect(self._on_minimap_changed)
        reader_layout.addWidget(self.minimap_checkbox)

        self.scene_anchors_checkbox = QCheckBox(t("settings.reader.scene_anchors"))
        self.scene_anchors_checkbox.setChecked(load_setting(VIEWER_SCENE_ANCHORS_VISIBLE_KEY, True))
        self.scene_anchors_checkbox.setStyleSheet(CHECKBOX_STYLE)
        self.scene_anchors_checkbox.toggled.connect(self._on_scene_anchors_changed)
        reader_layout.addWidget(self.scene_anchors_checkbox)

        zoom_row = QHBoxLayout()
        zoom_row.setSpacing(10)

        zoom_text = QLabel(t("settings.reader.default_zoom"))
        zoom_text.setStyleSheet(TEXT_MUTED_TRANSPARENT_STYLE)
        zoom_text.setFixedWidth(100)

        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setMinimum(15)
        self.zoom_slider.setMaximum(100)
        self.zoom_slider.setValue(int(load_setting(VIEWER_ZOOM_KEY, 0.5) * 100))
        self.zoom_slider.setStyleSheet(SLIDER_STYLE)
        self.zoom_slider.valueChanged.connect(self._on_zoom_changed)

        self.zoom_value_label = QLabel(f"{self.zoom_slider.value()}%")
        self.zoom_value_label.setStyleSheet(PILL_LABEL_STYLE)
        self.zoom_value_label.setAlignment(Qt.AlignCenter)
        self.zoom_value_label.setFixedWidth(54)

        zoom_row.addWidget(zoom_text)
        zoom_row.addWidget(self.zoom_slider)
        zoom_row.addWidget(self.zoom_value_label)
        reader_layout.addLayout(zoom_row)

        manga_defaults_label = QLabel(t("settings.reader.manga"))
        manga_defaults_label.setStyleSheet(SECTION_LABEL_TRANSPARENT_STYLE)
        reader_layout.addWidget(manga_defaults_label)

        manga_layout_row = QHBoxLayout()
        manga_layout_row.setSpacing(10)
        manga_layout_text = QLabel(t("settings.reader.page_layout"))
        manga_layout_text.setStyleSheet(TEXT_MUTED_TRANSPARENT_STYLE)
        manga_layout_text.setFixedWidth(100)
        self.manga_layout_combo = QComboBox()
        self.manga_layout_combo.setStyleSheet(INPUT_STYLE)
        self.manga_layout_combo.addItem(t("settings.reader.layout.single"), ("single", "odd"))
        self.manga_layout_combo.addItem(t("settings.reader.layout.double_odds"), ("double", "odd"))
        self.manga_layout_combo.addItem(t("settings.reader.layout.double_evens"), ("double", "even"))
        current_layout = (
            str(load_setting(VIEWER_MANGA_LAYOUT_KEY, "single") or "single"),
            str(load_setting(VIEWER_MANGA_SPREAD_PARITY_KEY, "odd") or "odd"),
        )
        self.manga_layout_combo.setCurrentIndex(max(0, self.manga_layout_combo.findData(current_layout)))
        self.manga_layout_combo.currentIndexChanged.connect(self._on_manga_layout_defaults_changed)
        manga_layout_row.addWidget(manga_layout_text)
        manga_layout_row.addWidget(self.manga_layout_combo, 1)
        reader_layout.addLayout(manga_layout_row)

        manga_fit_row = QHBoxLayout()
        manga_fit_row.setSpacing(10)
        manga_fit_text = QLabel(t("settings.reader.scale"))
        manga_fit_text.setStyleSheet(TEXT_MUTED_TRANSPARENT_STYLE)
        manga_fit_text.setFixedWidth(100)
        self.manga_fit_combo = QComboBox()
        self.manga_fit_combo.setStyleSheet(INPUT_STYLE)
        self.manga_fit_combo.addItem(t("settings.reader.fit_width"), "width")
        self.manga_fit_combo.addItem(t("settings.reader.fit_height"), "height")
        self.manga_fit_combo.setCurrentIndex(max(0, self.manga_fit_combo.findData(str(load_setting(VIEWER_MANGA_FIT_MODE_KEY, "width") or "width"))))
        self.manga_fit_combo.currentIndexChanged.connect(self._on_manga_layout_defaults_changed)
        manga_fit_row.addWidget(manga_fit_text)
        manga_fit_row.addWidget(self.manga_fit_combo, 1)
        reader_layout.addLayout(manga_fit_row)

        manga_nav_row = QHBoxLayout()
        manga_nav_row.setSpacing(10)
        manga_nav_text = QLabel(t("settings.reader.navigation"))
        manga_nav_text.setStyleSheet(TEXT_MUTED_TRANSPARENT_STYLE)
        manga_nav_text.setFixedWidth(100)
        self.manga_nav_combo = QComboBox()
        self.manga_nav_combo.setStyleSheet(INPUT_STYLE)
        self.manga_nav_combo.addItem(t("settings.reader.nav_ltr"), "ltr")
        self.manga_nav_combo.addItem(t("settings.reader.nav_rtl"), "rtl")
        self.manga_nav_combo.setCurrentIndex(max(0, self.manga_nav_combo.findData(str(load_setting(VIEWER_NAV_DIRECTION_KEY, "ltr") or "ltr"))))
        self.manga_nav_combo.currentIndexChanged.connect(self._on_manga_navigation_defaults_changed)
        manga_nav_row.addWidget(manga_nav_text)
        manga_nav_row.addWidget(self.manga_nav_combo, 1)
        reader_layout.addLayout(manga_nav_row)

        novel_defaults_label = QLabel(t("settings.reader.novels"))
        novel_defaults_label.setStyleSheet(SECTION_LABEL_TRANSPARENT_STYLE)
        reader_layout.addWidget(novel_defaults_label)

        self.text_progress_checkbox = QCheckBox(t("settings.reader.text_progress"))
        self.text_progress_checkbox.setChecked(load_setting(VIEWER_TEXT_PROGRESS_VISIBLE_KEY, True))
        self.text_progress_checkbox.setStyleSheet(CHECKBOX_STYLE)
        self.text_progress_checkbox.toggled.connect(self._on_text_progress_changed)
        reader_layout.addWidget(self.text_progress_checkbox)

        text_size_row = QHBoxLayout()
        text_size_row.setSpacing(10)
        text_size_text = QLabel(t("settings.reader.text_size"))
        text_size_text.setStyleSheet(TEXT_MUTED_TRANSPARENT_STYLE)
        text_size_text.setFixedWidth(100)
        self.text_size_spin = QSpinBox()
        self.text_size_spin.setRange(12, 32)
        self.text_size_spin.setValue(int(load_setting(VIEWER_TEXT_SIZE_KEY, 18) or 18))
        self.text_size_spin.setStyleSheet(INPUT_STYLE)
        self.text_size_spin.valueChanged.connect(self._on_text_style_defaults_changed)
        text_size_row.addWidget(text_size_text)
        text_size_row.addWidget(self.text_size_spin)
        text_size_row.addStretch()
        reader_layout.addLayout(text_size_row)

        self.page_color_button = QPushButton()
        self.page_color_button.setStyleSheet(BUTTON_STYLE)
        self.page_color_button.clicked.connect(lambda: self._pick_reader_color("page"))
        reader_layout.addLayout(self._build_color_setting_row(t("settings.reader.page_color"), self.page_color_button, lambda: self._reset_reader_color("page")))

        self.text_color_button = QPushButton()
        self.text_color_button.setStyleSheet(BUTTON_STYLE)
        self.text_color_button.clicked.connect(lambda: self._pick_reader_color("text"))
        reader_layout.addLayout(self._build_color_setting_row(t("settings.reader.text_color"), self.text_color_button, lambda: self._reset_reader_color("text")))

        self._refresh_reader_color_buttons()

        layout.addWidget(reader_card)

        actions_row = QHBoxLayout()
        actions_row.setContentsMargins(0, 2, 0, 0)
        actions_row.setSpacing(12)

        reset_btn = QPushButton(t("settings.general.reset_defaults"))
        reset_btn.setStyleSheet(BUTTON_STYLE)
        reset_btn.setFixedWidth(148)
        reset_btn.clicked.connect(self._reset)
        actions_row.addWidget(reset_btn)
        actions_row.addStretch()
        layout.addLayout(actions_row)

        self.reader_status_label = QLabel("")
        self.reader_status_label.setStyleSheet(STATUS_LABEL_STYLE)
        layout.addWidget(self.reader_status_label)
        layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(TRANSPARENT_SCROLL_AREA_STYLE)
        scroll.setWidget(page)
        return scroll

    def _populate_locale_combo(self):
        current_locale = str(load_setting(APP_LOCALE_KEY, get_locale()) or get_locale()).strip() or "en"
        locales = list(available_locales())
        preferred = ["en", "pt-BR"]
        ordered = [locale for locale in preferred if locale in locales]
        ordered.extend(locale for locale in locales if locale not in ordered)

        self.locale_combo.blockSignals(True)
        self.locale_combo.clear()
        for locale in ordered:
            self.locale_combo.addItem(self._locale_display_name(locale), locale)
        index = self.locale_combo.findData(current_locale)
        if index < 0:
            index = self.locale_combo.findData("en")
        self.locale_combo.setCurrentIndex(max(0, index))
        self.locale_combo.blockSignals(False)

    def _locale_display_name(self, locale: str) -> str:
        normalized = str(locale or "").strip()
        if normalized == "pt-BR":
            return t("settings.locale.pt_br")
        if normalized == "en":
            return t("settings.locale.english")
        return normalized

    def _on_app_locale_changed(self, _index: int):
        if not hasattr(self, "locale_combo"):
            return
        locale = str(self.locale_combo.currentData() or "en").strip() or "en"
        current_saved = str(load_setting(APP_LOCALE_KEY, "en") or "en").strip() or "en"
        if locale == current_saved:
            return
        save_setting(APP_LOCALE_KEY, locale)
        set_locale(locale)
        self.status_label.setText(t("settings.general.language_restart"))

    def _build_color_setting_row(self, label_text: str, button: QPushButton, reset_callback) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        label = QLabel(label_text)
        label.setStyleSheet(TEXT_MUTED_TRANSPARENT_STYLE)
        label.setFixedWidth(100)
        row.addWidget(label)
        row.addWidget(button)
        reset_btn = QPushButton(t("settings.reader.reset"))
        reset_btn.setStyleSheet(BUTTON_STYLE)
        reset_btn.setFixedWidth(64)
        reset_btn.clicked.connect(reset_callback)
        row.addWidget(reset_btn)
        row.addStretch()
        return row

    def _refresh_reader_color_buttons(self):
        self._set_reader_color_button(self.page_color_button, str(load_setting(VIEWER_TEXT_PAGE_COLOR_KEY, "#140e0c") or "#140e0c"))
        self._set_reader_color_button(self.text_color_button, str(load_setting(VIEWER_TEXT_COLOR_KEY, "#f6ece5") or "#f6ece5"))

    def _set_reader_color_button(self, button: QPushButton, color_value: str):
        color = QColor(str(color_value or "#000000"))
        if not color.isValid():
            color = QColor("#000000")
        luminance = (0.299 * color.red()) + (0.587 * color.green()) + (0.114 * color.blue())
        text_color = "#111111" if luminance > 160 else "#ffffff"
        button.setText(color.name().upper())
        button.setStyleSheet(
            f"background:{color.name()}; color:{text_color}; border:1px solid rgba(255,255,255,0.10); border-radius:6px; padding:6px 12px;"
        )

    def _pick_reader_color(self, target: str):
        key = VIEWER_TEXT_PAGE_COLOR_KEY if target == "page" else VIEWER_TEXT_COLOR_KEY
        title = t("settings.reader.choose_page_color") if target == "page" else t("settings.reader.choose_text_color")
        current = QColor(str(load_setting(key, "#140e0c" if target == "page" else "#f6ece5") or ""))
        color = QColorDialog.getColor(current, self, title)
        if not color.isValid():
            return
        save_setting(key, color.name())
        logger.info("Reader %s color changed: %s", target, color.name())
        self._refresh_reader_color_buttons()
        self._apply_text_defaults_to_active_viewer()
        self._set_settings_status(t("settings.reader.saved"))

    def _reset_reader_color(self, target: str):
        key = VIEWER_TEXT_PAGE_COLOR_KEY if target == "page" else VIEWER_TEXT_COLOR_KEY
        default = "#140e0c" if target == "page" else "#f6ece5"
        save_setting(key, default)
        logger.info("Reader %s color reset: %s", target, default)
        self._refresh_reader_color_buttons()
        self._apply_text_defaults_to_active_viewer()
        self._set_settings_status(t("settings.reader.saved"))

    def _apply_text_defaults_to_active_viewer(self):
        viewer = getattr(self.main_window, "viewer", None)
        if viewer is None or self._viewer_uses_saved_text_overrides(viewer):
            return
        viewer._text_font_size = int(load_setting(VIEWER_TEXT_SIZE_KEY, 18) or 18)
        viewer._text_page_color = str(load_setting(VIEWER_TEXT_PAGE_COLOR_KEY, "#140e0c") or "#140e0c")
        viewer._text_color = str(load_setting(VIEWER_TEXT_COLOR_KEY, "#f6ece5") or "#f6ece5")
        viewer._apply_text_reader_style()
        if hasattr(viewer, "_sync_text_content_height"):
            viewer._sync_text_content_height()

    def _viewer_uses_saved_text_overrides(self, viewer) -> bool:
        if viewer is None or not getattr(viewer, "webtoon", None):
            return False
        webtoon_name = getattr(viewer.webtoon, "name", "")
        if not webtoon_name:
            return False
        return any((
            viewer.settings_store.get_text_font_size(webtoon_name) is not None,
            bool(str(viewer.settings_store.get_text_page_color(webtoon_name) or "").strip()),
            bool(str(viewer.settings_store.get_text_color(webtoon_name) or "").strip()),
        ))

    def _viewer_uses_saved_manga_overrides(self, viewer) -> bool:
        if viewer is None or not getattr(viewer, "webtoon", None):
            return False
        webtoon_name = getattr(viewer.webtoon, "name", "")
        if not webtoon_name:
            return False
        has_view = bool(str(viewer.settings_store.get_manga_view_mode(webtoon_name) or "").strip())
        has_fit = bool(str(viewer.settings_store.get_manga_fit_mode(webtoon_name) or "").strip())
        return has_view or has_fit

    def _apply_manga_defaults_to_active_viewer(self):
        viewer = getattr(self.main_window, "viewer", None)
        if viewer is None:
            return
        uses_saved_manga_overrides = self._viewer_uses_saved_manga_overrides(viewer)
        layout_mode, spread_parity = self.manga_layout_combo.currentData() or ("single", "odd")
        if not uses_saved_manga_overrides:
            viewer._manga_layout_mode = viewer._normalize_manga_layout(layout_mode)
            viewer._manga_spread_parity = viewer._normalize_manga_spread_parity(spread_parity)
            viewer._manga_fit_mode = viewer._normalize_manga_fit_mode(self.manga_fit_combo.currentData() or "width")
        viewer._nav_direction = viewer._normalize_nav_direction(self.manga_nav_combo.currentData() or "ltr")
        if hasattr(viewer, "_sync_horizontal_navigation_ui"):
            viewer._sync_horizontal_navigation_ui()
        if getattr(viewer, "webtoon", None) and getattr(viewer, "image_labels", None) and viewer._is_manga_image_mode():
            if not uses_saved_manga_overrides:
                viewer.rescale_images(previous_zoom=viewer._zoom)
            viewer._sync_manga_page_visibility()
        viewer._apply_reader_session_state()

    def _build_scrapers_tab(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(TRANSPARENT_BG_STYLE)

        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignTop)

        sources_card, sources_layout = self._build_card()
        sources_header = QHBoxLayout()
        sources_header.setContentsMargins(0, 0, 0, 0)
        sources_header.setSpacing(10)

        sources_label = QLabel(t("settings.scrapers.title"))
        sources_label.setStyleSheet(SECTION_LABEL_TRANSPARENT_STYLE)
        sources_header.addWidget(sources_label)
        sources_header.addStretch()
        sources_layout.addLayout(sources_header)

        sources_help = QLabel(t("settings.scrapers.help"))
        sources_help.setWordWrap(True)
        sources_help.setStyleSheet(TEXT_MUTED_TRANSPARENT_STYLE)
        sources_layout.addWidget(sources_help)
        self._build_source_checkboxes(sources_layout)
        self._refresh_default_discovery_provider_checkboxes()

        layout.addWidget(sources_card)

        self.scrapers_status_label = QLabel("")
        self.scrapers_status_label.setStyleSheet(STATUS_LABEL_STYLE)
        layout.addWidget(self.scrapers_status_label)
        layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(TRANSPARENT_SCROLL_AREA_STYLE)
        scroll.setWidget(page)
        return scroll

    def _build_logs_tab(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(TRANSPARENT_BG_STYLE)

        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(16)

        logs_card, logs_layout = self._build_card(expand=True)

        title = QLabel(t("settings.logs.current_session"))
        title.setStyleSheet(SECTION_LABEL_TRANSPARENT_STYLE)
        logs_layout.addWidget(title)

        self.log_meta_label = QLabel("")
        self.log_meta_label.setStyleSheet(LOG_META_STYLE)
        self.log_meta_label.setWordWrap(True)
        logs_layout.addWidget(self.log_meta_label)

        controls = QHBoxLayout()
        controls.setSpacing(10)

        self.errors_only_checkbox = QCheckBox(t("settings.logs.hide_non_errors"))
        self.errors_only_checkbox.setStyleSheet(CHECKBOX_STYLE)
        self.errors_only_checkbox.toggled.connect(lambda _: self._refresh_logs(force=True))

        controls.addWidget(self.errors_only_checkbox)
        controls.addStretch()
        logs_layout.addLayout(controls)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet(LOG_VIEW_STYLE)
        logs_layout.addWidget(self.log_view, 1)

        layout.addWidget(logs_card, 1)

        self.logs_tab = page

        return page

    def _build_card(self, expand: bool = False):
        card = QWidget()
        card.setStyleSheet(SURFACE_PANEL_STYLE)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        if expand:
            card.setMinimumHeight(320)
        return card, layout

    def _build_library_path_input(self, content_type: str) -> QLineEdit:
        current = load_library_content_paths().get(content_type, DEFAULT_LIBRARY_CONTENT_PATHS[content_type])
        line_edit = QLineEdit()
        line_edit.setText(current)
        line_edit.setStyleSheet(INPUT_STYLE)
        return line_edit

    def _build_library_path_row(self, label_text: str, field: QLineEdit, content_type: str):
        row = QHBoxLayout()
        row.setSpacing(8)

        label = QLabel(label_text)
        label.setStyleSheet(TEXT_MUTED_TRANSPARENT_STYLE)
        label.setFixedWidth(92)
        row.addWidget(label)
        row.addWidget(field, 1)

        browse_btn = QPushButton(t("settings.logs.browse"))
        browse_btn.setStyleSheet(BUTTON_STYLE)
        browse_btn.setFixedWidth(90)
        browse_btn.clicked.connect(lambda *_args, kind=content_type: self._browse_library_content_path(kind))
        row.addWidget(browse_btn)
        return row

    def _load_library_content_path_inputs(self):
        content_paths = load_library_content_paths()
        self.webtoon_folder_input.setText(content_paths["webtoon"])
        self.manga_folder_input.setText(content_paths["manga"])
        self.webnovel_folder_input.setText(content_paths["webnovel"])

    def _browse_library_content_path(self, content_type: str):
        current = self._collect_library_content_paths().get(content_type, "")
        folder = QFileDialog.getExistingDirectory(self, t("settings.logs.select_folder", content_type=content_type.title()), current or load_library_path())
        if not folder:
            return
        logger.info("%s content folder selected via dialog: %s", content_type, folder)
        target = {
            "webtoon": self.webtoon_folder_input,
            "manga": self.manga_folder_input,
            "webnovel": self.webnovel_folder_input,
        }.get(content_type)
        if target is not None:
            target.setText(folder)

    def _collect_library_content_paths(self) -> dict[str, str]:
        return {
            "webtoon": str(self.webtoon_folder_input.text() or "").strip(),
            "manga": str(self.manga_folder_input.text() or "").strip(),
            "webnovel": str(self.webnovel_folder_input.text() or "").strip(),
        }

    def _validate_library_content_paths(self, content_paths: dict[str, str]) -> tuple[bool, str]:
        seen = {}
        for content_type in DEFAULT_LIBRARY_CONTENT_PATHS:
            value = str((content_paths or {}).get(content_type) or "").strip()
            if not value:
                return False, t("settings.validation.path_empty", content_type=content_type.title())
            path_obj = Path(value)
            if not path_obj.is_absolute():
                return False, t("settings.validation.path_absolute", content_type=content_type.title())
            normalized = str(path_obj).casefold()
            if normalized in seen:
                return False, t("settings.validation.path_different", content_type=content_type.title(), other=seen[normalized])
            seen[normalized] = content_type
        return True, ""

    def _apply_library_content_paths(self):
        self._save_library_layout(load_library_path(), self._collect_library_content_paths())

    def _save_library_layout(self, path: str, content_paths: dict[str, str]):
        if self._library_move_worker is not None:
            self._set_settings_status(t("settings.library_move.wait"))
            return

        valid, message = self._validate_library_content_paths(content_paths)
        if not valid:
            self._set_settings_status(message)
            return

        old_path = load_library_path()
        old_content_paths = load_library_content_paths()
        new_path = str(path or "").strip()
        new_content_paths = {
            content_type: str(content_paths.get(content_type) or DEFAULT_LIBRARY_CONTENT_PATHS[content_type]).strip()
            for content_type in DEFAULT_LIBRARY_CONTENT_PATHS
        }
        path_changed = old_path != new_path
        content_paths_changed = old_content_paths != new_content_paths

        if not path_changed and not content_paths_changed:
            self._set_settings_status(t("settings.library_move.saved"))
            return

        should_move = self._should_offer_library_move(old_path, new_path, old_content_paths, new_content_paths)
        if should_move:
            answer = QMessageBox.question(
                self,
                t("settings.library_move.title"),
                self._library_move_prompt_text(old_path, new_path, old_content_paths, new_content_paths, path_changed, content_paths_changed),
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.Yes,
            )
            if answer == QMessageBox.Cancel:
                self._load_library_content_path_inputs()
                return
            if answer == QMessageBox.Yes:
                self._pending_library_layout_save = {
                    "path": new_path,
                    "content_paths": dict(new_content_paths),
                    "old_path": old_path,
                    "old_content_paths": dict(old_content_paths),
                }
                self._library_move_worker = _LibraryMoveWorker(
                    old_path,
                    new_path,
                    old_content_paths,
                    new_content_paths,
                )
                self._library_move_worker.result_ready.connect(self._on_library_move_finished)
                self._library_move_worker.finished.connect(self._clear_library_move_worker)
                self._set_settings_status(t("settings.library_move.moving"))
                self._library_move_worker.start()
                return

        self._finalize_library_layout_save(new_path, new_content_paths)

    def _should_offer_library_move(
        self,
        old_path: str,
        new_path: str,
        old_content_paths: dict[str, str],
        new_content_paths: dict[str, str],
    ) -> bool:
        path_changed = old_path != new_path
        content_paths_changed = old_content_paths != new_content_paths
        if not path_changed and not content_paths_changed:
            return False

        old_root = Path(old_path)
        if old_root.exists() and old_root.is_dir() and any(old_root.iterdir()):
            return True
        return any(Path(path).exists() and Path(path).is_dir() and any(Path(path).iterdir()) for path in old_content_paths.values())

    def _library_move_prompt_text(
        self,
        old_path: str,
        new_path: str,
        old_content_paths: dict[str, str],
        new_content_paths: dict[str, str],
        path_changed: bool,
        content_paths_changed: bool,
    ) -> str:
        parts = []
        if path_changed:
            parts.append(f"Move the existing library contents from:\n{old_path}\n\nto:\n{new_path}")
        if content_paths_changed:
            moved_paths = []
            for content_type in DEFAULT_LIBRARY_CONTENT_PATHS:
                old_value = str(old_content_paths.get(content_type) or "").strip()
                new_value = str(new_content_paths.get(content_type) or "").strip()
                if old_value != new_value:
                    moved_paths.append(f"{content_type.title()}: {old_value} -> {new_value}")
            if moved_paths:
                parts.append("\n".join(moved_paths))
        detail = "\n\n".join(parts)
        return f"{detail}{t('settings.library_move.prompt_tail')}"

    def _finalize_library_layout_save(
        self,
        new_path: str,
        new_content_paths: dict[str, str],
    ):
        save_library_path(new_path)
        save_library_content_paths(new_content_paths)
        logger.info("Library layout saved: path=%s content_paths=%s", new_path, new_content_paths)
        self._set_settings_status(t("settings.library_move.saved"))
        self.main_window.library.load_library()

    def _on_library_move_finished(self, payload: object):
        success, error = payload if isinstance(payload, tuple) and len(payload) == 2 else (False, t("settings.library_move.failed"))
        pending = dict(self._pending_library_layout_save or {})
        self._pending_library_layout_save = None
        if not success:
            self._set_settings_status(f"Library move failed: {error}")
            self._load_library_content_path_inputs()
            return

        self._finalize_library_layout_save(
            str(pending.get("path") or load_library_path()),
            dict(pending.get("content_paths") or load_library_content_paths()),
        )

    def _clear_library_move_worker(self):
        worker = self._library_move_worker
        self._library_move_worker = None
        if worker is not None:
            worker.deleteLater()

    def _reset(self):
        logger.info("Resetting settings to defaults")
        save_library_path(DEFAULT_LIBRARY_PATH)
        save_library_content_paths(DEFAULT_LIBRARY_CONTENT_PATHS)
        save_settings({
            VIEWER_AUTO_SKIP_KEY: True,
            VIEWER_FOCUS_MODE_KEY: False,
            VIEWER_MINIMAP_VISIBLE_KEY: True,
            VIEWER_SCENE_ANCHORS_VISIBLE_KEY: True,
            VIEWER_TEXT_PROGRESS_VISIBLE_KEY: True,
            VIEWER_TEXT_SIZE_KEY: 18,
            VIEWER_TEXT_PAGE_COLOR_KEY: "#140e0c",
            VIEWER_TEXT_COLOR_KEY: "#f6ece5",
            VIEWER_MANGA_LAYOUT_KEY: "single",
            VIEWER_MANGA_SPREAD_PARITY_KEY: "odd",
            VIEWER_MANGA_FIT_MODE_KEY: "width",
            VIEWER_NAV_DIRECTION_KEY: "ltr",
            VIEWER_ZOOM_KEY: 0.5,
        })
        save_setting(LIBRARY_USE_CATEGORIES_KEY, True)
        save_setting(LIBRARY_SHOW_NEW_SECTION_KEY, True)
        save_setting(LIBRARY_SHOW_DOWNLOADS_SECTION_KEY, True)
        save_setting(LIBRARY_SHOW_BOOKMARKED_SECTION_KEY, True)
        save_setting(LIBRARY_SHOW_CONTINUE_SECTION_KEY, True)
        save_setting(LIBRARY_SHOW_UPDATES_SECTION_KEY, True)
        save_setting(LIBRARY_SHOW_COMPLETED_SECTION_KEY, True)
        save_setting(APP_UPDATE_CHECK_ON_STARTUP_KEY, True)
        save_setting(LIBRARY_UPDATE_CHECK_ON_STARTUP_KEY, False)
        save_setting(LIBRARY_UPDATE_INTERVAL_MINUTES_KEY, 60)
        save_default_discovery_provider("")
        save_site_availability({})

        self.auto_skip_checkbox.blockSignals(True)
        self.auto_skip_checkbox.setChecked(True)
        self.auto_skip_checkbox.blockSignals(False)

        self.focus_mode_checkbox.blockSignals(True)
        self.focus_mode_checkbox.setChecked(False)
        self.focus_mode_checkbox.blockSignals(False)

        self.minimap_checkbox.blockSignals(True)
        self.minimap_checkbox.setChecked(True)
        self.minimap_checkbox.blockSignals(False)

        self.scene_anchors_checkbox.blockSignals(True)
        self.scene_anchors_checkbox.setChecked(True)
        self.scene_anchors_checkbox.blockSignals(False)

        self.text_progress_checkbox.blockSignals(True)
        self.text_progress_checkbox.setChecked(True)
        self.text_progress_checkbox.blockSignals(False)

        self.text_size_spin.blockSignals(True)
        self.text_size_spin.setValue(18)
        self.text_size_spin.blockSignals(False)

        self.manga_layout_combo.blockSignals(True)
        self.manga_layout_combo.setCurrentIndex(0)
        self.manga_layout_combo.blockSignals(False)

        self.manga_fit_combo.blockSignals(True)
        self.manga_fit_combo.setCurrentIndex(0)
        self.manga_fit_combo.blockSignals(False)

        self.manga_nav_combo.blockSignals(True)
        self.manga_nav_combo.setCurrentIndex(0)
        self.manga_nav_combo.blockSignals(False)

        self._load_library_content_path_inputs()

        self._refresh_reader_color_buttons()

        self.use_categories_checkbox.blockSignals(True)
        self.use_categories_checkbox.setChecked(True)
        self.use_categories_checkbox.blockSignals(False)

        self.show_new_section_checkbox.blockSignals(True)
        self.show_new_section_checkbox.setChecked(True)
        self.show_new_section_checkbox.blockSignals(False)

        self.show_downloads_section_checkbox.blockSignals(True)
        self.show_downloads_section_checkbox.setChecked(True)
        self.show_downloads_section_checkbox.blockSignals(False)

        self.show_bookmarked_section_checkbox.blockSignals(True)
        self.show_bookmarked_section_checkbox.setChecked(True)
        self.show_bookmarked_section_checkbox.blockSignals(False)

        self.show_continue_section_checkbox.blockSignals(True)
        self.show_continue_section_checkbox.setChecked(True)
        self.show_continue_section_checkbox.blockSignals(False)

        self.show_updates_smart_section_checkbox.blockSignals(True)
        self.show_updates_smart_section_checkbox.setChecked(True)
        self.show_updates_smart_section_checkbox.blockSignals(False)

        self.show_completed_section_checkbox.blockSignals(True)
        self.show_completed_section_checkbox.setChecked(True)
        self.show_completed_section_checkbox.blockSignals(False)

        self.check_updates_on_startup_checkbox.blockSignals(True)
        self.check_updates_on_startup_checkbox.setChecked(True)
        self.check_updates_on_startup_checkbox.blockSignals(False)

        self.library_update_check_on_startup_checkbox.blockSignals(True)
        self.library_update_check_on_startup_checkbox.setChecked(False)
        self.library_update_check_on_startup_checkbox.blockSignals(False)

        self.library_update_interval_combo.blockSignals(True)
        self._set_library_update_interval_selection(60)
        self.library_update_interval_combo.blockSignals(False)

        self._refresh_default_discovery_provider_checkboxes()
        self._refresh_source_checkboxes()
        self.refresh_scraper_reliability()
        self.refresh_library_update_status()

        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(50)
        self.zoom_slider.blockSignals(False)
        self.zoom_value_label.setText("50%")

        viewer = getattr(self.main_window, "viewer", None)
        if viewer is not None:
            viewer.auto_skip_enabled = True
            viewer._focus_mode_enabled = False
            viewer._minimap_visible = True
            viewer._scene_anchors_visible = True
            viewer._text_progress_visible = True
            viewer._nav_direction = viewer._normalize_nav_direction("ltr")
            if hasattr(viewer, "_sync_horizontal_navigation_ui"):
                viewer._sync_horizontal_navigation_ui()
            if hasattr(viewer, "nav_toggle"):
                viewer.nav_toggle.blockSignals(True)
                viewer.nav_toggle.setChecked(True)
                viewer.nav_toggle.setText(t("settings.reader.auto_skip_label"))
                viewer.nav_toggle.blockSignals(False)

            viewer._zoom = 0.5
            if hasattr(viewer, "_zoom_slider"):
                viewer._zoom_slider.blockSignals(True)
                viewer._zoom_slider.setValue(50)
                viewer._zoom_slider.blockSignals(False)
            if hasattr(viewer, "_zoom_label"):
                viewer._zoom_label.setText("50%")
            if hasattr(viewer, "preview"):
                viewer.preview.set_zoom(0.5)
            self._apply_text_defaults_to_active_viewer()
            self._apply_manga_defaults_to_active_viewer()
            if hasattr(viewer, "_apply_reader_session_state"):
                viewer._apply_reader_session_state()
            if getattr(viewer, "image_labels", None) and not viewer._is_manga_image_mode():
                viewer.rescale_images()

        self._save(DEFAULT_LIBRARY_PATH)
        self.main_window.reload_scraper_availability()
        self._set_settings_status("Settings reset to defaults.")

    def _on_auto_skip_changed(self, checked: bool):
        save_setting(VIEWER_AUTO_SKIP_KEY, checked)
        logger.info("Viewer auto-skip changed: %s", checked)
        self._set_settings_status(t("settings.reader.saved"))

        viewer = getattr(self.main_window, "viewer", None)
        if viewer is not None:
            viewer.auto_skip_enabled = checked
            if hasattr(viewer, "nav_toggle"):
                viewer.nav_toggle.blockSignals(True)
                viewer.nav_toggle.setChecked(checked)
                viewer.nav_toggle.setText(t("settings.reader.auto_skip_label") if checked else t("settings.reader.standard_label"))
                viewer.nav_toggle.blockSignals(False)

    def _on_focus_mode_changed(self, checked: bool):
        save_setting(VIEWER_FOCUS_MODE_KEY, checked)
        logger.info("Viewer focus mode default changed: %s", checked)
        self._set_settings_status(t("settings.reader.saved"))

        viewer = getattr(self.main_window, "viewer", None)
        if viewer is not None:
            viewer._focus_mode_enabled = bool(checked)
            viewer._apply_reader_session_state()

    def _on_minimap_changed(self, checked: bool):
        save_setting(VIEWER_MINIMAP_VISIBLE_KEY, checked)
        logger.info("Viewer minimap default changed: %s", checked)
        self._set_settings_status(t("settings.reader.saved"))

        viewer = getattr(self.main_window, "viewer", None)
        if viewer is not None:
            viewer._minimap_visible = bool(checked)
            viewer._apply_reader_session_state()

    def _on_scene_anchors_changed(self, checked: bool):
        save_setting(VIEWER_SCENE_ANCHORS_VISIBLE_KEY, checked)
        logger.info("Viewer scene anchor default changed: %s", checked)
        self._set_settings_status(t("settings.reader.saved"))

        viewer = getattr(self.main_window, "viewer", None)
        if viewer is not None:
            viewer._scene_anchors_visible = bool(checked)
            viewer._apply_reader_session_state()

    def _on_text_progress_changed(self, checked: bool):
        save_setting(VIEWER_TEXT_PROGRESS_VISIBLE_KEY, checked)
        logger.info("Viewer text progress default changed: %s", checked)
        self._set_settings_status(t("settings.reader.saved"))

        viewer = getattr(self.main_window, "viewer", None)
        if viewer is not None:
            viewer._text_progress_visible = bool(checked)
            viewer._apply_reader_session_state()

    def _on_text_style_defaults_changed(self, value: int):
        save_setting(VIEWER_TEXT_SIZE_KEY, int(value))
        logger.info("Viewer text size default changed: %s", value)
        self._apply_text_defaults_to_active_viewer()
        self._set_settings_status(t("settings.reader.saved"))

    def _on_manga_layout_defaults_changed(self, _index: int):
        layout_mode, spread_parity = self.manga_layout_combo.currentData() or ("single", "odd")
        fit_mode = str(self.manga_fit_combo.currentData() or "width")
        save_settings({
            VIEWER_MANGA_LAYOUT_KEY: str(layout_mode),
            VIEWER_MANGA_SPREAD_PARITY_KEY: str(spread_parity),
            VIEWER_MANGA_FIT_MODE_KEY: fit_mode,
        })
        logger.info(
            "Viewer manga defaults changed: layout=%s parity=%s fit=%s",
            layout_mode,
            spread_parity,
            fit_mode,
        )
        self._apply_manga_defaults_to_active_viewer()
        self._set_settings_status(t("settings.reader.saved"))

    def _on_manga_navigation_defaults_changed(self, _index: int):
        direction = str(self.manga_nav_combo.currentData() or "ltr")
        save_setting(VIEWER_NAV_DIRECTION_KEY, direction)
        logger.info("Viewer manga navigation default changed: %s", direction)
        self._apply_manga_defaults_to_active_viewer()
        self._set_settings_status(t("settings.reader.saved"))

    def _on_zoom_changed(self, value: int):
        zoom = value / 100.0
        save_setting(VIEWER_ZOOM_KEY, zoom)
        logger.info("Viewer default zoom changed: %.2f", zoom)
        self.zoom_value_label.setText(f"{value}%")
        self._set_settings_status(t("settings.reader.saved"))

        viewer = getattr(self.main_window, "viewer", None)
        if viewer is not None:
            viewer._zoom = zoom
            if hasattr(viewer, "_zoom_slider"):
                viewer._zoom_slider.blockSignals(True)
                viewer._zoom_slider.setValue(value)
                viewer._zoom_slider.blockSignals(False)
            if hasattr(viewer, "_zoom_label"):
                viewer._zoom_label.setText(f"{value}%")
            if hasattr(viewer, "preview"):
                viewer.preview.set_zoom(zoom)
            if getattr(viewer, "image_labels", None):
                viewer.rescale_images()

    def _on_use_categories_changed(self, checked: bool):
        save_setting(LIBRARY_USE_CATEGORIES_KEY, checked)
        logger.info("Library categories enabled changed: %s", checked)
        self._set_settings_status("Library settings saved.")
        self.main_window.library.load_library()

    def _on_show_new_section_changed(self, checked: bool):
        save_setting(LIBRARY_SHOW_NEW_SECTION_KEY, checked)
        logger.info("Library New section visibility changed: %s", checked)
        self._set_settings_status("Library settings saved.")
        self.main_window.library.load_library()

    def _on_show_bookmarked_section_changed(self, checked: bool):
        save_setting(LIBRARY_SHOW_BOOKMARKED_SECTION_KEY, checked)
        self.main_window.library.load_library()
        self.status_label.setText(t("settings.status.library_sections_updated"))

    def _on_show_continue_section_changed(self, checked: bool):
        save_setting(LIBRARY_SHOW_CONTINUE_SECTION_KEY, checked)
        self.main_window.library.load_library()
        self.status_label.setText(t("settings.status.library_sections_updated"))

    def _on_show_updates_smart_section_changed(self, checked: bool):
        save_setting(LIBRARY_SHOW_UPDATES_SECTION_KEY, checked)
        self.main_window.library.load_library()
        self.status_label.setText(t("settings.status.library_sections_updated"))

    def _on_show_completed_section_changed(self, checked: bool):
        save_setting(LIBRARY_SHOW_COMPLETED_SECTION_KEY, checked)
        self.main_window.library.load_library()
        self.status_label.setText(t("settings.status.library_sections_updated"))

    def _on_show_downloads_section_changed(self, checked: bool):
        save_setting(LIBRARY_SHOW_DOWNLOADS_SECTION_KEY, checked)
        logger.info("Library Active Downloads section visibility changed: %s", checked)
        self._set_settings_status("Library settings saved.")
        self.main_window.library.load_library()

    def _on_check_updates_on_startup_changed(self, checked: bool):
        save_setting(APP_UPDATE_CHECK_ON_STARTUP_KEY, checked)
        logger.info("App update startup checks changed: %s", checked)
        self._set_settings_status("Update settings saved.")

    def _set_library_update_interval_selection(self, minutes: int):
        normalized = int(minutes)
        for index, (value, _label) in enumerate(LIBRARY_UPDATE_INTERVAL_OPTIONS):
            if value == normalized:
                self.library_update_interval_combo.setCurrentIndex(index)
                return
        fallback_minutes = LIBRARY_UPDATE_INTERVAL_OPTIONS[3][0]
        self._set_library_update_interval_selection(fallback_minutes)

    def refresh_library_update_status(self):
        last_checked_at = int(load_setting(LIBRARY_UPDATE_LAST_CHECK_AT_KEY, 0) or 0)
        last_result = str(load_setting(LIBRARY_UPDATE_LAST_RESULT_KEY, "") or "").strip()
        self.library_update_meta_label.setText(f"Last checked: {format_check_time(last_checked_at)}")
        self.library_update_status_label.setText(
            f"Latest result: {last_result}" if last_result else "Latest result: Not checked yet."
        )

    def _on_library_update_check_on_startup_changed(self, checked: bool):
        save_setting(LIBRARY_UPDATE_CHECK_ON_STARTUP_KEY, checked)
        logger.info("Library update startup checks changed: %s", checked)
        self._set_settings_status("Library update settings saved.")
        self.main_window.refresh_library_update_schedule()

    def _on_library_update_interval_changed(self, _index: int):
        minutes = int(self.library_update_interval_combo.currentData() or 0)
        save_setting(LIBRARY_UPDATE_INTERVAL_MINUTES_KEY, minutes)
        logger.info("Library update interval changed: %s minutes", minutes)
        self._set_settings_status("Library update settings saved.")
        self.main_window.refresh_library_update_schedule()

    def _open_library_health_report(self):
        dialog = LibraryHealthDialog(self.main_window, self)
        dialog.exec()

    def _check_library_updates_now(self):
        if self.main_window.run_library_update_check(reason="settings_manual"):
            self.library_update_status_label.setText("Latest result: Checking saved titles...")
            self.library_update_meta_label.setText("Last checked: In progress...")
        else:
            self._set_settings_status("A library update check is already in progress.")

    def notify_library_update_check_completed(self):
        self._set_settings_status("Library update check completed.")

    def _set_settings_status(self, message: str):
        self.status_label.setText(message)
        reader_status_label = getattr(self, "reader_status_label", None)
        if reader_status_label is not None:
            reader_status_label.setText(message)
        scrapers_status_label = getattr(self, "scrapers_status_label", None)
        if scrapers_status_label is not None:
            scrapers_status_label.setText(message)

    def _scraper_for_site_name(self, site_name: str):
        normalized_site_name = str(site_name or "").strip()
        if not normalized_site_name:
            return None
        for scraper in get_all_scrapers_including_disabled():
            if str(getattr(scraper, "site_name", "") or "").strip() == normalized_site_name:
                return scraper
        return None

    def _open_source_config_defaults(self, site_name: str):
        scraper = self._scraper_for_site_name(site_name)
        if scraper is None or not scraper.get_source_config_fields():
            self._set_settings_status("This source does not expose custom settings.")
            return
        dialog = ScraperConfigDialog(
            type(scraper),
            load_scraper_default_config(site_name),
            reset_values=type(scraper).default_source_config(),
            reset_label="Reset Defaults",
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        config = dialog.config_values()
        if config == type(scraper).default_source_config():
            reset_scraper_default_config(site_name)
        else:
            save_scraper_default_config(site_name, config)
        self._set_settings_status(f"Saved default settings for {getattr(scraper, 'site_display_name', site_name)}.")

    def _source_rows(self) -> list[dict]:
        rows_by_site = {}

        for scraper in get_all_scrapers_including_disabled():
            site_name = getattr(scraper, "site_name", "") or ""
            if not site_name:
                continue
            row = rows_by_site.setdefault(
                site_name,
                {"site_name": site_name, "label": site_name.replace("_", " ").title(), "download": False, "discover": False},
            )
            row["download"] = True

        for provider in get_all_discovery_providers_including_disabled():
            site_name = getattr(provider, "site_name", "") or ""
            if not site_name:
                continue
            row = rows_by_site.setdefault(
                site_name,
                {"site_name": site_name, "label": provider.get_display_name(), "download": False, "discover": False},
            )
            row["label"] = provider.get_display_name() or row["label"]
            row["discover"] = True

        return sorted(rows_by_site.values(), key=lambda row: row["label"].casefold())

    def _build_source_checkboxes(self, layout: QVBoxLayout):
        self._source_mode_boxes = {}
        self._source_reliability_widgets = {}
        self._default_discovery_provider_checkboxes = {}
        badge_buttons = []
        for row in self._source_rows():
            site_name = row["site_name"]

            row_widget = QWidget()
            row_widget.setStyleSheet(TRANSPARENT_BG_STYLE)
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(10)

            label = QLabel(self._source_checkbox_label(row))
            label.setStyleSheet(TEXT_MUTED_BODY_STYLE)
            row_layout.addWidget(label, 1)

            scraper = self._scraper_for_site_name(site_name)
            has_config_fields = scraper is not None and bool(scraper.get_source_config_fields())

            if row.get("discover"):
                default_checkbox = QCheckBox("Default")
                default_checkbox.setStyleSheet(CHECKBOX_STYLE)
                default_checkbox.toggled.connect(
                    lambda checked, site_name=site_name: self._on_default_discovery_provider_toggled(site_name, checked)
                )
                row_layout.addWidget(default_checkbox, 0, Qt.AlignVCenter)
                self._default_discovery_provider_checkboxes[site_name] = default_checkbox

            gear_btn = QPushButton()
            gear_btn.setIcon(qta.icon("fa5s.cog", color="#d8d8d8"))
            gear_btn.setFixedSize(36, 36)
            gear_btn.setStyleSheet(BUTTON_STYLE)
            gear_btn.setEnabled(has_config_fields)
            gear_btn.setToolTip("Edit default settings for this source." if has_config_fields else "This source does not expose custom settings.")
            gear_btn.clicked.connect(lambda _checked=False, site_name=site_name: self._open_source_config_defaults(site_name))
            row_layout.addWidget(gear_btn, 0, Qt.AlignVCenter)

            mode_box = QComboBox()
            mode_box.setStyleSheet(INPUT_STYLE)
            mode_box.setMinimumWidth(190)
            mode_box.setFont(QFont("Segoe UI", 10))
            mode_view = QListView(mode_box)
            mode_view.setFont(QFont("Segoe UI", 10))
            mode_box.setView(mode_view)
            for text, mode in self._source_availability_options(row):
                mode_box.addItem(text, mode)
            mode_box.setCurrentIndex(
                max(0, mode_box.findData(self._source_mode_for_row(site_name, row)))
            )
            mode_box.currentIndexChanged.connect(
                lambda _index, site_name=site_name, combo=mode_box: self._on_source_mode_changed(
                    site_name,
                    str(combo.currentData() or MODE_ENABLED),
                )
            )
            row_layout.addWidget(mode_box, 0, Qt.AlignVCenter)

            badge_btn = QPushButton("Unknown")
            badge_btn.clicked.connect(
                lambda checked=False, name=site_name: self._start_source_reliability_test_with_popup(name)
            )
            row_layout.addWidget(badge_btn, 0, Qt.AlignVCenter)
            badge_buttons.append(badge_btn)

            self._source_mode_boxes[site_name] = {
                "combo": mode_box,
                "row": dict(row),
            }
            self._source_reliability_widgets[site_name] = {
                "badge": badge_btn,
                "name": row["label"],
            }
            layout.addWidget(row_widget)

        if badge_buttons:
            max_badge_width = max(button.sizeHint().width() for button in badge_buttons)
            max_badge_width = max(max_badge_width, 120)
            for button in badge_buttons:
                button.setFixedWidth(max_badge_width)

    def refresh_scraper_reliability(self):
        for site_name, widgets in self._source_reliability_widgets.items():
            badge = badge_for_site(site_name)
            if site_name in self._reliability_test_workers:
                widgets["badge"].setText("Testing...")
                widgets["badge"].setEnabled(False)
                widgets["badge"].setToolTip("Running live status test...")
                continue

            widgets["badge"].setEnabled(is_download_enabled(site_name))
            widgets["badge"].setText(badge["label"])
            widgets["badge"].setStyleSheet(
                reliability_badge_button_style(badge["color"], badge["background"], badge["border"])
            )
            widgets["badge"].setToolTip(
                f"{badge['tooltip']}\n\nClick to test this source and view details."
            )

    def _start_source_reliability_test_with_popup(self, site_name: str):
        self._pending_reliability_popup_site = str(site_name or "").strip()
        self._test_source_reliability(site_name)

    def _test_source_reliability(self, site_name: str):
        site_name = str(site_name or "").strip()
        if not site_name or site_name in self._reliability_test_workers:
            return
        worker = _SiteReliabilityTestWorker(site_name)
        worker.result_ready.connect(self._on_source_reliability_test_finished)
        worker.finished.connect(worker.deleteLater)
        self._reliability_test_workers[site_name] = worker
        self.refresh_scraper_reliability()
        worker.start()

    def _looks_like_access_block(self, error: str) -> bool:
        text = " ".join(str(error or "").casefold().split())
        return "cloudflare" in text or "anti-bot" in text or "just a moment" in text

    def _on_source_reliability_test_finished(self, site_name: str, succeeded: bool, error: str, duration_ms: int):
        self._reliability_test_workers.pop(str(site_name or "").strip(), None)
        record_site_check(
            site_name,
            source="settings_test",
            succeeded=bool(succeeded),
            duration_ms=int(duration_ms),
            error=str(error or ""),
        )
        if succeeded:
            self._pending_reliability_popup_site = ""
            self._set_settings_status("Source status test completed.")
            self.refresh_scraper_reliability()
            return

        self._set_settings_status(f"Source status test failed: {error}")
        self.refresh_scraper_reliability()

        if self._looks_like_access_block(error):
            self._pending_reliability_popup_site = ""
            url = site_base_url(site_name)
            self._queue_source_authorization(site_name, url)
            return

        self._maybe_show_source_reliability_popup(site_name)

    def _maybe_show_source_reliability_popup(self, site_name: str):
        site_name = str(site_name or "").strip()
        if self._pending_reliability_popup_site != site_name:
            return
        self._pending_reliability_popup_site = ""
        badge = badge_for_site(site_name)
        title = self._source_reliability_widgets.get(site_name, {}).get("name", "Source")
        QMessageBox.information(self, f"{title} Status", badge["tooltip"])

    def _queue_source_authorization(self, site_name: str, url: str):
        site_name = str(site_name or "").strip()
        if not site_name:
            return
        if self._pending_source_authorization == (site_name, url):
            return
        self._pending_source_authorization = (site_name, str(url or "").strip())
        self._set_settings_status("Opening source authorization window...")
        QTimer.singleShot(150, self._run_pending_source_authorization)

    def _run_pending_source_authorization(self):
        pending = self._pending_source_authorization
        self._pending_source_authorization = None
        if not pending:
            return
        site_name, url = pending
        self._open_source_authorization_and_retest(site_name, url)

    def _open_source_authorization_and_retest(self, site_name: str, url: str):
        if not self.isVisible():
            self._set_settings_status("Open Settings again to continue the source authorization flow.")
            return
        try:
            accepted = self.main_window.open_site_authorization(site_name, url=url)
        except Exception as exc:
            logger.exception("Failed to open source authorization dialog for %s", site_name)
            self._set_settings_status(f"Could not open source authorization: {exc}")
            return
        if accepted:
            self._set_settings_status("Source authorization saved. Retesting...")
            self._test_source_reliability(site_name)
        else:
            self._set_settings_status("Source authorization was cancelled.")

    def _refresh_default_discovery_provider_checkboxes(self):
        saved_site_name = load_default_discovery_provider()
        available_sites = {
            getattr(provider, "site_name", "")
            for provider in get_all_discovery_providers()
            if getattr(provider, "site_name", "")
        }
        if saved_site_name and saved_site_name not in available_sites:
            save_default_discovery_provider("")
            saved_site_name = ""

        for site_name, checkbox in self._default_discovery_provider_checkboxes.items():
            checkbox.blockSignals(True)
            checkbox.setChecked(bool(saved_site_name) and site_name == saved_site_name)
            checkbox.setEnabled(site_name in available_sites)
            checkbox.setToolTip(
                "Open Discover on this source by default." if site_name in available_sites else "This source is not currently available in Discover."
            )
            checkbox.blockSignals(False)

    def _on_default_discovery_provider_toggled(self, site_name: str, checked: bool):
        normalized_site_name = str(site_name or "").strip()
        saved_site_name = load_default_discovery_provider()
        if checked:
            save_default_discovery_provider(normalized_site_name)
            logger.info("Default discovery provider changed: %s", normalized_site_name)
            self._set_settings_status("Source settings saved.")
            self._refresh_default_discovery_provider_checkboxes()
            self.main_window.reload_scraper_availability()
            return

        if saved_site_name != normalized_site_name:
            self._refresh_default_discovery_provider_checkboxes()
            return

        save_default_discovery_provider("")
        logger.info("Default discovery provider changed: <auto>")
        self._set_settings_status("Source settings saved.")
        self._refresh_default_discovery_provider_checkboxes()
        self.main_window.reload_scraper_availability()

    def _source_checkbox_label(self, row: dict) -> str:
        capabilities = []
        if row.get("download"):
            capabilities.append("Download")
        if row.get("discover"):
            capabilities.append("Discover")
        suffix = f" ({', '.join(capabilities)})" if capabilities else ""
        return f"{row['label']}{suffix}"

    def _refresh_source_checkboxes(self):
        for site_name, widgets in self._source_mode_boxes.items():
            combo = widgets["combo"]
            row = widgets["row"]
            target_mode = self._source_mode_for_row(site_name, row)
            index = combo.findData(target_mode)
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, index))
            combo.blockSignals(False)

    def _source_availability_options(self, row: dict) -> list[tuple[str, str]]:
        download = bool(row.get("download"))
        discover = bool(row.get("discover"))
        if discover and download:
            return [
                ("On", MODE_ENABLED),
                ("Disable Discovery", MODE_DISCOVERY_DISABLED),
                ("Disable Discovery + Download", MODE_ALL_DISABLED),
            ]
        if discover:
            return [
                ("On", MODE_ENABLED),
                ("Disable Discovery", MODE_ALL_DISABLED),
            ]
        return [
            ("On", MODE_ENABLED),
            ("Disable Download", MODE_ALL_DISABLED),
        ]

    def _source_mode_for_row(self, site_name: str, row: dict) -> str:
        mode = get_site_availability_mode(site_name)
        if mode == MODE_DISCOVERY_DISABLED and not bool(row.get("discover")):
            return MODE_ENABLED
        return mode

    def _on_source_mode_changed(self, site_name: str, mode: str):
        set_site_availability_mode(site_name, mode)
        logger.info("Scraper site availability changed for %s mode=%s", site_name, mode)
        self._set_settings_status("Source settings saved.")
        self._refresh_default_discovery_provider_checkboxes()
        self._refresh_source_checkboxes()
        self.refresh_scraper_reliability()
        self.main_window.reload_scraper_availability()

    def _on_tab_changed(self, index: int):
        if self.tabs.tabText(index) == "Scrapers":
            self.refresh_scraper_reliability()
        if self.tabs.tabText(index) == "Logs":
            if not self._logs_loaded:
                QTimer.singleShot(0, lambda: self._refresh_logs(force=True))
            else:
                self._refresh_logs(force=False)

    def _refresh_logs_if_changed(self):
        if self.tabs.currentWidget() is not getattr(self, "logs_tab", None):
            return
        self._refresh_logs(force=False)

    def _refresh_logs(self, force: bool = False):
        path = current_log_path()
        archives = archived_log_paths()
        errors_only = self.errors_only_checkbox.isChecked()

        if path.exists():
            stat = path.stat()
            stamp = (str(path), stat.st_mtime_ns, stat.st_size, errors_only)
        else:
            stamp = ("missing", errors_only)

        if not force and stamp == self._last_log_stamp:
            return

        self.log_meta_label.setText(
            f"Current file: {path} | Archived sessions kept: {len(archives)}"
        )

        if not path.exists():
            self.log_view.setHtml("<span style='color:#888888;'>No log file created yet.</span>")
            self._last_log_stamp = stamp
            self._last_log_path = str(path)
            self._last_log_size = 0
            self._logs_loaded = True
            return

        incremental_allowed = (
            not force
            and self._logs_loaded
            and not errors_only
            and self._last_log_path == str(path)
            and self._last_log_stamp is not None
            and len(stamp) >= 4
            and len(self._last_log_stamp) >= 4
            and stamp[2] >= self._last_log_size
        )

        if incremental_allowed:
            try:
                appended_text = self._read_log_tail(path, self._last_log_size)
            except OSError as exc:
                logger.error("Failed to read appended log lines", exc_info=exc)
                appended_text = None
            if appended_text is not None:
                if appended_text:
                    cursor = self.log_view.textCursor()
                    cursor.movePosition(QTextCursor.MoveOperation.End)
                    cursor.insertHtml(self._render_log_html(appended_text, errors_only))
                    cursor.movePosition(QTextCursor.MoveOperation.End)
                    self.log_view.setTextCursor(cursor)
                self._last_log_stamp = stamp
                self._last_log_path = str(path)
                self._last_log_size = stamp[2]
                self._logs_loaded = True
                return

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.error("Failed to read current log file", exc_info=exc)
            self.log_view.setHtml(
                f"<span style='color:#ef4444;'>Failed to read log file: {html.escape(str(exc))}</span>"
            )
            self._last_log_stamp = stamp
            self._last_log_path = str(path)
            self._last_log_size = 0
            self._logs_loaded = True
            return

        self.log_view.setHtml(self._render_log_html(text, errors_only))
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_view.setTextCursor(cursor)
        self._last_log_stamp = stamp
        self._last_log_path = str(path)
        self._last_log_size = stamp[2]
        self._logs_loaded = True

    def _read_log_tail(self, path, start: int) -> str:
        with path.open("rb") as handle:
            handle.seek(max(0, int(start)))
            data = handle.read()
        return data.decode("utf-8", errors="replace")

    def _render_log_html(self, text: str, errors_only: bool) -> str:
        lines = text.splitlines()
        chunks = []

        for line in lines:
            level = self._extract_level(line)
            if errors_only and level not in {"WARNING", "ERROR", "CRITICAL"}:
                continue
            color = self._level_color(level)
            chunks.append(f"<div style='color:{color}; white-space:pre-wrap;'>{html.escape(line)}</div>")

        if not chunks:
            if errors_only:
                return "<span style='color:#888888;'>No warnings or errors in the current log.</span>"
            return "<span style='color:#888888;'>Current log is empty.</span>"

        return "".join(chunks)

    def _extract_level(self, line: str) -> str:
        match = _LEVEL_RE.search(line)
        return match.group(1) if match else "INFO"

    def _level_color(self, level: str) -> str:
        return {
            "DEBUG": "#7c8aa0",
            "INFO": "#d0d0d0",
            "WARNING": "#f5c451",
            "ERROR": "#ef4444",
            "CRITICAL": "#ff6b6b",
        }.get(level, "#d0d0d0")

    def schedule_startup_update_check(self):
        if not load_setting(APP_UPDATE_CHECK_ON_STARTUP_KEY, True):
            return
        self._start_update_check(mode="startup")

    def _default_update_action_label(self) -> str:
        release = self._latest_update_result.latest_release if self._latest_update_result else None
        return t("settings.general.update_app") if can_self_update(release) else t("settings.general.view_releases")

    def _set_update_action_idle(self):
        self.download_update_btn.setText(self._default_update_action_label())

    def _set_update_progress_visible(self, visible: bool):
        self.update_progress_bar.setVisible(visible)
        self.update_progress_label.setVisible(visible)

    def _reset_update_progress(self):
        self.update_progress_bar.setRange(0, 100)
        self.update_progress_bar.setValue(0)
        self.update_progress_label.clear()
        self._set_update_progress_visible(False)

    def _set_update_diagnostic(self, text: str):
        self.update_diagnostic_label.setText(text)
        self.update_diagnostic_label.setVisible(bool(text.strip()))

    def _load_previous_update_diagnostic(self) -> str:
        error = load_last_update_error()
        if not error:
            return ""
        first_line = error.splitlines()[0].strip()
        if not first_line:
            first_line = "Unknown update error."
        return (
            f"Previous automatic update failed: {first_line} "
            "See data/last_update_error.txt and data/last_update_trace.txt for details."
        )

    def _format_bytes(self, size: int) -> str:
        value = float(max(0, int(size)))
        units = ["B", "KB", "MB", "GB"]
        unit = units[0]
        for unit in units:
            if value < 1024 or unit == units[-1]:
                break
            value /= 1024.0
        if unit == "B":
            return f"{int(value)} {unit}"
        return f"{value:.1f} {unit}"

    def _load_saved_update_state(self):
        last_version = load_setting(APP_UPDATE_LAST_VERSION_KEY, "")
        last_checked_at = load_setting(APP_UPDATE_LAST_CHECK_AT_KEY, 0)
        last_status = load_setting(APP_UPDATE_LAST_STATUS_KEY, "")
        last_error = load_setting(APP_UPDATE_LAST_ERROR_KEY, "")
        last_url = load_setting(APP_UPDATE_LAST_URL_KEY, "")
        asset_url = load_setting(APP_UPDATE_LAST_ASSET_URL_KEY, "")

        self._latest_release_url = last_url or GITHUB_RELEASES_URL
        self._latest_asset_url = asset_url or ""
        self._set_update_action_idle()
        self._reset_update_progress()
        self._set_update_diagnostic(self._load_previous_update_diagnostic())
        self.update_meta_label.setText(f"Last checked: {format_check_time(last_checked_at)}")

        if last_status == "error" and last_error:
            self.update_status_label.setText(f"Latest release: Check failed. {last_error}")
            self.download_update_btn.setEnabled(False)
            return

        if not last_version:
            self.update_status_label.setText("Latest release: Not checked yet.")
            self.download_update_btn.setEnabled(False)
            return

        if last_version == APP_VERSION:
            self.update_status_label.setText(
                f"Latest release: {display_version(last_version)}. You are up to date."
            )
            self.download_update_btn.setEnabled(False)
            return

        self.update_status_label.setText(
            f"Latest release: {display_version(last_version)} is available."
        )
        self.download_update_btn.setEnabled(bool(self._latest_asset_url or self._latest_release_url))

    def _check_for_app_updates(self):
        self._start_update_check(mode="manual")

    def _start_update_check(self, mode: str):
        if self._update_worker is not None and self._update_worker.isRunning():
            return

        self._pending_update_check_mode = mode
        self.check_updates_btn.setEnabled(False)
        self.download_update_btn.setEnabled(False)
        self.update_status_label.setText("Latest release: Checking GitHub...")
        self.update_meta_label.setText("Last checked: In progress...")

        worker = _AppUpdateWorker(self)
        worker.result_ready.connect(self._on_update_check_finished)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda: setattr(self, "_update_worker", None))
        self._update_worker = worker
        worker.start()

    @Slot(object)
    def _on_update_check_finished(self, result: object):
        if not isinstance(result, UpdateCheckResult):
            logger.warning("Received unexpected app update result: %r", result)
            return

        logger.info(
            "App update check completed available=%s error=%s",
            result.is_update_available,
            bool(result.error_message),
        )
        self._latest_update_result = result
        self.check_updates_btn.setEnabled(True)
        self._save_update_result(result)
        self._apply_update_result(result)

        if self._pending_update_check_mode == "startup":
            self._maybe_notify_startup_update(result)
        elif result.error_message:
            self.status_label.setText("Could not check for app updates.")
        elif result.is_update_available:
            self.status_label.setText("A newer app release is available.")
        else:
            self.status_label.setText("You are on the latest app release.")

    def _save_update_result(self, result: UpdateCheckResult):
        values = {
            APP_UPDATE_LAST_CHECK_AT_KEY: result.checked_at,
        }
        if result.error_message:
            values.update({
                APP_UPDATE_LAST_STATUS_KEY: "error",
                APP_UPDATE_LAST_ERROR_KEY: result.error_message,
            })
            save_settings(values)
            return

        release = result.latest_release
        values.update({
            APP_UPDATE_LAST_STATUS_KEY: "ok",
            APP_UPDATE_LAST_ERROR_KEY: "",
            APP_UPDATE_LAST_VERSION_KEY: release.version if release else "",
            APP_UPDATE_LAST_URL_KEY: release.html_url if release else GITHUB_RELEASES_URL,
            APP_UPDATE_LAST_ASSET_URL_KEY: release.asset.download_url if release and release.asset else "",
        })
        save_settings(values)

    def _apply_update_result(self, result: UpdateCheckResult):
        self._latest_release_url = GITHUB_RELEASES_URL
        self._latest_asset_url = ""
        self._set_update_action_idle()
        self._reset_update_progress()
        self._set_update_diagnostic(self._load_previous_update_diagnostic())
        self.update_meta_label.setText(f"Last checked: {format_check_time(result.checked_at)}")

        if result.error_message:
            self.update_status_label.setText(f"Latest release: Check failed. {result.error_message}")
            self.download_update_btn.setEnabled(False)
            return

        release = result.latest_release
        if release is None:
            self.update_status_label.setText("Latest release: No release information returned.")
            self.download_update_btn.setEnabled(False)
            return

        self._latest_release_url = release.html_url or GITHUB_RELEASES_URL
        self._latest_asset_url = release.asset.download_url if release.asset else ""
        self._set_update_action_idle()

        if result.is_update_available:
            asset_text = release.asset.name if release.asset else "latest release"
            self.update_status_label.setText(
                f"Latest release: {display_version(release.version)} is available. Package: {asset_text}"
            )
            self.download_update_btn.setEnabled(bool(self._latest_asset_url or self._latest_release_url))
            return

        self.update_status_label.setText(
            f"Latest release: {display_version(release.version)}. You are up to date."
        )
        self.download_update_btn.setEnabled(False)

    def _maybe_notify_startup_update(self, result: UpdateCheckResult):
        if result.error_message or not result.is_update_available or result.latest_release is None:
            return

        release = result.latest_release
        last_notified_version = load_setting(APP_UPDATE_LAST_NOTIFIED_VERSION_KEY, "")
        if last_notified_version == release.version:
            return

        save_setting(APP_UPDATE_LAST_NOTIFIED_VERSION_KEY, release.version)
        dialog = _StartupUpdateDialog(
            release.version,
            APP_VERSION,
            can_self_update(release),
            release.body,
            self,
        )
        dialog.install_btn.clicked.connect(lambda: self._trigger_app_update(startup_dialog=dialog))
        dialog.finished.connect(self._clear_startup_update_dialog)
        self._startup_update_dialog = dialog
        dialog.exec()

    def _open_latest_release_download(self):
        logger.info("App update action button clicked")
        self._trigger_app_update()

    def _clear_startup_update_dialog(self, *_args):
        self._startup_update_dialog = None

    def _trigger_app_update(self, startup_dialog: _StartupUpdateDialog | None = None):
        release = self._latest_update_result.latest_release if self._latest_update_result else None
        if release is None:
            logger.info("No release metadata available for self-update; opening releases page instead")
            self._open_releases_page()
            return

        if not can_self_update(release):
            url = self._latest_asset_url or self._latest_release_url or GITHUB_RELEASES_URL
            logger.info(
                "Self-update not available for release=%s asset=%s; opening external download url=%s",
                release.version,
                release.asset.name if release.asset else "",
                url,
            )
            if url:
                QDesktopServices.openUrl(QUrl(url))
            if startup_dialog is not None:
                startup_dialog.accept()
            return

        if self._update_install_worker is not None and self._update_install_worker.isRunning():
            logger.info("Ignoring duplicate self-update request while download is already running")
            return

        logger.info(
            "User accepted self-update release=%s asset=%s",
            release.version,
            release.asset.name if release.asset else "",
        )
        self._set_update_diagnostic("")

        self.check_updates_btn.setEnabled(False)
        self.download_update_btn.setEnabled(False)
        self.download_update_btn.setText("Downloading...")
        self.update_status_label.setText(
            f"Latest release: Downloading {display_version(release.version)} for automatic install..."
        )
        self.update_progress_bar.setRange(0, 100)
        self.update_progress_bar.setValue(0)
        self.update_progress_label.setText("Preparing download...")
        self._set_update_progress_visible(True)
        if startup_dialog is not None:
            startup_dialog.begin_install()

        worker = _AppUpdateInstallWorker(release.asset)
        worker.progress_changed.connect(self._on_update_install_progress)
        worker.result_ready.connect(self._on_update_install_finished)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda: setattr(self, "_update_install_worker", None))
        self._update_install_worker = worker
        worker.start()

    @Slot(int, int)
    def _on_update_install_progress(self, current: int, total: int):
        if total > 0:
            percent = int((max(0, current) / max(1, total)) * 100)
            if percent in {0, 25, 50, 75, 100}:
                logger.info(
                    "Self-update download progress current=%s total=%s percent=%s",
                    current,
                    total,
                    percent,
                )
            self.update_progress_bar.setRange(0, 100)
            self.update_progress_bar.setValue(percent)
            self.update_progress_label.setText(
                f"Downloaded {self._format_bytes(current)} of {self._format_bytes(total)} ({percent}%)"
            )
            self.download_update_btn.setText(f"Downloading {percent}%")
        else:
            self.update_progress_bar.setRange(0, 0)
            self.update_progress_label.setText(f"Downloaded {self._format_bytes(current)}")
            self.download_update_btn.setText("Downloading...")

        if self._startup_update_dialog is not None:
            self._startup_update_dialog.set_progress(current, total, self._format_bytes)

    @Slot(object)
    def _on_update_install_finished(self, payload: object):
        ok = False
        zip_path = ""
        error = "Unknown update error."
        if isinstance(payload, tuple) and len(payload) == 3:
            ok = bool(payload[0])
            zip_path = str(payload[1] or "")
            error = str(payload[2] or error)

        if not ok:
            logger.error("Self-update download worker failed: %s", error)
            self.check_updates_btn.setEnabled(True)
            self.download_update_btn.setEnabled(True)
            self._set_update_action_idle()
            self._reset_update_progress()
            self.update_status_label.setText(f"Latest release: Automatic update failed. {error}")
            self.status_label.setText("Automatic app update failed.")
            if self._startup_update_dialog is not None:
                self._startup_update_dialog.install_failed(error)
            return

        launched, launch_error = launch_windows_update_installer(zip_path)
        if not launched:
            logger.error("Self-update package launch failed zip=%s error=%s", zip_path, launch_error)
            self.check_updates_btn.setEnabled(True)
            self.download_update_btn.setEnabled(True)
            self._set_update_action_idle()
            self._reset_update_progress()
            self.update_status_label.setText(f"Latest release: Could not launch update helper. {launch_error}")
            self.status_label.setText("Automatic app update could not start.")
            if self._startup_update_dialog is not None:
                self._startup_update_dialog.install_failed(launch_error)
            return

        logger.info("Self-update package launch started successfully zip=%s", zip_path)
        self.update_progress_bar.setRange(0, 100)
        self.update_progress_bar.setValue(100)
        self.update_progress_label.setText("Download complete. Closing the app so the update helper can replace files.")
        self._set_update_progress_visible(True)
        self.download_update_btn.setText("Installing...")
        self.update_status_label.setText("Latest release: Installing update and restarting...")
        self.status_label.setText("Closing the app to install the update...")
        if self._startup_update_dialog is not None:
            self._startup_update_dialog.install_launching()
        self.main_window.close()

    def _open_releases_page(self):
        logger.info("Opening releases page url=%s", GITHUB_RELEASES_URL)
        QDesktopServices.openUrl(QUrl(GITHUB_RELEASES_URL))




