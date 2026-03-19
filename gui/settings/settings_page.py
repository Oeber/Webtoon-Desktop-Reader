import html
import os
import re

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QMessageBox,
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
from stores.app_settings_store import get_instance as get_app_settings_store
from core.app_logging import archived_log_paths, current_log_path, get_logger
from core.app_paths import default_library_path
from scrapers.discovery_registry import get_all_discovery_providers_including_disabled
from scrapers.registry import get_all_scrapers_including_disabled
from scrapers.site_availability import is_site_enabled, save_disabled_sites
from gui.common.styles import (
    APP_UPDATE_PROGRESS_STYLE,
    BUTTON_STYLE,
    CHECKBOX_STYLE,
    INPUT_STYLE,
    LOG_META_STYLE,
    LOG_VIEW_STYLE,
    PAGE_BG_STYLE,
    PAGE_TITLE_STYLE,
    PILL_LABEL_STYLE,
    SECTION_LABEL_STYLE,
    SLIDER_STYLE,
    STATUS_LABEL_STYLE,
    SURFACE_PANEL_STYLE,
    TAB_STYLE,
    TEXT_MUTED_LABEL_STYLE,
    VERTICAL_SCROLLBAR_STYLE,
)


logger = get_logger(__name__)

DEFAULT_PATH = str(default_library_path())
LIBRARY_USE_CATEGORIES_KEY = "library_use_categories"
LIBRARY_SHOW_NEW_SECTION_KEY = "library_show_new_section"
LIBRARY_SHOW_DOWNLOADS_SECTION_KEY = "library_show_downloads_section"
APP_UPDATE_CHECK_ON_STARTUP_KEY = "app_update_check_on_startup"
APP_UPDATE_LAST_CHECK_AT_KEY = "app_update_last_check_at"
APP_UPDATE_LAST_VERSION_KEY = "app_update_last_version"
APP_UPDATE_LAST_URL_KEY = "app_update_last_url"
APP_UPDATE_LAST_ASSET_URL_KEY = "app_update_last_asset_url"
APP_UPDATE_LAST_STATUS_KEY = "app_update_last_status"
APP_UPDATE_LAST_ERROR_KEY = "app_update_last_error"
APP_UPDATE_LAST_NOTIFIED_VERSION_KEY = "app_update_last_notified_version"

_LEVEL_RE = re.compile(r"\[(DEBUG|INFO|WARNING|ERROR|CRITICAL)\]")
_app_settings = get_app_settings_store()


def load_library_path() -> str:
    return str(_app_settings.get("library_path", DEFAULT_PATH))


def save_library_path(path: str):
    _app_settings.set("library_path", path)


def load_setting(key: str, default):
    return _app_settings.get(key, default)


def save_setting(key: str, value):
    _app_settings.set(key, value)


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


class _StartupUpdateDialog(QDialog):
    def __init__(self, release_version: str, current_version: str, can_install: bool, parent=None):
        super().__init__(parent)
        self._install_started = False
        self._can_install = bool(can_install)

        self.setWindowTitle("Update Available")
        self.setModal(True)
        self.setMinimumWidth(500)
        self.setStyleSheet(
            "QDialog { background: #100c0c; color: #ffe7e2; }"
            "QWidget#updateDialogPanel { background: #171111; border: 1px solid #4b302c; border-radius: 18px; }"
            f"QLabel {{ background: transparent; color: inherit; }}"
        )

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

        eyebrow = QLabel("APP UPDATE")
        eyebrow.setStyleSheet(SECTION_LABEL_STYLE + " letter-spacing: 0.12em; font-weight: 700;")
        panel_layout.addWidget(eyebrow)

        title = QLabel(f"{display_version(release_version)} is ready to install")
        title.setStyleSheet(PAGE_TITLE_STYLE + " font-size: 24px;")
        title.setWordWrap(True)
        panel_layout.addWidget(title)

        version_row = QHBoxLayout()
        version_row.setContentsMargins(0, 0, 0, 0)
        version_row.setSpacing(8)

        current_pill = QLabel(f"Current {display_version(current_version)}")
        current_pill.setStyleSheet(PILL_LABEL_STYLE)
        version_row.addWidget(current_pill)

        latest_pill = QLabel(f"Latest {display_version(release_version)}")
        latest_pill.setStyleSheet(PILL_LABEL_STYLE)
        version_row.addWidget(latest_pill)
        version_row.addStretch()
        panel_layout.addLayout(version_row)

        self.message_label = QLabel(
            "The app will download the update, close itself, replace the installed files, and relaunch automatically."
            if self._can_install
            else "Automatic install is not available for this build. You can open the release page instead."
        )
        self.message_label.setWordWrap(True)
        self.message_label.setStyleSheet(TEXT_MUTED_LABEL_STYLE + " background: transparent; font-size: 13px;")
        panel_layout.addWidget(self.message_label)

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

        self.close_btn = QPushButton("Close")
        self.close_btn.setStyleSheet(BUTTON_STYLE)
        self.close_btn.clicked.connect(self.reject)
        actions.addWidget(self.close_btn)

        self.install_btn = QPushButton("Update App" if self._can_install else "View Releases")
        self.install_btn.setStyleSheet(BUTTON_STYLE)
        self.install_btn.setDefault(True)
        actions.addWidget(self.install_btn)

        panel_layout.addLayout(actions)

    def begin_install(self):
        self._install_started = True
        self.install_btn.setEnabled(False)
        self.close_btn.setEnabled(False)
        self.message_label.setText("Downloading update for automatic install...")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_label.setText("Preparing download...")
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
                f"Downloaded {format_bytes(current)} of {format_bytes(total)} ({percent}%)"
            )
            self.install_btn.setText(f"Downloading {percent}%")
            return

        self.progress_bar.setRange(0, 0)
        self.progress_label.setText(f"Downloaded {format_bytes(current)}")
        self.install_btn.setText("Downloading...")

    def install_failed(self, error: str):
        self._install_started = False
        self.message_label.setText(f"Automatic update failed.\n\n{error}")
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
        self.progress_label.setText("Download complete. Closing the app so the update helper can replace files.")
        self.message_label.setText("Installing update and restarting...")
        self.install_btn.setText("Installing...")

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
        self._source_checkboxes = {}
        self._update_worker = None
        self._update_install_worker = None
        self._latest_update_result = None
        self._latest_release_url = GITHUB_RELEASES_URL
        self._latest_asset_url = ""
        self._pending_update_check_mode = "manual"
        self._startup_update_dialog = None

        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(PAGE_BG_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(18)
        layout.setAlignment(Qt.AlignTop)

        title = QLabel("Settings")
        title.setStyleSheet(PAGE_TITLE_STYLE)
        layout.addWidget(title)

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(TAB_STYLE)
        self.tabs.addTab(self._build_general_tab(), "General")
        self.tabs.addTab(self._build_logs_tab(), "Logs")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs, 1)

        self._log_refresh_timer = QTimer(self)
        self._log_refresh_timer.timeout.connect(self._refresh_logs_if_changed)
        self._log_refresh_timer.start(1500)
        self._load_saved_update_state()

    def open_logs_tab(self):
        self.tabs.setCurrentWidget(self.logs_tab)
        if not self._logs_loaded:
            QTimer.singleShot(0, lambda: self._refresh_logs(force=True))
        else:
            self._refresh_logs(force=False)

    def _build_general_tab(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: transparent;")

        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignTop)

        library_card, library_layout = self._build_card()
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(10)

        folder_label = QLabel("Library")
        folder_label.setStyleSheet(SECTION_LABEL_STYLE + " background: transparent;")
        header_row.addWidget(folder_label)
        header_row.addStretch()
        library_layout.addLayout(header_row)

        row = QHBoxLayout()
        row.setSpacing(8)

        self.path_input = QLineEdit()
        self.path_input.setText(load_library_path())
        self.path_input.setStyleSheet(INPUT_STYLE)
        self.path_input.editingFinished.connect(self._on_path_edited)

        browse_btn = QPushButton("Browse")
        browse_btn.setStyleSheet(BUTTON_STYLE)
        browse_btn.setFixedWidth(90)
        browse_btn.clicked.connect(self._browse)

        row.addWidget(self.path_input)
        row.addWidget(browse_btn)
        library_layout.addLayout(row)

        self.use_categories_checkbox = QCheckBox("Enable library categories")
        self.use_categories_checkbox.setChecked(load_setting(LIBRARY_USE_CATEGORIES_KEY, True))
        self.use_categories_checkbox.setStyleSheet(CHECKBOX_STYLE)
        self.use_categories_checkbox.toggled.connect(self._on_use_categories_changed)
        library_layout.addWidget(self.use_categories_checkbox)

        self.show_new_section_checkbox = QCheckBox("Show New section")
        self.show_new_section_checkbox.setChecked(load_setting(LIBRARY_SHOW_NEW_SECTION_KEY, True))
        self.show_new_section_checkbox.setStyleSheet(CHECKBOX_STYLE)
        self.show_new_section_checkbox.toggled.connect(self._on_show_new_section_changed)
        library_layout.addWidget(self.show_new_section_checkbox)

        self.show_downloads_section_checkbox = QCheckBox("Show Active Downloads section")
        self.show_downloads_section_checkbox.setChecked(load_setting(LIBRARY_SHOW_DOWNLOADS_SECTION_KEY, True))
        self.show_downloads_section_checkbox.setStyleSheet(CHECKBOX_STYLE)
        self.show_downloads_section_checkbox.toggled.connect(self._on_show_downloads_section_changed)
        library_layout.addWidget(self.show_downloads_section_checkbox)

        layout.addWidget(library_card)

        updates_card, updates_layout = self._build_card()
        updates_header = QHBoxLayout()
        updates_header.setContentsMargins(0, 0, 0, 0)
        updates_header.setSpacing(10)

        updates_label = QLabel("App Updates")
        updates_label.setStyleSheet(SECTION_LABEL_STYLE + " background: transparent;")
        updates_header.addWidget(updates_label)
        updates_header.addStretch()
        updates_layout.addLayout(updates_header)

        current_version_label = QLabel(f"Current version: {display_version(APP_VERSION)}")
        current_version_label.setStyleSheet(TEXT_MUTED_LABEL_STYLE + " background: transparent;")
        updates_layout.addWidget(current_version_label)

        self.update_status_label = QLabel("Latest release: Not checked yet.")
        self.update_status_label.setWordWrap(True)
        self.update_status_label.setStyleSheet(TEXT_MUTED_LABEL_STYLE + " background: transparent;")
        updates_layout.addWidget(self.update_status_label)

        self.update_meta_label = QLabel("Last checked: Never")
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

        self.check_updates_btn = QPushButton("Check for Updates")
        self.check_updates_btn.setStyleSheet(BUTTON_STYLE)
        self.check_updates_btn.setMinimumWidth(140)
        self.check_updates_btn.setMinimumHeight(34)
        self.check_updates_btn.clicked.connect(self._check_for_app_updates)
        update_actions_row.addWidget(self.check_updates_btn)

        self.download_update_btn = QPushButton("Update App")
        self.download_update_btn.setStyleSheet(BUTTON_STYLE)
        self.download_update_btn.setMinimumWidth(140)
        self.download_update_btn.setMinimumHeight(34)
        self.download_update_btn.clicked.connect(self._open_latest_release_download)
        self.download_update_btn.setEnabled(False)
        update_actions_row.addWidget(self.download_update_btn)

        self.view_releases_btn = QPushButton("View Releases")
        self.view_releases_btn.setStyleSheet(BUTTON_STYLE)
        self.view_releases_btn.setMinimumWidth(120)
        self.view_releases_btn.setMinimumHeight(34)
        self.view_releases_btn.clicked.connect(self._open_releases_page)
        update_actions_row.addWidget(self.view_releases_btn)

        update_actions_row.addStretch()
        updates_layout.addLayout(update_actions_row)

        self.check_updates_on_startup_checkbox = QCheckBox("Check GitHub releases on startup")
        self.check_updates_on_startup_checkbox.setChecked(load_setting(APP_UPDATE_CHECK_ON_STARTUP_KEY, True))
        self.check_updates_on_startup_checkbox.setStyleSheet(CHECKBOX_STYLE)
        self.check_updates_on_startup_checkbox.toggled.connect(self._on_check_updates_on_startup_changed)
        updates_layout.addWidget(self.check_updates_on_startup_checkbox)

        layout.addWidget(updates_card)

        sources_card, sources_layout = self._build_card()
        sources_header = QHBoxLayout()
        sources_header.setContentsMargins(0, 0, 0, 0)
        sources_header.setSpacing(10)

        sources_label = QLabel("Sources")
        sources_label.setStyleSheet(SECTION_LABEL_STYLE + " background: transparent;")
        sources_header.addWidget(sources_label)
        sources_header.addStretch()
        sources_layout.addLayout(sources_header)

        sources_help = QLabel("Enable or disable supported scraper sites for downloads, updates, and Discover.")
        sources_help.setWordWrap(True)
        sources_help.setStyleSheet(TEXT_MUTED_LABEL_STYLE + " background: transparent;")
        sources_layout.addWidget(sources_help)
        self._build_source_checkboxes(sources_layout)

        layout.addWidget(sources_card)

        reader_card, reader_layout = self._build_card()
        reader_header = QHBoxLayout()
        reader_header.setContentsMargins(0, 0, 0, 0)
        reader_header.setSpacing(10)

        reader_label = QLabel("Reader Defaults")
        reader_label.setStyleSheet(SECTION_LABEL_STYLE + " background: transparent;")
        reader_header.addWidget(reader_label)
        reader_header.addStretch()
        reader_layout.addLayout(reader_header)

        self.auto_skip_checkbox = QCheckBox("Enable auto panel skip")
        self.auto_skip_checkbox.setChecked(load_setting("viewer_auto_skip", True))
        self.auto_skip_checkbox.setStyleSheet(CHECKBOX_STYLE)
        self.auto_skip_checkbox.toggled.connect(self._on_auto_skip_changed)
        reader_layout.addWidget(self.auto_skip_checkbox)

        zoom_row = QHBoxLayout()
        zoom_row.setSpacing(10)

        zoom_text = QLabel("Default zoom")
        zoom_text.setStyleSheet(TEXT_MUTED_LABEL_STYLE + " background: transparent;")
        zoom_text.setFixedWidth(100)

        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setMinimum(25)
        self.zoom_slider.setMaximum(100)
        self.zoom_slider.setValue(int(load_setting("viewer_zoom", 0.5) * 100))
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

        layout.addWidget(reader_card)

        actions_row = QHBoxLayout()
        actions_row.setContentsMargins(0, 2, 0, 0)
        actions_row.setSpacing(12)

        reset_btn = QPushButton("Reset Defaults")
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
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QWidget { background: transparent; }"
            + VERTICAL_SCROLLBAR_STYLE
        )
        scroll.setWidget(page)
        return scroll

    def _build_logs_tab(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: transparent;")

        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(16)

        logs_card, logs_layout = self._build_card(expand=True)

        title = QLabel("Current Session Log")
        title.setStyleSheet(SECTION_LABEL_STYLE + " background: transparent;")
        logs_layout.addWidget(title)

        self.log_meta_label = QLabel("")
        self.log_meta_label.setStyleSheet(LOG_META_STYLE)
        self.log_meta_label.setWordWrap(True)
        logs_layout.addWidget(self.log_meta_label)

        controls = QHBoxLayout()
        controls.setSpacing(10)

        self.errors_only_checkbox = QCheckBox("Hide non-warning/error lines")
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

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Library Folder")
        if folder:
            logger.info("Library folder selected via dialog: %s", folder)
            self.path_input.setText(folder)
            self._save(folder)

    def _on_path_edited(self):
        self._save(self.path_input.text().strip())

    def _save(self, path: str):
        if not os.path.isdir(path):
            logger.warning("Rejected invalid library folder: %s", path)
            self.status_label.setText("Warning: Folder not found.")
            return

        save_library_path(path)
        logger.info("Library path saved: %s", path)
        self.status_label.setText("Saved.")
        self.main_window.library.load_library()

    def _reset(self):
        logger.info("Resetting settings to defaults")
        self.path_input.setText(DEFAULT_PATH)
        save_setting("viewer_auto_skip", True)
        save_setting("viewer_zoom", 0.5)
        save_setting(LIBRARY_USE_CATEGORIES_KEY, True)
        save_setting(LIBRARY_SHOW_NEW_SECTION_KEY, True)
        save_setting(LIBRARY_SHOW_DOWNLOADS_SECTION_KEY, True)
        save_setting(APP_UPDATE_CHECK_ON_STARTUP_KEY, True)
        save_disabled_sites([])

        self.auto_skip_checkbox.blockSignals(True)
        self.auto_skip_checkbox.setChecked(True)
        self.auto_skip_checkbox.blockSignals(False)

        self.use_categories_checkbox.blockSignals(True)
        self.use_categories_checkbox.setChecked(True)
        self.use_categories_checkbox.blockSignals(False)

        self.show_new_section_checkbox.blockSignals(True)
        self.show_new_section_checkbox.setChecked(True)
        self.show_new_section_checkbox.blockSignals(False)

        self.show_downloads_section_checkbox.blockSignals(True)
        self.show_downloads_section_checkbox.setChecked(True)
        self.show_downloads_section_checkbox.blockSignals(False)

        self.check_updates_on_startup_checkbox.blockSignals(True)
        self.check_updates_on_startup_checkbox.setChecked(True)
        self.check_updates_on_startup_checkbox.blockSignals(False)

        self._refresh_source_checkboxes()

        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(50)
        self.zoom_slider.blockSignals(False)
        self.zoom_value_label.setText("50%")

        viewer = getattr(self.main_window, "viewer", None)
        if viewer is not None:
            viewer.auto_skip_enabled = True
            if hasattr(viewer, "nav_toggle"):
                viewer.nav_toggle.blockSignals(True)
                viewer.nav_toggle.setChecked(True)
                viewer.nav_toggle.setText("Auto Skip")
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
            if getattr(viewer, "image_labels", None):
                viewer.rescale_images()

        self._save(DEFAULT_PATH)
        self.main_window.reload_scraper_availability()
        self.status_label.setText("Settings reset to defaults.")

    def _on_auto_skip_changed(self, checked: bool):
        save_setting("viewer_auto_skip", checked)
        logger.info("Viewer auto-skip changed: %s", checked)
        self.status_label.setText("Reader settings saved.")

        viewer = getattr(self.main_window, "viewer", None)
        if viewer is not None:
            viewer.auto_skip_enabled = checked
            if hasattr(viewer, "nav_toggle"):
                viewer.nav_toggle.blockSignals(True)
                viewer.nav_toggle.setChecked(checked)
                viewer.nav_toggle.setText("Auto Skip" if checked else "Standard")
                viewer.nav_toggle.blockSignals(False)

    def _on_zoom_changed(self, value: int):
        zoom = value / 100.0
        save_setting("viewer_zoom", zoom)
        logger.info("Viewer default zoom changed: %.2f", zoom)
        self.zoom_value_label.setText(f"{value}%")
        self.status_label.setText("Reader settings saved.")

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
        self.status_label.setText("Library settings saved.")
        self.main_window.library.load_library()

    def _on_show_new_section_changed(self, checked: bool):
        save_setting(LIBRARY_SHOW_NEW_SECTION_KEY, checked)
        logger.info("Library New section visibility changed: %s", checked)
        self.status_label.setText("Library settings saved.")
        self.main_window.library.load_library()

    def _on_show_downloads_section_changed(self, checked: bool):
        save_setting(LIBRARY_SHOW_DOWNLOADS_SECTION_KEY, checked)
        logger.info("Library Active Downloads section visibility changed: %s", checked)
        self.status_label.setText("Library settings saved.")
        self.main_window.library.load_library()

    def _on_check_updates_on_startup_changed(self, checked: bool):
        save_setting(APP_UPDATE_CHECK_ON_STARTUP_KEY, checked)
        logger.info("App update startup checks changed: %s", checked)
        self.status_label.setText("Update settings saved.")

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
        for row in self._source_rows():
            checkbox = QCheckBox(self._source_checkbox_label(row))
            checkbox.setStyleSheet(CHECKBOX_STYLE)
            checkbox.setChecked(is_site_enabled(row["site_name"]))
            checkbox.toggled.connect(
                lambda checked, site_name=row["site_name"]: self._on_source_toggled(site_name, checked)
            )
            self._source_checkboxes[row["site_name"]] = checkbox
            layout.addWidget(checkbox)

    def _source_checkbox_label(self, row: dict) -> str:
        capabilities = []
        if row.get("download"):
            capabilities.append("Download")
        if row.get("discover"):
            capabilities.append("Discover")
        suffix = f" ({', '.join(capabilities)})" if capabilities else ""
        return f"{row['label']}{suffix}"

    def _refresh_source_checkboxes(self):
        for site_name, checkbox in self._source_checkboxes.items():
            checkbox.blockSignals(True)
            checkbox.setChecked(is_site_enabled(site_name))
            checkbox.blockSignals(False)

    def _on_source_toggled(self, site_name: str, checked: bool):
        disabled_sites = {
            name for name, checkbox in self._source_checkboxes.items()
            if not (checked if name == site_name else checkbox.isChecked())
        }
        save_disabled_sites(disabled_sites)
        logger.info("Scraper site availability changed for %s enabled=%s", site_name, checked)
        self.status_label.setText("Source settings saved.")
        self.main_window.reload_scraper_availability()

    def _on_tab_changed(self, index: int):
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
        return "Update App" if can_self_update(release) else "Download Latest"

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
        save_setting(APP_UPDATE_LAST_CHECK_AT_KEY, result.checked_at)
        if result.error_message:
            save_setting(APP_UPDATE_LAST_STATUS_KEY, "error")
            save_setting(APP_UPDATE_LAST_ERROR_KEY, result.error_message)
            return

        release = result.latest_release
        save_setting(APP_UPDATE_LAST_STATUS_KEY, "ok")
        save_setting(APP_UPDATE_LAST_ERROR_KEY, "")
        save_setting(APP_UPDATE_LAST_VERSION_KEY, release.version if release else "")
        save_setting(APP_UPDATE_LAST_URL_KEY, release.html_url if release else GITHUB_RELEASES_URL)
        save_setting(
            APP_UPDATE_LAST_ASSET_URL_KEY,
            release.asset.download_url if release and release.asset else "",
        )

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
            self,
        )
        dialog.install_btn.clicked.connect(lambda: self._trigger_app_update(confirm=False, startup_dialog=dialog))
        dialog.finished.connect(self._clear_startup_update_dialog)
        self._startup_update_dialog = dialog
        dialog.exec()

    def _open_latest_release_download(self):
        logger.info("App update action button clicked")
        self._trigger_app_update()

    def _clear_startup_update_dialog(self, *_args):
        self._startup_update_dialog = None

    def _trigger_app_update(self, confirm: bool = True, startup_dialog: _StartupUpdateDialog | None = None):
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

        if confirm:
            result = QMessageBox.question(
                self,
                "Install Update",
                (
                    f"Install {display_version(release.version)} now?\n\n"
                    "The app will download the update, close itself, replace the installed files, and relaunch automatically."
                ),
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Yes,
            )
            if result != QMessageBox.Yes:
                logger.info("User cancelled self-update confirmation for release=%s", release.version)
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
