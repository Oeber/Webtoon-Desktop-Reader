import html
import os
import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from stores.app_settings_store import get_instance as get_app_settings_store
from core.app_logging import archived_log_paths, current_log_path, get_logger
from core.app_paths import default_library_path
from scrapers.discovery_registry import get_all_discovery_providers_including_disabled
from scrapers.registry import get_all_scrapers_including_disabled
from scrapers.site_availability import is_site_enabled, save_disabled_sites
from gui.common.styles import (
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


class SettingsPage(QWidget):

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self._last_log_stamp = None
        self._last_log_path = None
        self._last_log_size = 0
        self._logs_loaded = False
        self._source_checkboxes = {}

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
        layout.addWidget(self.tabs)

        self._log_refresh_timer = QTimer(self)
        self._log_refresh_timer.timeout.connect(self._refresh_logs_if_changed)
        self._log_refresh_timer.start(1500)

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

        return page

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
