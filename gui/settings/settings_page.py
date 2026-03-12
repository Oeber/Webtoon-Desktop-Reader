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

from app_settings_store import get_instance as get_app_settings_store
from app_logging import archived_log_paths, current_log_path, get_logger
from app_paths import default_library_path
from gui.common.styles import (
    BUTTON_STYLE,
    INPUT_STYLE,
    PAGE_BG_STYLE,
    PAGE_TITLE_STYLE,
    STATUS_LABEL_STYLE,
    VERTICAL_SCROLLBAR_STYLE,
)


logger = get_logger(__name__)

DEFAULT_PATH = str(default_library_path())

LABEL_STYLE = "color: #8f959e; font-size: 12px; letter-spacing: 0.02em;"
SECTION_STYLE = "color: #f1f1f1; font-size: 15px; font-weight: 700; letter-spacing: 0.03em;"
TAB_STYLE = """
    QTabWidget::pane {
        border: none;
        background: #121212;
        border-radius: 0px;
        top: -2px;
        padding: 10px 0 0 0;
    }
    QTabBar::tab {
        background: #171717;
        color: #8f959e;
        border: none;
        padding: 10px 18px;
        margin-right: 8px;
        border-top-left-radius: 10px;
        border-top-right-radius: 10px;
        font-size: 12px;
        font-weight: 700;
    }
    QTabBar::tab:selected {
        background: #202020;
        color: #f1f1f1;
    }
    QTabBar::tab:hover:!selected {
        background: #1c1c1c;
        color: #cfcfcf;
    }
"""
SURFACE_STYLE = """
    QWidget {
        background: #161616;
        border: none;
        border-radius: 14px;
    }
"""
VALUE_PILL_STYLE = """
    QLabel {
        color: #d8d8d8;
        background: #202020;
        border: none;
        border-radius: 11px;
        padding: 3px 8px;
        font-size: 11px;
        font-weight: 700;
    }
"""
CHECKBOX_STYLE = """
    QCheckBox {
        color: #e6e6e6;
        font-size: 13px;
        spacing: 10px;
        background: transparent;
    }
    QCheckBox::indicator {
        width: 18px;
        height: 18px;
        border-radius: 5px;
        border: 1px solid #3a3a3a;
        background: #151515;
    }
    QCheckBox::indicator:checked {
        background: #d9d9d9;
        border: 1px solid #e5e5e5;
    }
"""
SLIDER_STYLE = """
    QSlider::groove:horizontal {
        height: 8px;
        border-radius: 4px;
        background: #222222;
    }
    QSlider::sub-page:horizontal {
        border-radius: 4px;
        background: #cfcfcf;
    }
    QSlider::add-page:horizontal {
        border-radius: 4px;
        background: #2d2d2d;
    }
    QSlider::handle:horizontal {
        width: 18px;
        margin: -6px 0;
        border-radius: 9px;
        border: 1px solid #d8d8d8;
        background: #f2f2f2;
    }
"""
LOG_META_STYLE = """
    QLabel {
        color: #a9b0b9;
        font-size: 12px;
        background: #1a1a1a;
        border: none;
        border-radius: 10px;
        padding: 10px 12px;
    }
"""
LOG_VIEW_STYLE = """
    QTextEdit {
        background: #101010;
        color: #d8d8d8;
        border: none;
        border-radius: 14px;
        padding: 10px;
        font-family: Consolas, 'Courier New', monospace;
        font-size: 12px;
    }
""" + VERTICAL_SCROLLBAR_STYLE

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
        folder_label.setStyleSheet(SECTION_STYLE + " background: transparent;")
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
        layout.addWidget(library_card)

        reader_card, reader_layout = self._build_card()
        reader_header = QHBoxLayout()
        reader_header.setContentsMargins(0, 0, 0, 0)
        reader_header.setSpacing(10)

        reader_label = QLabel("Reader Defaults")
        reader_label.setStyleSheet(SECTION_STYLE + " background: transparent;")
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
        zoom_text.setStyleSheet(LABEL_STYLE + " background: transparent;")
        zoom_text.setFixedWidth(100)

        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setMinimum(25)
        self.zoom_slider.setMaximum(100)
        self.zoom_slider.setValue(int(load_setting("viewer_zoom", 0.5) * 100))
        self.zoom_slider.setStyleSheet(SLIDER_STYLE)
        self.zoom_slider.valueChanged.connect(self._on_zoom_changed)

        self.zoom_value_label = QLabel(f"{self.zoom_slider.value()}%")
        self.zoom_value_label.setStyleSheet(VALUE_PILL_STYLE)
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
        title.setStyleSheet(SECTION_STYLE + " background: transparent;")
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
        card.setStyleSheet(SURFACE_STYLE)
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

        self.auto_skip_checkbox.blockSignals(True)
        self.auto_skip_checkbox.setChecked(True)
        self.auto_skip_checkbox.blockSignals(False)

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
