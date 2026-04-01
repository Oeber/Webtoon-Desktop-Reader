import json
import os
import time
import inspect
from pathlib import Path
from bisect import bisect_right
from functools import wraps

import qtawesome as qta
from bs4 import BeautifulSoup
from core.app_logging import get_logger
from core.app_paths import data_path
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QScrollArea,
    QPushButton, QComboBox, QHBoxLayout, QSlider, QMessageBox, QDialog, QInputDialog, QTextBrowser, QSizePolicy, QProgressBar, QColorDialog, QSpinBox,
    QGroupBox
)
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QCursor, QImageReader, QTextDocument
from PySide6.QtCore import Qt, QPoint, QEvent, QEventLoop, QTimer, Signal, QSize

from gui.common.scene_bookmark_dialog import AllSceneBookmarksDialog, SceneBookmarksDialog
from gui.common.styles import (
    ACCENT,
    BG,
    BORDER,
    LOADING_DETAIL_LABEL_STYLE,
    LOADING_TITLE_LABEL_STYLE,
    BUTTON_STYLE,
    INPUT_STYLE,
    SECTION_LABEL_TRANSPARENT_STYLE,
    SURFACE,
    TEXT,
    TEXT_MUTED,
    TEXT_MUTED_BODY_STYLE,
    VIEWER_LOADING_OVERLAY_STYLE,
    VIEWER_TOOLBAR_BUTTON_STYLE,
    VIEWER_TOOLBAR_COMBO_STYLE,
    VIEWER_TOOLBAR_STYLE,
    VIEWER_ZOOM_BUTTON_STYLE,
    VIEWER_ZOOM_LABEL_STYLE,
)
from gui.downloader.download_widgets import SpinnerCircle
from gui.viewer.viewer_skip_logic import (
    build_skip_targets,
)
from gui.viewer.viewer_support import (
    ChapterPreview,
    ContinueDialog,
    ImageLoader,
    PAGE_COLUMN_W,
    PREVIEW_W,
    SPECIAL_CHAPTER_RE,
    VIEWER_AUTO_SCROLL_CURSOR_SIZE,
    VIEWER_AUTO_SCROLL_LINE,
)
from stores.progress_store import get_instance as get_progress_store
from stores.scene_bookmark_store import get_instance as get_scene_bookmark_store
from stores.webtoon_settings_store import get_instance as get_webtoon_settings
from stores.settings_store import (
    VIEWER_AUTO_SKIP_KEY,
    VIEWER_FOCUS_MODE_KEY,
    VIEWER_MINIMAP_VISIBLE_KEY,
    VIEWER_SCENE_ANCHORS_VISIBLE_KEY,
    VIEWER_TEXT_COLOR_KEY,
    VIEWER_TEXT_PAGE_COLOR_KEY,
    VIEWER_TEXT_PROGRESS_VISIBLE_KEY,
    VIEWER_TEXT_SIZE_KEY,
    VIEWER_ZOOM_KEY,
    VIEWER_MANGA_LAYOUT_KEY,
    VIEWER_MANGA_SPREAD_PARITY_KEY,
    VIEWER_MANGA_FIT_MODE_KEY,
    VIEWER_NAV_DIRECTION_KEY,
    load_setting,
    save_setting,
    save_settings,
)

LAZY_WINDOW   = 2000
BATCH_MS      = 16
PREVIEW_EAGER_COUNT = 4
PREVIEW_BATCH_SIZE = 16
PREVIEW_BATCH_MS = 24
SUPPORTED_VIEWER_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".avif")
VIEWER_TOOLBAR_ICON_SIZE = QSize(16, 16)
VIEWER_TOOLBAR_BUTTON_SIZE = 30
VIEWER_TOOLBAR_TRIGGER_HEIGHT = 96
MANGA_SPREAD_GAP = 0
logger = get_logger(__name__)


def _trace_viewer_callable(label: str, func):
    if getattr(func, "_viewer_trace_wrapped", False):
        return func

    signature = inspect.signature(func)
    positional_params = [
        param
        for param in signature.parameters.values()
        if param.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    has_varargs = any(param.kind == inspect.Parameter.VAR_POSITIONAL for param in signature.parameters.values())
    accepts_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values())
    max_positional = None if has_varargs else len(positional_params)

    @wraps(func)
    def _wrapped(*args, **kwargs):
        try:
            logger.info("VIEWER TRACE %s", label)
        except Exception:
            pass
        call_args = args if max_positional is None else args[:max_positional]
        call_kwargs = kwargs if accepts_kwargs else {}
        return func(*call_args, **call_kwargs)

    _wrapped._viewer_trace_wrapped = True
    return _wrapped


class TextReaderSettingsDialog(QDialog):
    def __init__(self, *, text_size: int, page_color: str, text_color: str, on_change=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Text Reader Settings")
        self.setModal(True)
        self.setFixedWidth(360)

        self._default_page_color = "#140e0c"
        self._default_text_color = "#f6ece5"
        self._page_color = str(page_color or self._default_page_color)
        self._text_color = str(text_color or self._default_text_color)
        self._initial_values = {
            "text_size": max(12, min(32, int(text_size))),
            "page_color": self._page_color,
            "text_color": self._text_color,
        }
        self._on_change = on_change

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        size_row = QHBoxLayout()
        size_label = QLabel("Text size")
        self.size_spin = QSpinBox()
        self.size_spin.setRange(12, 32)
        self.size_spin.setValue(self._initial_values["text_size"])
        self.size_spin.valueChanged.connect(lambda _value: self._emit_live_change())
        size_row.addWidget(size_label)
        size_row.addStretch()
        size_row.addWidget(self.size_spin)
        layout.addLayout(size_row)

        self.page_color_btn = QPushButton()
        self.page_color_btn.clicked.connect(self._pick_page_color)
        layout.addLayout(self._build_color_row("Page color", self.page_color_btn, self._reset_page_color))

        self.text_color_btn = QPushButton()
        self.text_color_btn.clicked.connect(self._pick_text_color)
        layout.addLayout(self._build_color_row("Text color", self.text_color_btn, self._reset_text_color))

        self.preview_label = QLabel()
        self.preview_label.setWordWrap(True)
        self.preview_label.setMinimumHeight(120)
        self.preview_label.setTextFormat(Qt.RichText)
        layout.addWidget(self.preview_label)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.accept)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(save_btn)
        layout.addLayout(buttons)

        self._refresh_color_buttons()
        self._update_preview()
        self._emit_live_change()

    def _build_color_row(self, label_text: str, button: QPushButton, reset_callback) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel(label_text))
        row.addStretch()
        button.setFixedWidth(110)
        row.addWidget(button)
        reset_btn = QPushButton("Reset")
        reset_btn.setFixedWidth(56)
        reset_btn.clicked.connect(reset_callback)
        row.addWidget(reset_btn)
        return row

    def _pick_page_color(self):
        color = QColorDialog.getColor(QColor(self._page_color), self, "Choose page color")
        if color.isValid():
            self._page_color = color.name()
            self._refresh_color_buttons()
            self._update_preview()
            self._emit_live_change()

    def _pick_text_color(self):
        color = QColorDialog.getColor(QColor(self._text_color), self, "Choose text color")
        if color.isValid():
            self._text_color = color.name()
            self._refresh_color_buttons()
            self._update_preview()
            self._emit_live_change()

    def _reset_page_color(self):
        self._page_color = self._default_page_color
        self._refresh_color_buttons()
        self._update_preview()
        self._emit_live_change()

    def _reset_text_color(self):
        self._text_color = self._default_text_color
        self._refresh_color_buttons()
        self._update_preview()
        self._emit_live_change()

    def _refresh_color_buttons(self):
        self.page_color_btn.setText(self._page_color.upper())
        self.page_color_btn.setStyleSheet(f"background:{self._page_color}; color:{self._best_contrast(self._page_color)};")
        self.text_color_btn.setText(self._text_color.upper())
        self.text_color_btn.setStyleSheet(f"background:{self._text_color}; color:{self._best_contrast(self._text_color)};")

    def _update_preview(self):
        self.preview_label.setStyleSheet(
            f"background:{self._page_color}; color:{self._text_color}; border:1px solid rgba(255,255,255,0.08); border-radius:12px; padding:14px; font-size:{self.size_spin.value()}px;"
        )
        self.preview_label.setText("<b>Preview</b><br/>The chapter text will use these colors and size.")

    @staticmethod
    def _best_contrast(color_value: str) -> str:
        color = QColor(str(color_value or "#000000"))
        if not color.isValid():
            return "#ffffff"
        luminance = (0.299 * color.red()) + (0.587 * color.green()) + (0.114 * color.blue())
        return "#111111" if luminance > 160 else "#ffffff"

    def _emit_live_change(self):
        if callable(self._on_change):
            self._on_change(self.values)

    def reject(self):
        if callable(self._on_change):
            self._on_change(dict(self._initial_values))
        super().reject()

    def accept(self):
        self._update_preview()
        self._emit_live_change()
        super().accept()

    @property
    def values(self) -> dict:
        return {
            "text_size": int(self.size_spin.value()),
            "page_color": self._page_color,
            "text_color": self._text_color,
        }


class MangaReaderSettingsDialog(QDialog):
    def __init__(self, *, layout_mode: str, spread_parity: str, fit_mode: str, nav_direction: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manga Reader Settings")
        self.setModal(True)
        self.setFixedWidth(340)
        self.setStyleSheet(
            f"""
            QDialog {{
                background: {BG};
                color: {TEXT};
            }}
            QLabel {{
                background: transparent;
                color: {TEXT};
            }}
            QGroupBox {{
                background: {SURFACE};
                color: {TEXT};
                border: 1px solid {BORDER};
                border-radius: 12px;
                margin-top: 10px;
                padding-top: 8px;
                font-weight: 700;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 4px;
                color: {TEXT_MUTED};
            }}
            {INPUT_STYLE}
            {BUTTON_STYLE}
            """
        )

        self._layout_mode = "double" if str(layout_mode or "").strip().casefold() == "double" else "single"
        self._spread_parity = "even" if str(spread_parity or "").strip().casefold() == "even" else "odd"
        self._fit_mode = "height" if str(fit_mode or "").strip().casefold() == "height" else "width"
        self._nav_direction = "rtl" if str(nav_direction or "").strip().casefold() == "rtl" else "ltr"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        intro = QLabel("Choose how manga pages are shown in the viewer.")
        intro.setWordWrap(True)
        intro.setStyleSheet(TEXT_MUTED_BODY_STYLE)
        layout.addWidget(intro)

        page_group = QGroupBox("Page Layout")
        page_layout = QVBoxLayout(page_group)
        page_layout.setContentsMargins(12, 12, 12, 12)
        page_layout.setSpacing(8)
        page_row = QHBoxLayout()
        page_row.addWidget(QLabel("View"))
        page_row.addStretch()
        self.mode_combo = QComboBox()
        self.mode_combo.setFixedWidth(170)
        self.mode_combo.addItem("Single Page", ("single", "odd"))
        self.mode_combo.addItem("Double Page (Odds)", ("double", "odd"))
        self.mode_combo.addItem("Double Page (Evens)", ("double", "even"))
        if self._layout_mode == "double":
            self.mode_combo.setCurrentIndex(2 if self._spread_parity == "even" else 1)
        else:
            self.mode_combo.setCurrentIndex(0)
        page_row.addWidget(self.mode_combo)
        page_layout.addLayout(page_row)
        layout.addWidget(page_group)

        fit_group = QGroupBox("Scale")
        fit_layout = QVBoxLayout(fit_group)
        fit_layout.setContentsMargins(12, 12, 12, 12)
        fit_layout.setSpacing(8)
        fit_row = QHBoxLayout()
        fit_row.addWidget(QLabel("Sizing"))
        fit_row.addStretch()
        self.fit_combo = QComboBox()
        self.fit_combo.setFixedWidth(170)
        self.fit_combo.addItem("Fit Width", "width")
        self.fit_combo.addItem("Fit Height", "height")
        self.fit_combo.setCurrentIndex(max(0, self.fit_combo.findData(self._fit_mode)))
        fit_row.addWidget(self.fit_combo)
        fit_layout.addLayout(fit_row)
        layout.addWidget(fit_group)

        nav_group = QGroupBox("Navigation")
        nav_layout = QVBoxLayout(nav_group)
        nav_layout.setContentsMargins(12, 12, 12, 12)
        nav_layout.setSpacing(8)
        nav_row = QHBoxLayout()
        nav_row.addWidget(QLabel("Direction"))
        nav_row.addStretch()
        self.nav_combo = QComboBox()
        self.nav_combo.setFixedWidth(170)
        self.nav_combo.addItem("Left to Right", "ltr")
        self.nav_combo.addItem("Right to Left", "rtl")
        self.nav_combo.setCurrentIndex(max(0, self.nav_combo.findData(self._nav_direction)))
        nav_row.addWidget(self.nav_combo)
        nav_layout.addLayout(nav_row)
        layout.addWidget(nav_group)

        note = QLabel("Single-page mode keeps the active page centered.")
        note.setWordWrap(True)
        note.setStyleSheet(SECTION_LABEL_TRANSPARENT_STYLE)
        layout.addWidget(note)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(BUTTON_STYLE)
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Save")
        save_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {ACCENT};
                color: {BG};
                border: 1px solid {ACCENT};
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 13px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                background: #ff9e90;
                border-color: #ff9e90;
            }}
            QPushButton:pressed {{
                background: #ff7c69;
                border-color: #ff7c69;
            }}
            """
        )
        save_btn.clicked.connect(self.accept)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(save_btn)
        layout.addLayout(buttons)

    @property
    def values(self) -> dict:
        layout_mode, spread_parity = self.mode_combo.currentData() or ("single", "odd")
        return {
            "layout_mode": str(layout_mode),
            "spread_parity": str(spread_parity),
            "fit_mode": str(self.fit_combo.currentData() or "width"),
            "nav_direction": str(self.nav_combo.currentData() or "ltr"),
        }


class ViewerPage(QWidget):
    chapter_loading_started = Signal(str, str)
    chapter_loading_finished = Signal(str, str)

    def __init__(self, main_window):
        super().__init__()
        self.setFocusPolicy(Qt.StrongFocus)

        self.main_window = main_window
        self.webtoon = None
        self.current_chapter_index = 0
        self.progress_store = get_progress_store()
        self.scene_bookmark_store = get_scene_bookmark_store()
        self.settings_store = get_webtoon_settings()

        self._restore_image_index = None
        self._restore_image_offset = 0.0
        self._restore_text_scroll = 0.0
        self._resize_packed = None
        self._resize_anchor_px = 0
        self._applying_restore = False
        self._manual_navigation_since_chapter_open = False
        self._manga_page_index = 0
        self._chapter_mode = "image"
        self._text_loaded_segments: list[dict] = []
        self._text_segment_bounds: list[dict] = []
        self._text_append_threshold = 0.82
        self._text_prepend_threshold = 0.18
        self._text_max_loaded_segments = 3
        self._pending_text_bookmark: tuple[int, float] | None = None

        self._pending_batch: dict[int, QPixmap] = {}
        self._chapter_image_cache: dict[str, tuple[int, list[str]]] = {}
        self._chapter_image_info_cache: dict[str, tuple[int, list[tuple[str, int, int, int]]]] = {}
        self._queued_preview_indexes: set[int] = set()
        self._pending_preview_queue: list[int] = []
        self._label_heights: list[int] = []
        self._prefix_heights: list[int] = [0]

        self.loader = ImageLoader()
        self.loader.image_ready.connect(self._on_image_ready)
        self.loader.preview_ready.connect(self._on_preview_ready)
        self.loader.panel_ranges_ready.connect(self._on_panel_ranges_ready)

        self._batch_timer = QTimer()
        self._batch_timer.setSingleShot(True)
        self._batch_timer.setInterval(BATCH_MS)
        self._batch_timer.timeout.connect(self._flush_batch)

        self._panel_ranges = []
        self._panel_ranges_dirty = True
        self._panel_build_generation = 0
        self._panel_build_inflight = False

        self._panel_warm_timer = QTimer()
        self._panel_warm_timer.setSingleShot(True)
        self._panel_warm_timer.setInterval(180)
        self._panel_warm_timer.timeout.connect(self._warm_panel_cache)

        self._preview_timer = QTimer()
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(PREVIEW_BATCH_MS)
        self._preview_timer.timeout.connect(self._drain_preview_queue)

        self._zoom_persist_timer = QTimer()
        self._zoom_persist_timer.setSingleShot(True)
        self._zoom_persist_timer.setInterval(250)
        self._zoom_persist_timer.timeout.connect(self._persist_zoom_override_now)

        self._zoom = load_setting(VIEWER_ZOOM_KEY, 0.5)
        self.auto_skip_enabled = load_setting(VIEWER_AUTO_SKIP_KEY, True)
        self._focus_mode_enabled = bool(load_setting(VIEWER_FOCUS_MODE_KEY, False))
        self._minimap_visible = bool(load_setting(VIEWER_MINIMAP_VISIBLE_KEY, True))
        self._scene_anchors_visible = bool(load_setting(VIEWER_SCENE_ANCHORS_VISIBLE_KEY, True))
        self._text_progress_visible = bool(load_setting(VIEWER_TEXT_PROGRESS_VISIBLE_KEY, True))
        self._manga_layout_mode = self._normalize_manga_layout(load_setting(VIEWER_MANGA_LAYOUT_KEY, "single"))
        self._manga_spread_parity = self._normalize_manga_spread_parity(load_setting(VIEWER_MANGA_SPREAD_PARITY_KEY, "odd"))
        self._manga_fit_mode = self._normalize_manga_fit_mode(load_setting(VIEWER_MANGA_FIT_MODE_KEY, "width"))
        self._nav_direction = self._normalize_nav_direction(load_setting(VIEWER_NAV_DIRECTION_KEY, "ltr"))
        self._text_font_size = int(load_setting(VIEWER_TEXT_SIZE_KEY, 18) or 18)
        self._text_page_color = str(load_setting(VIEWER_TEXT_PAGE_COLOR_KEY, "#140e0c") or "#140e0c")
        self._text_color = str(load_setting(VIEWER_TEXT_COLOR_KEY, "#f6ece5") or "#f6ece5")
        self.skip_specials_enabled = False
        self._zoom_override_active = False  # True when this webtoon has a saved override
        self._chapter_scene_marks: list[dict] = []
        # Maps selector combo index to real webtoon.chapters index (used when skip_specials is on)
        self._chapter_index_map: list[int] = []
        self._toolbar_hover_active = False

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.top_bar_widget = QWidget(self)
        self.top_bar_widget.setObjectName("viewerToolbar")
        self.top_bar_widget.setStyleSheet(VIEWER_TOOLBAR_STYLE)
        self.top_bar_widget.setAttribute(Qt.WA_StyledBackground, True)
        self.top_bar_widget.setMouseTracking(True)
        top_bar = QHBoxLayout(self.top_bar_widget)
        top_bar.setContentsMargins(8, 8, 8, 8)
        top_bar.setSpacing(4)

        self._toolbar_hide_timer = QTimer(self)
        self._toolbar_hide_timer.setSingleShot(True)
        self._toolbar_hide_timer.setInterval(180)
        self._toolbar_hide_timer.timeout.connect(self._hide_toolbar_after_hover)

        self.setMouseTracking(True)
        self.installEventFilter(self)
        self.top_bar_widget.installEventFilter(self)

        self.back_button = self._make_toolbar_button("fa5s.arrow-left", "(Esc) Back to details", self.go_back)
        self.prev_button = self._make_toolbar_button("fa5s.chevron-left", "", self._on_left_nav_button)
        self.next_button = self._make_toolbar_button("fa5s.chevron-right", "", self._on_right_nav_button)

        self.chapter_selector = QComboBox()
        self.chapter_selector.setFocusPolicy(Qt.NoFocus)
        self.chapter_selector.setStyleSheet(VIEWER_TOOLBAR_COMBO_STYLE)
        self.chapter_selector.setMinimumWidth(170)
        self.chapter_selector.setMaximumWidth(240)
        self.chapter_selector.setVisible(False)
        self.chapter_selector.currentIndexChanged.connect(self.load_selected_chapter)

        self.nav_toggle = self._make_toolbar_button("fa5s.magic", "(Space) Auto Skip", self._toggle_navigation_mode, checkable=True)
        self.nav_toggle.setChecked(self.auto_skip_enabled)

        self.save_scene_btn = self._make_toolbar_button("fa5s.bookmark", "(S) Save the current scene with an optional note", self._save_scene_bookmark)
        self.scene_list_btn = self._make_toolbar_button("fa5s.images", "(G) Open saved scenes for this chapter", self._open_scene_bookmarks)
        self.focus_mode_btn = self._make_toolbar_button("fa5s.bullseye", "(F) Focus mode", self._toggle_focus_mode, checkable=True)
        self.focus_mode_btn.setChecked(self._focus_mode_enabled)
        self.text_progress_btn = self._make_toolbar_button("fa5s.stream", "(P) Show or hide text chapter progress", self._toggle_text_progress, checkable=True)
        self.text_progress_btn.setChecked(self._text_progress_visible)
        self.text_settings_btn = self._make_toolbar_button("fa5s.font", "(T) Text reader settings", self._open_text_reader_settings)
        self.minimap_btn = self._make_toolbar_button("fa5s.map", "(M) Show or hide the reading mini-map", self._toggle_minimap, checkable=True)
        self.minimap_btn.setChecked(self._minimap_visible)
        self.manga_settings_btn = self._make_toolbar_button("fa5s.book-open", "(T) Manga reader settings", self._open_manga_reader_settings)
        self.anchors_btn = self._make_toolbar_button("fa5s.map-pin", "(A) Show or hide saved scene anchors on the mini-map", self._toggle_scene_anchors, checkable=True)
        self.anchors_btn.setChecked(self._scene_anchors_visible)
        self._sync_horizontal_navigation_ui()
        self.zoom_out_btn = self._make_toolbar_button("fa5s.search-minus", "(-) Decrease image width", self._zoom_out)
        self.zoom_in_btn = self._make_toolbar_button("fa5s.search-plus", "(+) Increase image width", self._zoom_in)

        self._zoom_slider = QSlider(Qt.Horizontal)
        self._zoom_slider.setFixedWidth(100)
        self._zoom_slider.setMinimum(15)
        self._zoom_slider.setMaximum(100)
        self._zoom_slider.setValue(int(self._zoom * 100))
        self._zoom_slider.setFocusPolicy(Qt.NoFocus)
        self._zoom_slider.setToolTip("(+/-) Image width")
        self._zoom_slider.valueChanged.connect(self._on_zoom_slider)

        self._zoom_label = QLabel(f"{int(self._zoom * 100)}%")
        self._zoom_label.setFixedWidth(36)
        self._zoom_label.setAlignment(Qt.AlignCenter)
        self._zoom_label.setStyleSheet(VIEWER_ZOOM_LABEL_STYLE)

        self._zoom_reset_btn = QPushButton("Reset zoom")
        self._zoom_reset_btn.setFocusPolicy(Qt.NoFocus)
        self._zoom_reset_btn.setToolTip("(0) Remove webtoon zoom override and use global default")
        self._zoom_reset_btn.setStyleSheet(VIEWER_ZOOM_BUTTON_STYLE)
        self._zoom_reset_btn.setEnabled(False)  # enabled only when an override is active
        self._zoom_reset_btn.clicked.connect(self._clear_zoom_override)

        top_bar.addWidget(self.back_button)
        top_bar.addWidget(self.prev_button)
        top_bar.addWidget(self.next_button)
        top_bar.addWidget(self.chapter_selector)
        top_bar.addWidget(self.nav_toggle)
        top_bar.addWidget(self.save_scene_btn)
        top_bar.addWidget(self.scene_list_btn)
        top_bar.addWidget(self.focus_mode_btn)
        top_bar.addWidget(self.text_progress_btn)
        top_bar.addWidget(self.text_settings_btn)
        top_bar.addWidget(self.minimap_btn)
        top_bar.addWidget(self.manga_settings_btn)
        top_bar.addWidget(self.anchors_btn)
        top_bar.addStretch()
        top_bar.addWidget(self.zoom_out_btn)
        top_bar.addWidget(self._zoom_slider)
        top_bar.addWidget(self.zoom_in_btn)
        top_bar.addWidget(self._zoom_label)
        top_bar.addSpacing(4)
        top_bar.addWidget(self._zoom_reset_btn)

        content_row = QHBoxLayout()
        content_row.setContentsMargins(0, 0, 0, 0)
        content_row.setSpacing(0)

        reader_column = QVBoxLayout()
        reader_column.setContentsMargins(0, 0, 0, 0)
        reader_column.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.preview = ChapterPreview(self.scroll, metrics_provider=self, scene_jump_callback=self._jump_to_current_scene_mark)
        self.manga_preview = ChapterPreview(self.scroll, metrics_provider=self, scene_jump_callback=self._jump_to_current_scene_mark)
        self.manga_preview.set_display_mode("pages_only")
        self.manga_preview.hide()

        self.text_progress_panel = QWidget()
        self.text_progress_panel.setFixedWidth(PREVIEW_W)
        self.text_progress_panel.setStyleSheet("")
        text_progress_layout = QVBoxLayout(self.text_progress_panel)
        text_progress_layout.setContentsMargins(16, 18, 16, 18)
        text_progress_layout.setSpacing(10)
        text_progress_layout.setAlignment(Qt.AlignHCenter | Qt.AlignTop)

        self.text_side_progress_percent = QLabel("0%")
        self.text_side_progress_percent.setAlignment(Qt.AlignCenter)
        self.text_side_progress_percent.setStyleSheet(
            "color: #fff0e7;"
            "font-size: 24px;"
            "font-weight: 700;"
        )

        self.text_side_progress_label = QLabel("READ")
        self.text_side_progress_label.setAlignment(Qt.AlignCenter)
        self.text_side_progress_label.setStyleSheet(
            "color: rgba(255, 240, 231, 0.62);"
            "font-size: 11px;"
            "font-weight: 700;"
            "letter-spacing: 0.18em;"
        )

        self.text_side_progress_track = QWidget()
        self.text_side_progress_track.setFixedWidth(18)
        self.text_side_progress_track.setMinimumHeight(220)
        self.text_side_progress_track.setStyleSheet(
            "background: rgba(255, 255, 255, 0.08);"
            "border: 1px solid rgba(255, 255, 255, 0.06);"
            "border-radius: 9px;"
        )
        self.text_side_progress_fill = QWidget(self.text_side_progress_track)
        self.text_side_progress_fill.setStyleSheet(
            "background: qlineargradient(x1:0,y1:1,x2:0,y2:0,stop:0 #ffb185, stop:1 #ffe2ce);"
            "border-radius: 7px;"
        )

        text_progress_layout.addWidget(self.text_side_progress_percent)
        text_progress_layout.addWidget(self.text_side_progress_label)
        text_progress_layout.addSpacing(6)
        text_progress_layout.addWidget(self.text_side_progress_track, 1, Qt.AlignHCenter)
        text_progress_layout.addStretch()
        self.text_progress_panel.hide()

        reader_column.addWidget(self.scroll)
        content_row.addLayout(reader_column, 1)
        content_row.addWidget(self.preview)
        content_row.addWidget(self.manga_preview)
        content_row.addWidget(self.text_progress_panel)
        main_layout.addLayout(content_row)

        self.auto_scroll = False
        self.auto_scroll_origin = QPoint()
        self.current_mouse_pos = QPoint()

        self.scroll_timer = QTimer()
        self.scroll_timer.timeout.connect(self.perform_auto_scroll)

        self._resize_timer = QTimer()
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(150)
        self._resize_timer.timeout.connect(self.rescale_images)

        self.scroll.viewport().setMouseTracking(True)
        self.scroll.viewport().installEventFilter(self)
        self.scroll.setMouseTracking(True)

        self.container = QWidget()
        self.container.setMouseTracking(True)
        self.container.installEventFilter(self)
        self.image_layout = QVBoxLayout(self.container)
        self.image_layout.setSpacing(0)
        self.image_layout.setContentsMargins(0, 0, 0, 0)
        self.manga_spread_label = QLabel(self.container)
        self.manga_spread_label.setAlignment(Qt.AlignCenter)
        self.manga_spread_label.setMouseTracking(True)
        self.manga_spread_label.installEventFilter(self)
        self.manga_spread_label.hide()
        self.image_layout.addWidget(self.manga_spread_label, 0, Qt.AlignHCenter)
        self.scroll.setWidget(self.container)

        self.text_container = QWidget()
        self.text_container.setMouseTracking(True)
        self.text_container.installEventFilter(self)
        self.text_layout = QVBoxLayout(self.text_container)
        self.text_layout.setContentsMargins(28, 28, 28, 36)
        self.text_layout.setSpacing(14)
        self.text_layout.setAlignment(Qt.AlignTop | Qt.AlignHCenter)

        self.text_title_label = QLabel()
        self.text_title_label.setWordWrap(True)
        self.text_title_label.setTextFormat(Qt.RichText)
        self.text_title_label.setMaximumWidth(860)
        self.text_title_label.setMouseTracking(True)
        self.text_title_label.installEventFilter(self)
        self.text_title_label.setStyleSheet("")

        self.text_content_label = QTextBrowser()
        self.text_content_label.setOpenExternalLinks(True)
        self.text_content_label.setOpenLinks(True)
        self.text_content_label.setReadOnly(True)
        self.text_content_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.text_content_label.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.text_content_label.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.text_content_label.setStyleSheet("")
        self.text_content_label.setMaximumWidth(860)
        self.text_content_label.setMouseTracking(True)
        self.text_content_label.installEventFilter(self)
        self.text_content_label.viewport().setMouseTracking(True)
        self.text_content_label.viewport().installEventFilter(self)
        self.text_content_label.document().documentLayout().documentSizeChanged.connect(
            lambda _size: self._sync_text_content_height()
        )

        self.text_progress_label = QLabel("0% read")
        self.text_progress_label.setMaximumWidth(860)
        self.text_progress_label.setStyleSheet(
            "color: rgba(246, 236, 229, 0.78);"
            "font-size: 12px;"
            "font-weight: 600;"
            "letter-spacing: 0.04em;"
            "padding: 2px 2px 0 2px;"
        )

        self.text_progress_bar = QProgressBar()
        self.text_progress_bar.setMaximumWidth(860)
        self.text_progress_bar.setTextVisible(False)
        self.text_progress_bar.setRange(0, 1000)
        self.text_progress_bar.setValue(0)
        self.text_progress_bar.setFixedHeight(8)
        self.text_progress_bar.setStyleSheet(
            "QProgressBar {"
            "background: rgba(255, 255, 255, 0.08);"
            "border: 1px solid rgba(255, 255, 255, 0.05);"
            "border-radius: 4px;"
            "}"
            "QProgressBar::chunk {"
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #ffb185, stop:1 #ffd7b8);"
            "border-radius: 4px;"
            "}"
        )

        self.text_progress_label.hide()
        self.text_progress_bar.hide()

        self.text_layout.addWidget(self.text_title_label)
        self.text_layout.addWidget(self.text_content_label)
        self.text_layout.addStretch()

        self.preview.setMouseTracking(True)
        self.preview.installEventFilter(self)
        self._auto_scroll_direction = 0
        self._auto_scroll_cursors = {
            -1: self._build_auto_scroll_cursor(-1),
            0: self._build_auto_scroll_cursor(0),
            1: self._build_auto_scroll_cursor(1),
        }

        self.image_labels = []
        self._label_pool: list[QLabel] = []
        self._chapter_load_total = 0
        self._chapter_load_loaded = 0
        self._chapter_loading_active = False

        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scrollbar_value_changed)
        self.scroll.verticalScrollBar().valueChanged.connect(self.check_visible_images)
        self.scroll.verticalScrollBar().valueChanged.connect(self.preview.update)
        self.scroll.verticalScrollBar().valueChanged.connect(self._update_session_overlay)
        self.scroll.verticalScrollBar().valueChanged.connect(self._update_text_progress_indicator)
        self.scroll.verticalScrollBar().actionTriggered.connect(self._on_scrollbar_action_triggered)

        self._progress_save_timer = QTimer()
        self._progress_save_timer.setSingleShot(True)
        self._progress_save_timer.setInterval(1000)
        self._progress_save_timer.timeout.connect(self._save_progress_deferred)
        self.scroll.verticalScrollBar().valueChanged.connect(
            lambda: self._progress_save_timer.start()
        )
        self._did_immediate_first_paint = False

        self.loading_overlay = QWidget(self.scroll.viewport())
        self.loading_overlay.setStyleSheet(VIEWER_LOADING_OVERLAY_STYLE)
        self.loading_overlay.hide()
        overlay_layout = QVBoxLayout(self.loading_overlay)
        overlay_layout.setContentsMargins(24, 24, 24, 24)
        overlay_layout.setSpacing(10)
        overlay_layout.setAlignment(Qt.AlignCenter)

        self.loading_spinner = SpinnerCircle(self.loading_overlay)
        self.loading_spinner.set_spinning()
        self.loading_label = QLabel("Loading chapter...")
        self.loading_label.setAlignment(Qt.AlignCenter)
        self.loading_label.setStyleSheet(LOADING_TITLE_LABEL_STYLE)
        self.loading_detail_label = QLabel("")
        self.loading_detail_label.setAlignment(Qt.AlignCenter)
        self.loading_detail_label.setStyleSheet(LOADING_DETAIL_LABEL_STYLE)

        overlay_layout.addWidget(self.loading_spinner, 0, Qt.AlignCenter)
        overlay_layout.addWidget(self.loading_label)
        overlay_layout.addWidget(self.loading_detail_label)

        self.session_overlay = QLabel(self.scroll.viewport())
        self.session_overlay.hide()
        self.session_overlay.setWordWrap(True)
        self.session_overlay.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.session_overlay.setStyleSheet(
            "background-color: rgba(8, 10, 14, 180);"
            "color: #f4ede8;"
            "border: 1px solid rgba(255, 255, 255, 32);"
            "border-radius: 10px;"
            "padding: 8px 10px;"
            "font-size: 11px;"
        )
        self._apply_text_reader_style()
        self._apply_reader_session_state(persist=False)
        self._update_text_progress_indicator()

    def _make_toolbar_button(self, icon_name: str, tooltip: str, callback, *, checkable: bool = False) -> QPushButton:
        button = QPushButton()
        button.setFocusPolicy(Qt.NoFocus)
        button.setCheckable(checkable)
        button.setFixedSize(VIEWER_TOOLBAR_BUTTON_SIZE, VIEWER_TOOLBAR_BUTTON_SIZE)
        button.setIcon(qta.icon(icon_name, color="#ffd7cf"))
        button.setIconSize(VIEWER_TOOLBAR_ICON_SIZE)
        button.setToolTip(tooltip)
        button.setStyleSheet(VIEWER_TOOLBAR_BUTTON_STYLE)
        button.clicked.connect(callback)
        return button

    def _position_toolbar(self):
        if not hasattr(self, "top_bar_widget"):
            return
        self.top_bar_widget.adjustSize()
        hint = self.top_bar_widget.sizeHint()
        width = min(hint.width(), max(220, self.width() - 24))
        x = max(12, (self.width() - width) // 2)
        self.top_bar_widget.setGeometry(x, 12, width, hint.height())
        self.top_bar_widget.raise_()

    def _set_toolbar_hover_active(self, active: bool):
        active = bool(active)
        if self._toolbar_hover_active == active:
            return
        self._toolbar_hover_active = active
        self._apply_toolbar_visibility()

    def _toolbar_popup_open(self) -> bool:
        selector = getattr(self, "chapter_selector", None)
        if selector is None:
            return False
        view = selector.view()
        return bool(view is not None and view.isVisible())

    def _hide_toolbar_after_hover(self):
        if self.top_bar_widget.underMouse() or self._toolbar_popup_open():
            return
        self._set_toolbar_hover_active(False)

    def _apply_toolbar_visibility(self):
        if not hasattr(self, "top_bar_widget"):
            return
        should_show = (not self._focus_mode_enabled) and (
            self._toolbar_hover_active or self.top_bar_widget.underMouse() or self._toolbar_popup_open()
        )
        self.top_bar_widget.setVisible(should_show)
        if should_show:
            self._position_toolbar()

    def load_webtoon(self, webtoon, start_chapter: int = 0, start_scroll: float = 0.0):
        logger.info(
            "Viewer loading webtoon=%s chapter_index=%d start_scroll=%.3f",
            webtoon.name,
            start_chapter,
            start_scroll,
        )
        webtoon.path = os.path.abspath(webtoon.path)
        self.webtoon = webtoon
        self._unpack_restore(start_scroll)
        self._apply_webtoon_settings(webtoon, rescale_existing=False)
        self._repopulate_chapter_selector()
        self.current_chapter_index = start_chapter
        self._load_chapter_no_prompt(start_chapter)

    def _apply_reader_session_state(self, *, persist: bool = True):
        self._apply_toolbar_visibility()
        image_mode = self._chapter_mode == "image"
        manga_mode = self._is_manga_image_mode()
        text_mode = self._chapter_mode == "text"
        preview_visible = self._minimap_visible and image_mode and not manga_mode
        manga_preview_visible = self._minimap_visible and image_mode and manga_mode
        self.preview.setVisible(preview_visible)
        self.manga_preview.setVisible(manga_preview_visible)
        self.text_progress_panel.setVisible(text_mode and self._text_progress_visible)
        self.preview.set_scene_marks_visible(self._scene_anchors_visible)
        self.focus_mode_btn.setChecked(self._focus_mode_enabled)
        self.text_progress_btn.setChecked(self._text_progress_visible)
        self.minimap_btn.setChecked(self._minimap_visible)
        self.anchors_btn.setChecked(self._scene_anchors_visible)
        self.nav_toggle.setChecked(self.auto_skip_enabled)
        self.nav_toggle.setToolTip("(Space) Auto Skip enabled" if self.auto_skip_enabled else "(Space) Standard page navigation")
        self.focus_mode_btn.setToolTip("(F) Focus mode on" if self._focus_mode_enabled else "(F) Focus mode off")
        self.text_progress_btn.setToolTip("(P) Text progress visible" if self._text_progress_visible else "(P) Text progress hidden")
        self.text_settings_btn.setToolTip("(T) Text reader settings")
        if manga_mode:
            self.minimap_btn.setToolTip("(M) Page tracker visible" if self._minimap_visible else "(M) Page tracker hidden")
            layout_label = "Double Page" if self._manga_uses_double_page() else "Single Page"
            parity_label = "Evens" if self._normalize_manga_spread_parity(self._manga_spread_parity) == "even" else "Odds"
            fit_label = "Fit Height" if self._manga_fit_mode == "height" else "Fit Width"
            self.manga_settings_btn.setToolTip(f"(T) Manga reader settings ({layout_label}, {parity_label}, {fit_label})")
        else:
            self.minimap_btn.setToolTip("(M) Mini-map visible" if self._minimap_visible else "(M) Mini-map hidden")
        self.anchors_btn.setToolTip("(A) Scene anchors visible" if self._scene_anchors_visible else "(A) Scene anchors hidden")
        self.minimap_btn.setEnabled(image_mode)
        self.anchors_btn.setEnabled(preview_visible)
        self.nav_toggle.setEnabled(image_mode and not manga_mode)
        self.manga_settings_btn.setEnabled(manga_mode)
        self.text_progress_btn.setEnabled(text_mode)
        self.text_settings_btn.setEnabled(text_mode)
        zoom_enabled = image_mode and not manga_mode
        self.zoom_in_btn.setEnabled(zoom_enabled)
        self.zoom_out_btn.setEnabled(zoom_enabled)
        self._zoom_slider.setEnabled(zoom_enabled)
        self._zoom_reset_btn.setEnabled(zoom_enabled and self._zoom_override_active)
        for widget in (
            self.nav_toggle,
            self.minimap_btn,
            self.manga_settings_btn,
            self.anchors_btn,
            self.zoom_out_btn,
            self._zoom_slider,
            self.zoom_in_btn,
            self._zoom_label,
            self._zoom_reset_btn,
        ):
            if widget in (self.nav_toggle, self.anchors_btn):
                widget.setVisible(image_mode and not manga_mode)
            elif widget == self.minimap_btn:
                widget.setVisible(image_mode)
            elif widget == self.manga_settings_btn:
                widget.setVisible(image_mode and manga_mode)
            else:
                widget.setVisible(image_mode and not manga_mode)
        if manga_mode:
            self._sync_manga_page_visibility()
        for widget in (self.save_scene_btn, self.scene_list_btn):
            widget.setVisible(image_mode or text_mode)
        for widget in (self.text_progress_btn, self.text_settings_btn):
            widget.setVisible(text_mode)
        self._position_session_overlay()
        self._update_text_progress_indicator()
        self._update_session_overlay()
        if persist:
            save_setting(VIEWER_FOCUS_MODE_KEY, self._focus_mode_enabled)
            save_setting(VIEWER_MINIMAP_VISIBLE_KEY, self._minimap_visible)
            save_setting(VIEWER_SCENE_ANCHORS_VISIBLE_KEY, self._scene_anchors_visible)
            save_setting(VIEWER_TEXT_PROGRESS_VISIBLE_KEY, self._text_progress_visible)

    def _toggle_focus_mode(self, checked: bool):
        self._focus_mode_enabled = bool(checked)
        if self._focus_mode_enabled:
            self._minimap_visible = True
        self._apply_reader_session_state()
        self.setFocus()

    def _toggle_text_progress(self, checked: bool):
        self._text_progress_visible = bool(checked)
        self._apply_reader_session_state()
        self.setFocus()

    def _open_text_reader_settings(self):
        dialog = TextReaderSettingsDialog(
            text_size=self._text_font_size,
            page_color=self._text_page_color,
            text_color=self._text_color,
            on_change=self._preview_text_reader_settings,
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            self.setFocus()
            return
        values = dialog.values
        self._apply_text_reader_values(values)
        if self.webtoon:
            self.settings_store.set_text_font_size(self.webtoon.name, self._text_font_size)
            self.settings_store.set_text_page_color(self.webtoon.name, self._text_page_color)
            self.settings_store.set_text_color(self.webtoon.name, self._text_color)
        self.setFocus()

    def _preview_text_reader_settings(self, values: dict):
        self._apply_text_reader_values(values)

    def _apply_text_reader_values(self, values: dict):
        self._text_font_size = int(values.get("text_size", self._text_font_size) or self._text_font_size)
        self._text_page_color = str(values.get("page_color", self._text_page_color) or self._text_page_color)
        self._text_color = str(values.get("text_color", self._text_color) or self._text_color)
        self._apply_text_reader_style()
        self._sync_text_content_height()

    def _toggle_minimap(self, checked: bool):
        self._minimap_visible = bool(checked)
        self._apply_reader_session_state()
        self.setFocus()

    def _open_manga_reader_settings(self):
        dialog = MangaReaderSettingsDialog(
            layout_mode=self._manga_layout_mode,
            spread_parity=self._manga_spread_parity,
            fit_mode=self._manga_fit_mode,
            nav_direction=self._nav_direction,
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values
        next_mode = self._normalize_manga_layout(values.get("layout_mode"))
        next_parity = self._normalize_manga_spread_parity(values.get("spread_parity"))
        next_fit = self._normalize_manga_fit_mode(values.get("fit_mode"))
        next_nav_direction = self._normalize_nav_direction(values.get("nav_direction"))
        changed = (
            (next_mode != self._manga_layout_mode)
            or (next_parity != self._manga_spread_parity)
            or (next_fit != self._manga_fit_mode)
            or (next_nav_direction != self._nav_direction)
        )
        self._manga_layout_mode = next_mode
        self._manga_spread_parity = next_parity
        self._manga_fit_mode = next_fit
        self._nav_direction = next_nav_direction
        if changed:
            save_setting(VIEWER_NAV_DIRECTION_KEY, self._nav_direction)
            if self.webtoon:
                view_mode_value = "double_even" if self._manga_layout_mode == "double" and self._manga_spread_parity == "even" else self._manga_layout_mode
                self.settings_store.set_manga_view_mode(self.webtoon.name, view_mode_value)
                self.settings_store.set_manga_fit_mode(self.webtoon.name, self._manga_fit_mode)
            self._sync_horizontal_navigation_ui()
            if self._is_manga_image_mode() and self.image_labels:
                self.rescale_images(previous_zoom=self._zoom)
            self._sync_manga_page_visibility()
            self._apply_reader_session_state()
            self._progress_save_timer.start()
        self.setFocus()

    def _toggle_scene_anchors(self, checked: bool):
        self._scene_anchors_visible = bool(checked)
        self._apply_reader_session_state()
        self.setFocus()

    def _position_session_overlay(self):
        if not hasattr(self, "session_overlay"):
            return
        self.session_overlay.adjustSize()
        margin = 12
        size = self.session_overlay.sizeHint()
        self.session_overlay.setGeometry(margin, margin, min(size.width() + 4, 360), size.height() + 4)

    def _update_session_overlay(self):
        if not hasattr(self, "session_overlay"):
            return
        chapter = self.webtoon.chapters[self.current_chapter_index] if self.webtoon and 0 <= self.current_chapter_index < len(self.webtoon.chapters) else ""
        if self._chapter_mode == "text":
            bar = self.scroll.verticalScrollBar()
            progress = 0.0 if bar.maximum() <= 0 else max(0.0, min(1.0, bar.value() / bar.maximum()))
        elif self._is_manga_image_mode():
            total = max(1, len(self.image_labels))
            visible_indexes = self._manga_visible_indexes()
            last_visible = visible_indexes[-1] if visible_indexes else self._manga_page_index
            progress = ((last_visible + 1) / total) if self.image_labels else 0.0
        else:
            total = max(1, len(self.image_labels))
            progress = max(0.0, min(1.0, self._current_packed_position() / total)) if self.image_labels else 0.0
        scene_count = len(self._chapter_scene_marks)
        if self._chapter_mode == "text":
            shortcut_line = "(F) Focus | (P) Progress | (T) Text settings | ([ ]) Chapter | (Esc) Exit"
            detail_line = "Text chapter"
        elif self._is_manga_image_mode():
            total_pages = max(1, len(self.image_labels))
            shortcut_line = "(Left/Right/Up/Down) Page | (M) Tracker | (T) Settings | ([ ]) Chapter | (S) Save | (G) List | (Esc) Exit"
            visible_indexes = self._manga_visible_indexes()
            if len(visible_indexes) >= 2:
                detail_line = f"Pages {visible_indexes[0] + 1}-{visible_indexes[-1] + 1} / {total_pages}"
            else:
                detail_line = f"Page {visible_indexes[0] + 1 if visible_indexes else 1} / {total_pages}"
        else:
            shortcut_line = "(F) Focus | (M) Map | (S) Save | (G) List | ([ ]) Chapter | (Esc) Exit"
            detail_line = f"Scenes {scene_count}"
        self.session_overlay.setText(
            f"{chapter} | {int(progress * 100)}%\n"
            f"{shortcut_line}\n{detail_line}"
        )
        show_overlay = self._focus_mode_enabled
        self.session_overlay.setVisible(show_overlay)
        self._position_session_overlay()

    def _refresh_scene_marks(self):
        if not self.webtoon or not (0 <= self.current_chapter_index < len(self.webtoon.chapters)):
            self._chapter_scene_marks = []
            self.preview.set_scene_marks([])
            self.manga_preview.set_scene_marks([])
            self.scene_list_btn.setToolTip("No saved bookmarks for this chapter")
            self.scene_list_btn.setEnabled(False)
            self.save_scene_btn.setToolTip("Save a bookmark for this chapter")
            self.save_scene_btn.setEnabled(False)
            self._update_session_overlay()
            return

        chapter = self.webtoon.chapters[self.current_chapter_index]
        if self._chapter_mode == "image":
            marks = sorted(
                self.scene_bookmark_store.list_for_chapter(self.webtoon.name, chapter),
                key=lambda item: float(item.get("packed") or 0.0),
            )
            self._chapter_scene_marks = marks
            self.preview.set_scene_marks(marks)
            self.manga_preview.set_scene_marks([])
            count = len(marks)
            self.scene_list_btn.setToolTip(f"(G) Open saved scenes for this chapter ({count})" if count else "(G) Open saved scenes for this chapter")
            self.scene_list_btn.setEnabled(True)
            self.save_scene_btn.setToolTip("(S) Save the current scene with an optional note")
            self.save_scene_btn.setEnabled(True)
            self._update_session_overlay()
            return

        marks = sorted(
            self.scene_bookmark_store.list_for_webtoon(self.webtoon.name),
            key=lambda item: (str(item.get("chapter") or ""), float(item.get("packed") or 0.0)),
        )
        self._chapter_scene_marks = marks
        self.preview.set_scene_marks([])
        count = len(marks)
        self.scene_list_btn.setToolTip(f"(G) Open saved bookmarks for this novel ({count})" if count else "(G) Open saved bookmarks for this novel")
        self.scene_list_btn.setEnabled(True)
        self.save_scene_btn.setToolTip("(S) Save a bookmark for this chapter")
        self.save_scene_btn.setEnabled(True)
        self._update_session_overlay()

    def _jump_to_current_scene_mark(self, packed: float):
        if not self.webtoon:
            return
        chapter = self.webtoon.chapters[self.current_chapter_index]
        self._jump_to_saved_scene(chapter, packed)
        self.setFocus()

    def open_chapter_with_prompt(self, webtoon, chapter_index: int) -> bool:
        logger.info("Viewer opening chapter with prompt for %s index=%d", webtoon.name, chapter_index)
        webtoon.path = os.path.abspath(webtoon.path)
        self.webtoon = webtoon
        self._apply_webtoon_settings(webtoon)
        self._repopulate_chapter_selector()
        self._pending_scroll_pct = 0.0
        return self._load_chapter_with_prompt(chapter_index)

    def _apply_webtoon_settings(self, webtoon, rescale_existing: bool = True):
        """Apply per-webtoon settings (zoom, hide filler). Called whenever a webtoon is opened."""
        logger.info("Applying viewer settings for %s", webtoon.name)
        # Hide filler
        self.skip_specials_enabled = self.settings_store.get_hide_filler(webtoon.name)

        # Zoom override
        override = self.settings_store.get_zoom_override(webtoon.name)
        if override is not None:
            self._zoom_override_active = True
            self._set_zoom(override, rescale_existing=rescale_existing)
        else:
            self._zoom_override_active = False
            self._set_zoom(load_setting(VIEWER_ZOOM_KEY, 0.5), rescale_existing=rescale_existing)
        default_layout = self._normalize_manga_layout(load_setting(VIEWER_MANGA_LAYOUT_KEY, "single"))
        default_parity = self._normalize_manga_spread_parity(load_setting(VIEWER_MANGA_SPREAD_PARITY_KEY, "odd"))
        default_fit = self._normalize_manga_fit_mode(load_setting(VIEWER_MANGA_FIT_MODE_KEY, "width"))
        saved_view_mode = str(self.settings_store.get_manga_view_mode(webtoon.name) or "").strip().casefold()
        if saved_view_mode == "double_even":
            self._manga_layout_mode = "double"
            self._manga_spread_parity = "even"
        elif saved_view_mode == "double":
            self._manga_layout_mode = "double"
            self._manga_spread_parity = "odd"
        elif saved_view_mode == "single":
            self._manga_layout_mode = "single"
            self._manga_spread_parity = default_parity
        else:
            self._manga_layout_mode = default_layout
            self._manga_spread_parity = default_parity
        self._manga_fit_mode = self._normalize_manga_fit_mode(self.settings_store.get_manga_fit_mode(webtoon.name) or default_fit)
        self._nav_direction = self._normalize_nav_direction(load_setting(VIEWER_NAV_DIRECTION_KEY, "ltr"))
        self._sync_horizontal_navigation_ui()
        self._text_font_size = int(self.settings_store.get_text_font_size(webtoon.name) or load_setting(VIEWER_TEXT_SIZE_KEY, 18) or 18)
        self._text_page_color = str(self.settings_store.get_text_page_color(webtoon.name) or load_setting(VIEWER_TEXT_PAGE_COLOR_KEY, "#140e0c") or "#140e0c")
        self._text_color = str(self.settings_store.get_text_color(webtoon.name) or load_setting(VIEWER_TEXT_COLOR_KEY, "#f6ece5") or "#f6ece5")
        self._zoom_reset_btn.setEnabled(self._zoom_override_active)

    @staticmethod
    def _normalize_manga_layout(value) -> str:
        return "double" if str(value or "").strip().casefold() == "double" else "single"

    @staticmethod
    def _normalize_manga_spread_parity(value) -> str:
        return "even" if str(value or "").strip().casefold() == "even" else "odd"

    @staticmethod
    def _normalize_manga_fit_mode(value) -> str:
        return "height" if str(value or "").strip().casefold() == "height" else "width"

    @staticmethod
    def _normalize_nav_direction(value) -> str:
        return "rtl" if str(value or "").strip().casefold() == "rtl" else "ltr"

    def _horizontal_forward_key(self):
        return Qt.Key_Left if self._nav_direction == "rtl" else Qt.Key_Right

    def _horizontal_back_key(self):
        return Qt.Key_Right if self._nav_direction == "rtl" else Qt.Key_Left

    def _sync_horizontal_navigation_ui(self) -> None:
        if not hasattr(self, "prev_button") or not hasattr(self, "next_button"):
            return
        self.prev_button.setToolTip("([) Previous chapter")
        self.next_button.setToolTip("] Next chapter")
        self.update_nav_buttons()

    def _on_left_nav_button(self):
        self.prev_chapter()

    def _on_right_nav_button(self):
        self.next_chapter()

    def _is_manga_image_mode(self) -> bool:
        if self._chapter_mode != "image" or not self.webtoon:
            return False
        return str(getattr(self.webtoon, "content_type", "webtoon") or "webtoon").strip().casefold() == "manga"

    def _manga_uses_double_page(self) -> bool:
        return self._normalize_manga_layout(self._manga_layout_mode) == "double"

    def _manga_spread_anchor_for_index(self, index: int) -> int:
        if not self.image_labels:
            return 0
        index = max(0, min(len(self.image_labels) - 1, int(index)))
        if not self._manga_uses_double_page():
            return index
        if self._normalize_manga_spread_parity(self._manga_spread_parity) == "odd":
            return index - (index % 2)
        if index <= 0:
            return 0
        return index if index % 2 == 1 else index - 1

    def _manga_visible_indexes(self, index: int | None = None) -> list[int]:
        if not self.image_labels:
            return []
        anchor = self._manga_spread_anchor_for_index(self._manga_page_index if index is None else index)
        if not self._manga_uses_double_page():
            return [anchor]
        if self._normalize_manga_spread_parity(self._manga_spread_parity) == "even" and anchor == 0:
            return [0]
        indexes = [anchor]
        if anchor + 1 < len(self.image_labels):
            indexes.append(anchor + 1)
        return indexes

    def _manga_canvas_width(self) -> int:
        return max(1, self.scroll.viewport().width())

    def _manga_target_page_size(self, index: int | None = None) -> tuple[int, int]:
        viewport_width = self._manga_canvas_width()
        viewport_height = max(1, self.scroll.viewport().height())
        visible_indexes = self._manga_visible_indexes(index)
        if index < 0 or index >= len(self.image_labels):
            return (viewport_width, viewport_height)
        label = self.image_labels[index]
        natural_w = getattr(label, "_natural_width", 0)
        natural_h = getattr(label, "_natural_height", 0)
        fit_mode = self._normalize_manga_fit_mode(self._manga_fit_mode)

        if fit_mode == "height" and natural_w > 0 and natural_h > 0:
            target_h = viewport_height
            target_w = max(1, int(target_h * (natural_w / natural_h)))
            return (target_w, target_h)

        if len(visible_indexes) >= 2:
            target_w = max(1, viewport_width // 2)
        else:
            target_w = viewport_width

        if natural_w > 0 and natural_h > 0:
            target_h = max(1, int(target_w * (natural_h / natural_w)))
        else:
            target_h = max(1, label.height())
        return (target_w, target_h)

    def _manga_display_page_width(self, index: int | None = None) -> int:
        return self._manga_target_page_size(index)[0]

    def _manga_scaled_label_height(self, index: int, zoom: float | None = None) -> int:
        return self._manga_target_page_size(index)[1]

    def _update_manga_spread_label(self) -> None:
        if not hasattr(self, "manga_spread_label"):
            return
        if not self._is_manga_image_mode() or not self.image_labels:
            self.manga_spread_label.clear()
            self.manga_spread_label.hide()
            return

        visible_indexes = self._manga_visible_indexes()
        if not visible_indexes:
            self.manga_spread_label.clear()
            self.manga_spread_label.hide()
            return

        canvas_width = self._manga_canvas_width()
        page_entries: list[tuple[QPixmap | None, int, int]] = []
        max_height = 1
        for index in visible_indexes:
            label = self.image_labels[index]
            src = getattr(label, "_source_pixmap", None)
            if src is None or src.isNull():
                src = getattr(label, "_preview_pixmap", None)
            if src is not None and src.isNull():
                src = None
            scaled_width, scaled_height = self._manga_target_page_size(index)
            max_height = max(max_height, scaled_height)
            page_entries.append((src, scaled_width, scaled_height))

        if len(page_entries) >= 2:
            page_entries.reverse()

        total_width = sum(width for _src, width, _height in page_entries)
        if total_width > canvas_width and total_width > 0:
            scale = canvas_width / total_width
            scaled_entries: list[tuple[QPixmap | None, int, int]] = []
            max_height = 1
            for src, width, height in page_entries:
                next_width = max(1, int(width * scale))
                next_height = max(1, int(height * scale))
                max_height = max(max_height, next_height)
                scaled_entries.append((src, next_width, next_height))
            page_entries = scaled_entries

        canvas = QPixmap(canvas_width, max_height)
        canvas.fill(QColor("#101010"))
        painter = QPainter(canvas)
        painter.fillRect(canvas.rect(), QColor("#101010"))

        spread_width = sum(width for _src, width, _height in page_entries)
        x = max(0, (canvas_width - spread_width) // 2)

        for src, scaled_width, scaled_height in page_entries:
            target_rect = QPoint(x, max(0, (max_height - scaled_height) // 2))
            if src is not None and not src.isNull():
                scaled = src.scaled(scaled_width, scaled_height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                painter.drawPixmap(target_rect, scaled)
            else:
                painter.fillRect(x, target_rect.y(), scaled_width, scaled_height, QColor("#242424"))
            x += scaled_width + MANGA_SPREAD_GAP
        painter.end()

        self.manga_spread_label.setPixmap(canvas)
        self.manga_spread_label.setFixedSize(canvas.size())
        self.manga_spread_label.show()

    def _sync_manga_page_visibility(self) -> None:
        if not self.image_labels:
            self._manga_page_index = 0
            self.manga_spread_label.hide()
            return
        current = self._manga_spread_anchor_for_index(int(self._manga_page_index))
        self._manga_page_index = current
        manga_mode = self._is_manga_image_mode()
        for index, label in enumerate(self.image_labels):
            label.setVisible(not manga_mode)
        if manga_mode:
            self._update_manga_spread_label()
        else:
            self.manga_spread_label.hide()

    def _set_manga_page(self, index: int, offset_frac: float = 0.0) -> bool:
        if not self.image_labels:
            self._manga_page_index = 0
            return False
        self._manga_page_index = self._manga_spread_anchor_for_index(index)
        self._sync_manga_page_visibility()
        visible_indexes = self._manga_visible_indexes()
        start_index = max(0, min(visible_indexes) - 1)
        end_index = min(len(self.image_labels), max(visible_indexes) + 2)
        for probe in range(start_index, end_index):
            self._queue_preview_index(probe)
            label = self.image_labels[probe]
            if getattr(label, "_source_pixmap", None) is None:
                self.loader.load(probe, label.img_path, 0)
        height = max(1, self.manga_spread_label.height())
        target_px = int(max(0.0, min(1.0, float(offset_frac or 0.0))) * max(0, height))
        bar = self.scroll.verticalScrollBar()
        bar.setValue(max(0, min(target_px, bar.maximum())))
        self._update_session_overlay()
        self.preview.update()
        self.manga_preview.update()
        return True

    def _step_manga_page(self, delta: int) -> bool:
        if not self._is_manga_image_mode() or not self.image_labels:
            return False
        visible_indexes = self._manga_visible_indexes()
        if not visible_indexes:
            return False
        if int(delta) > 0:
            next_index = visible_indexes[-1] + 1
        else:
            next_index = visible_indexes[0] - 1
        if next_index < 0 or next_index >= len(self.image_labels):
            return False
        self._set_manga_page(next_index, 0.0)
        self._progress_save_timer.start()
        return True

    def current_preview_image_index(self) -> int:
        if self._is_manga_image_mode():
            return max(0, min(len(self.image_labels) - 1, int(self._manga_page_index))) if self.image_labels else 0
        return self.image_index_at_offset(self.scroll.verticalScrollBar().value())

    def current_preview_image_indexes(self) -> list[int]:
        if self._is_manga_image_mode():
            return list(self._manga_visible_indexes())
        return [self.current_preview_image_index()]

    def jump_to_image_index(self, index: int) -> None:
        if self._is_manga_image_mode():
            self._set_manga_page(index, 0.0)
            self._progress_save_timer.start()
            return
        cumulative = self.cumulative_height_before(index)
        bar = self.scroll.verticalScrollBar()
        bar.setValue(max(0, min(cumulative, bar.maximum())))

    def _current_packed_position(self) -> float:
        if self._is_manga_image_mode():
            if not self.image_labels:
                return 0.0
            index = max(0, min(len(self.image_labels) - 1, int(self._manga_page_index)))
            height = max(1, self.manga_spread_label.height())
            bar = self.scroll.verticalScrollBar()
            offset_frac = (bar.value() / max(1, height)) if height > 0 else 0.0
            return index + max(0.0, min(1.0, offset_frac))
        return self._packed_position_at(self.scroll.verticalScrollBar().value())

    def _scaled_label_height(self, label, zoom: float | None = None) -> int:
        natural_w = getattr(label, '_natural_width', 0)
        natural_h = getattr(label, '_natural_height', 0)
        zoom = self._zoom if zoom is None else zoom
        if natural_w > 0 and natural_h > 0:
            image_width = max(1, int(self.scroll.viewport().width() * zoom))
            return max(1, int(image_width * (natural_h / natural_w)))
        return max(1, label.height())

    def scaled_label_height(self, label) -> int:
        return self._scaled_label_height(label)

    def _reset_layout_metrics(self):
        self._label_heights = []
        self._prefix_heights = [0]

    def _set_label_height_cache(self, index: int, height: int):
        if index < 0 or index >= len(self.image_labels):
            return
        height = max(1, int(height))
        if index >= len(self._label_heights):
            self._label_heights.extend([0] * (index + 1 - len(self._label_heights)))
        if self._label_heights[index] == height:
            return
        self._label_heights[index] = height
        self._rebuild_prefix_heights()

    def _rebuild_prefix_heights(self):
        prefix = [0]
        running = 0
        for height in self._label_heights:
            running += max(1, int(height))
            prefix.append(running)
        self._prefix_heights = prefix

    def cumulative_height_before(self, index: int) -> int:
        if index <= 0:
            return 0
        if index < len(self._prefix_heights):
            return self._prefix_heights[index]
        return self._prefix_heights[-1]

    def total_content_height(self) -> int:
        if self._is_manga_image_mode():
            return max(1, self.manga_spread_label.height())
        return self._prefix_heights[-1] if self._prefix_heights else 0

    def image_index_at_offset(self, scroll_top: int) -> int:
        if not self.image_labels:
            return 0
        idx = bisect_right(self._prefix_heights, max(0, int(scroll_top))) - 1
        return max(0, min(len(self.image_labels) - 1, idx))

    def _packed_position_at(self, scroll_top: int, zoom: float | None = None) -> float:
        if not self.image_labels:
            return 0.0
        if self._is_manga_image_mode():
            index = max(0, min(len(self.image_labels) - 1, int(self._manga_page_index)))
            height = max(1, self.manga_spread_label.height())
            offset_frac = (max(0, int(scroll_top)) / max(1, height)) if height > 0 else 0.0
            return index + max(0.0, min(1.0, offset_frac))
        if zoom is None or abs(zoom - self._zoom) < 0.0001:
            idx = self.image_index_at_offset(scroll_top)
            cumulative = self.cumulative_height_before(idx)
            h = self._label_heights[idx] if idx < len(self._label_heights) else self._scaled_label_height(self.image_labels[idx], zoom)
            offset_frac = ((scroll_top - cumulative) / h) if h > 0 else 0.0
            return idx + offset_frac
        scroll_top = max(0, scroll_top)
        cumulative = 0
        for i, label in enumerate(self.image_labels):
            h = self._scaled_label_height(label, zoom)
            if cumulative + h > scroll_top:
                offset_frac = ((scroll_top - cumulative) / h) if h > 0 else 0.0
                return i + offset_frac
            cumulative += h
        return len(self.image_labels) - 1

    def packed_to_content_offset(self, packed: float) -> int:
        if not self.image_labels:
            return 0
        if self._is_manga_image_mode():
            total = len(self.image_labels)
            packed = max(0.0, float(packed))
            idx = max(0, min(total - 1, int(packed)))
            frac = max(0.0, min(1.0, packed - int(packed)))
            height = max(1, self.manga_spread_label.height())
            return int(height * frac)
        total = len(self.image_labels)
        packed = max(0.0, float(packed))
        if packed >= total:
            return self.total_content_height()
        idx = max(0, min(total - 1, int(packed)))
        frac = max(0.0, min(1.0, packed - int(packed)))
        base = self.cumulative_height_before(idx)
        height = self._label_heights[idx] if idx < len(self._label_heights) else self._scaled_label_height(self.image_labels[idx])
        return base + int(height * frac)

    def _save_progress_deferred(self):
        self._save_progress(immediate=False)

    def _save_progress(self, *, immediate: bool = True):
        if not self.webtoon:
            return
        if self._chapter_mode == "text":
            self._save_text_progress(immediate=immediate)
            return
        chapter = self.webtoon.chapters[self.current_chapter_index]
        bar = self.scroll.verticalScrollBar()
        if not self.image_labels:
            return
        total = len(self.image_labels)
        if bar.value() >= bar.maximum() and bar.maximum() > 0:
            packed = float(total)
        else:
            packed = self._current_packed_position()
        logger.info(
            "Viewer saving progress for %s chapter=%s packed=%.3f total=%d immediate=%s",
            self.webtoon.name,
            chapter,
            packed,
            total,
            immediate,
        )
        self.progress_store.save(self.webtoon.name, chapter, packed, total, immediate=immediate)
        self._advance_resume_to_next_chapter(chapter, packed, total)

    def _save_text_progress(self, *, immediate: bool = True):
        if not self.webtoon or not self._text_loaded_segments:
            return
        bar = self.scroll.verticalScrollBar()
        active = self._active_text_segment(bar.value())
        if active is None:
            chapter = self.webtoon.chapters[self.current_chapter_index]
            progress = 0.0 if bar.maximum() <= 0 else max(0.0, min(1.0, bar.value() / bar.maximum()))
            self.progress_store.save(self.webtoon.name, chapter, progress, 1, immediate=immediate)
            return
        entries = []
        for segment in self._text_loaded_segments:
            chapter = segment["chapter"]
            index = int(segment["index"])
            if index < int(active["index"]):
                entries.append((chapter, 1.0, 1))
            elif index == int(active["index"]):
                entries.append((chapter, float(active["progress"]), 1))
        if entries:
            logger.info(
                "Viewer saving text progress for %s active_chapter=%s progress=%.3f loaded_segments=%d",
                self.webtoon.name,
                active["chapter"],
                float(active["progress"]),
                len(self._text_loaded_segments),
            )
            self.progress_store.save_many(self.webtoon.name, entries)
            self._advance_resume_to_next_chapter(str(active["chapter"]), float(active["progress"]), 1)

    def _advance_resume_to_next_chapter(self, chapter: str, scroll: float, total_images: int) -> None:
        if not self.webtoon:
            return
        if total_images <= 0 or float(scroll) < float(total_images):
            return
        try:
            current_index = self.webtoon.chapters.index(chapter)
        except ValueError:
            return
        next_index = current_index + 1
        if next_index >= len(self.webtoon.chapters):
            return
        next_chapter = self.webtoon.chapters[next_index]
        next_progress = float(self.progress_store.get_for_chapter(self.webtoon.name, next_chapter) or 0.0)
        if next_progress > 0.005:
            return
        logger.info(
            "Viewer advancing resume point for %s from %s to %s",
            self.webtoon.name,
            chapter,
            next_chapter,
        )
        self.progress_store.save(self.webtoon.name, next_chapter, 0.0, 0)

    def _current_scene_bookmark_payload(self) -> tuple[str, float, int, float] | None:
        if not self.webtoon:
            return None
        if self._chapter_mode == "text":
            active = self._active_text_segment(self.scroll.verticalScrollBar().value())
            if active is None:
                return None
            chapter = str(active["chapter"])
            packed = max(0.0, min(1.0, float(active.get("progress") or 0.0)))
            return chapter, packed, 0, packed
        if not self.image_labels:
            return None
        chapter = self.webtoon.chapters[self.current_chapter_index]
        total = len(self.image_labels)
        bar = self.scroll.verticalScrollBar()
        if bar.value() >= bar.maximum() and bar.maximum() > 0:
            packed = float(total)
            offset_frac = 1.0
        else:
            packed = self._current_packed_position()
            offset_frac = packed - int(packed)
        image_index = max(0, min(total - 1, int(packed)))
        return chapter, packed, image_index, max(0.0, min(1.0, offset_frac))

    def _save_scene_bookmark(self):
        payload = self._current_scene_bookmark_payload()
        if payload is None:
            return
        chapter, packed, image_index, offset_frac = payload
        title = "Save Bookmark" if self._chapter_mode == "text" else "Save Scene"
        prompt = "Optional note for this bookmark:" if self._chapter_mode == "text" else "Optional note for this scene:"
        note, accepted = QInputDialog.getText(self, title, prompt)
        if not accepted:
            return
        thumbnail_path = ""
        if self._chapter_mode == "image":
            thumbnail_path = self._save_scene_thumbnail(image_index, offset_frac)
        self.scene_bookmark_store.save(
            self.webtoon.name,
            chapter,
            packed,
            image_index + 1 if self._chapter_mode == "image" else 0,
            note,
            thumbnail_path=thumbnail_path,
        )
        self._refresh_scene_marks()
        self.setFocus()

    def _save_scene_thumbnail(self, index: int, offset_frac: float) -> str:
        if index < 0 or index >= len(self.image_labels):
            return ""
        label = self.image_labels[index]
        pixmap = getattr(label, '_source_pixmap', None)
        if pixmap is None or pixmap.isNull():
            pixmap = self._load_scene_thumbnail_pixmap(getattr(label, 'img_path', ''))
        if pixmap is None or pixmap.isNull():
            return ""

        source = pixmap
        viewport_h = max(1, self.scroll.viewport().height())
        display_h = max(1, self._label_heights[index] if index < len(self._label_heights) else self._scaled_label_height(label))
        visible_ratio = min(1.0, viewport_h / display_h)
        crop_h = max(120, min(source.height(), int(source.height() * visible_ratio)))
        top_frac = max(0.0, min(1.0, offset_frac))
        center_frac = min(1.0, top_frac + (visible_ratio / 2.0))
        center_y = int(center_frac * source.height())
        top = max(0, min(source.height() - crop_h, center_y - (crop_h // 2)))
        cropped = source.copy(0, top, source.width(), crop_h)
        scaled = cropped.scaled(160, 160, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)

        thumb_dir = data_path("scene_bookmarks")
        thumb_dir.mkdir(parents=True, exist_ok=True)
        chapter = self.webtoon.chapters[self.current_chapter_index] if self.webtoon else "chapter"
        thumb_path = thumb_dir / self._scene_thumbnail_name(chapter, index)
        if not scaled.save(str(thumb_path), "JPEG", 88):
            return ""
        return str(thumb_path)

    def _load_scene_thumbnail_pixmap(self, image_path: str) -> QPixmap | None:
        image_path = str(image_path or "").strip()
        if not image_path:
            return None
        reader = QImageReader(image_path)
        size = reader.size()
        if size.isValid() and size.width() > 0 and size.height() > 0:
            target = size.scaled(420, 420, Qt.KeepAspectRatio)
            reader.setScaledSize(target)
        pixmap = QPixmap.fromImageReader(reader)
        if pixmap.isNull():
            return None
        return pixmap

    def _scene_thumbnail_name(self, chapter: str, index: int) -> str:
        return f"{self._safe_scene_name(self.webtoon.name if self.webtoon else 'webtoon')}_{self._safe_scene_name(chapter)}_{index + 1}_{int(time.time() * 1000)}.jpg"

    @staticmethod
    def _safe_scene_name(value: str) -> str:
        cleaned = "".join(ch if ch.isalnum() or ch in {'-', '_'} else '_' for ch in str(value or ""))
        cleaned = cleaned.strip('_')
        return cleaned or "scene"

    def _open_scene_bookmarks(self):
        if not self.webtoon:
            return
        if not (0 <= self.current_chapter_index < len(self.webtoon.chapters)):
            return
        chapter = self.webtoon.chapters[self.current_chapter_index]
        if self._chapter_mode == "text":
            dialog = AllSceneBookmarksDialog(
                self.webtoon,
                self.scene_bookmark_store,
                self._jump_to_saved_scene,
                parent=self,
                mode_label="Bookmark",
            )
        else:
            dialog = SceneBookmarksDialog(
                self.webtoon,
                chapter,
                self.scene_bookmark_store,
                lambda packed: self._jump_to_saved_scene(chapter, packed),
                parent=self,
                mode_label="Scene",
            )
        dialog.exec()
        self._refresh_scene_marks()
        self.setFocus()

    def _jump_to_saved_scene(self, chapter: str, packed: float):
        if not self.webtoon:
            return
        if self._chapter_mode == "text":
            self._jump_to_text_bookmark(chapter, packed)
            return
        current_chapter = self.webtoon.chapters[self.current_chapter_index]
        if chapter != current_chapter:
            return
        self._unpack_restore(float(packed))
        self._apply_restore()
        if self._restore_image_index is not None:
            self._progress_save_timer.start()

    def _jump_to_text_bookmark(self, chapter: str, packed: float):
        if not self.webtoon:
            return
        try:
            chapter_index = self.webtoon.chapters.index(chapter)
        except ValueError:
            return
        progress = max(0.0, min(1.0, float(packed or 0.0)))
        if any(int(segment.get("index") or -1) == chapter_index for segment in self._text_loaded_segments):
            self._jump_to_text_segment_progress(chapter_index, progress)
            return
        self._pending_text_bookmark = (chapter_index, progress)
        self._clear_pending_restore()
        self._load_chapter_no_prompt(chapter_index)

    def _jump_to_text_segment_progress(self, chapter_index: int, progress: float):
        self._rebuild_text_segment_bounds()
        progress = max(0.0, min(1.0, float(progress or 0.0)))
        for bound in self._text_segment_bounds:
            if int(bound.get("index") or -1) != int(chapter_index):
                continue
            span = max(1, int(bound["end"]) - int(bound["start"]))
            target = int(bound["start"]) + int(span * progress)
            bar = self.scroll.verticalScrollBar()
            bar.setValue(max(0, min(target, bar.maximum())))
            self._sync_text_active_chapter(target, force=True)
            self._update_text_progress_indicator()
            self._progress_save_timer.start()
            return

    def _unpack_restore(self, packed: float):
        self._restore_text_scroll = max(0.0, min(1.0, float(packed or 0.0)))
        if packed < 0.005:
            self._restore_image_index = None
            self._restore_image_offset = 0.0
        else:
            self._restore_image_index = int(packed)
            self._restore_image_offset = packed - int(packed)

    def _clear_pending_restore(self):
        self._restore_image_index = None
        self._restore_image_offset = 0.0
        self._restore_text_scroll = 0.0

    def _mark_manual_navigation(self) -> None:
        self._manual_navigation_since_chapter_open = True
        self._resize_packed = None
        self._resize_anchor_px = 0

    def _on_scrollbar_action_triggered(self, _action: int) -> None:
        # Some key presses can be handled by the focused scroll area or child widget
        # instead of ViewerPage.keyPressEvent(). Clear pending restore there too so
        # a late image restore cannot snap the user back to an older position.
        if self._restore_image_index is not None and not self._applying_restore:
            self._clear_pending_restore()

    def _on_scrollbar_value_changed(self, value: int) -> None:
        try:
            bar = self.scroll.verticalScrollBar()
            logger.info(
                "VIEWER DEBUG scrollbar_changed value=%d max=%d applying_restore=%s restore_idx=%s manual_nav=%s chapter_mode=%s loaded=%d/%d",
                int(value),
                int(bar.maximum()),
                self._applying_restore,
                self._restore_image_index,
                self._manual_navigation_since_chapter_open,
                self._chapter_mode,
                int(self._chapter_load_loaded),
                int(self._chapter_load_total),
            )
        except Exception:
            logger.exception("VIEWER DEBUG failed to log scrollbar change")
        if self._chapter_mode == "image" and not self._applying_restore and int(value) > 0:
            self._mark_manual_navigation()
        # Startup restore can finish after the user has already scrolled if the
        # key event was handled by the scroll area instead of the viewer widget.
        # Once the viewport has moved away from the top under user control, let
        # manual navigation win and cancel the deferred restore.
        if (
            self._chapter_mode == "image"
            and self._restore_image_index is not None
            and not self._applying_restore
            and int(value) > 0
        ):
            self._clear_pending_restore()

    def _apply_restore(self):
        idx = self._restore_image_index
        logger.info(
            "VIEWER DEBUG apply_restore idx=%s offset=%.4f image_count=%d chapter_mode=%s",
            idx,
            float(self._restore_image_offset),
            len(self.image_labels),
            self._chapter_mode,
        )
        if idx is None or idx >= len(self.image_labels):
            return
        if self._is_manga_image_mode():
            self._applying_restore = True
            try:
                if self._jump_to_packed(idx, self._restore_image_offset):
                    self._clear_pending_restore()
            finally:
                self._applying_restore = False
            return
        for i in range(idx + 1):
            lbl = self.image_labels[i]
            if lbl.pixmap() is None or lbl.pixmap().isNull():
                return
        self._applying_restore = True
        try:
            if self._jump_to_packed(idx, self._restore_image_offset):
                self._clear_pending_restore()
        finally:
            self._applying_restore = False

    def _jump_to_packed(self, idx: int, offset_frac: float, anchor_px: int = 0) -> bool:
        if self._is_manga_image_mode():
            return self._set_manga_page(idx, offset_frac)
        cumulative = self.cumulative_height_before(idx)
        height = self._label_heights[idx] if idx < len(self._label_heights) else self._scaled_label_height(self.image_labels[idx])
        target_px = cumulative + int(height * offset_frac) - max(0, anchor_px)

        bar = self.scroll.verticalScrollBar()
        logger.info(
            "VIEWER DEBUG jump_to_packed idx=%d offset=%.4f anchor_px=%d cumulative=%d height=%d before=%d target=%d max=%d",
            int(idx),
            float(offset_frac),
            int(anchor_px),
            int(cumulative),
            int(height),
            int(bar.value()),
            int(target_px),
            int(bar.maximum()),
        )
        bar.setValue(max(0, min(target_px, bar.maximum())))
        logger.info(
            "VIEWER DEBUG jump_to_packed_applied idx=%d after=%d",
            int(idx),
            int(bar.value()),
        )

        return not (bar.value() < target_px - 5)

    def _capture_layout_anchor(self, changed_indexes: list[int]) -> float | None:
        if (
            not changed_indexes
            or not self.image_labels
            or self._chapter_mode == "image"
            or self._is_manga_image_mode()
            or self._restore_image_index is not None
            or self._applying_restore
            or self._resize_packed is not None
            or self._manual_navigation_since_chapter_open
        ):
            return None

        current_index = self.image_index_at_offset(self.scroll.verticalScrollBar().value())
        if min(changed_indexes) > current_index:
            return None
        return self._current_packed_position()

    def _restore_layout_anchor(self, packed: float | None) -> None:
        logger.info(
            "VIEWER DEBUG restore_layout_anchor packed=%s image_count=%d chapter_mode=%s manual_nav=%s",
            packed,
            len(self.image_labels),
            self._chapter_mode,
            self._manual_navigation_since_chapter_open,
        )
        if (
            packed is None
            or not self.image_labels
            or self._chapter_mode == "image"
            or self._manual_navigation_since_chapter_open
        ):
            return
        idx = max(0, min(len(self.image_labels) - 1, int(packed)))
        self._jump_to_packed(idx, packed - int(packed))

    def _on_image_ready(self, index: int, path: str, pixmap: QPixmap):
        if index >= len(self.image_labels):
            return
        label = self.image_labels[index]
        if str(getattr(label, "img_path", "") or "") != str(path or ""):
            logger.info(
                "Ignoring stale viewer image load index=%d expected=%s got=%s",
                index,
                getattr(label, "img_path", ""),
                path,
            )
            return
        self._chapter_load_loaded += 1
        self._update_loading_overlay()

        # Only do one immediate paint per chapter load.
        # Everything else should go through the batch path so restore logic runs.
        if not self._did_immediate_first_paint and not self._pending_batch:
            label._source_pixmap = pixmap
            label._natural_width = pixmap.width()
            label._natural_height = pixmap.height()
            self._apply_pixmap_to_label(label)
            self._set_label_height_cache(index, label.height())

            self._did_immediate_first_paint = True

            self.preview.notify_image_loaded()
            self.manga_preview.notify_image_loaded()
            if self._is_manga_image_mode():
                self._update_manga_spread_label()
            self.check_visible_images()
            self._panel_warm_timer.start()
            self._hide_loading_overlay()

            # Restore might already be possible for very small saved positions.
            self._apply_restore()
            return

        self._pending_batch[index] = pixmap
        if not self._batch_timer.isActive():
            self._batch_timer.start()

    def _flush_batch(self):
        if not self._pending_batch:
            return

        anchor = self._capture_layout_anchor(list(self._pending_batch.keys()))
        logger.info(
            "VIEWER DEBUG flush_batch batch_indexes=%s anchor=%s restore_idx=%s manual_nav=%s loaded=%d/%d",
            sorted(self._pending_batch.keys()),
            anchor,
            self._restore_image_index,
            self._manual_navigation_since_chapter_open,
            int(self._chapter_load_loaded),
            int(self._chapter_load_total),
        )
        self.container.setUpdatesEnabled(False)
        try:
            needs_restore_check = False
            restore_idx = self._restore_image_index

            for index, pixmap in self._pending_batch.items():
                if index >= len(self.image_labels):
                    continue
                label = self.image_labels[index]
                label._source_pixmap = pixmap
                label._natural_width = pixmap.width()
                label._natural_height = pixmap.height()
                self._apply_pixmap_to_label(label)
                self._set_label_height_cache(index, label.height())
                if restore_idx is not None and index <= restore_idx:
                    needs_restore_check = True
        finally:
            self._pending_batch.clear()
            self.container.setUpdatesEnabled(True)

        self.preview.notify_image_loaded()
        self.manga_preview.notify_image_loaded()
        if self._is_manga_image_mode():
            self._update_manga_spread_label()
        self._invalidate_panel_cache()
        self.check_visible_images()
        self._panel_warm_timer.start()
        self._restore_layout_anchor(anchor)

        if self._resize_packed is not None:
            idx = int(self._resize_packed)
            frac = self._resize_packed - idx
            if (
                not self._manual_navigation_since_chapter_open
                and self._resize_packed is not None
                and self.image_labels
                and idx < len(self.image_labels)
                and getattr(self.image_labels[idx], '_source_pixmap', None) is not None
            ):
                self._jump_to_packed(idx, frac, self._resize_anchor_px)
                self._resize_packed = None

        if needs_restore_check:
            self._apply_restore()

    def load_selected_chapter(self, index):
        # If skip_specials is on, the combo index must be translated to a real chapter index
        if self._chapter_index_map and index < len(self._chapter_index_map):
            real_index = self._chapter_index_map[index]
        else:
            real_index = index
        self._open_chapter_from_viewer(real_index)

    def _should_prompt_for_chapter(self, index: int) -> bool:
        if not self.webtoon or index < 0 or index >= len(self.webtoon.chapters):
            return False
        chapter = self.webtoon.chapters[index]
        progress_map = self.progress_store.get_progress_map(self.webtoon.name)
        saved_scroll, total_images = progress_map.get(chapter, (0.0, 0))
        saved_scroll = float(saved_scroll or 0.0)
        total_images = int(total_images or 0)
        if saved_scroll <= 0.005:
            return False
        if total_images > 0 and saved_scroll >= float(total_images):
            return False
        return True

    def _open_chapter_from_viewer(self, index: int) -> bool:
        if self._should_prompt_for_chapter(index):
            return self._load_chapter_with_prompt(index)
        self._load_chapter_no_prompt(index)
        return True

    def _load_chapter_with_prompt(self, index):
        if not self.webtoon:
            return False
        chapter = self.webtoon.chapters[index]
        logger.info("Viewer loading chapter with prompt: %s / %s", self.webtoon.name, chapter)

        saved_scroll = self.progress_store.get_for_chapter(self.webtoon.name, chapter)
        packed = 0.0
        if saved_scroll > 0.005:
            dlg = ContinueDialog(chapter, parent=self)
            if dlg.exec() != QDialog.Accepted:
                logger.info("Resume dialog cancelled for %s / %s", self.webtoon.name, chapter)
                return False
            if dlg.choice == "continue":
                packed = saved_scroll
                logger.info("Resume dialog chose continue for %s / %s", self.webtoon.name, chapter)
            elif dlg.choice != "restart":
                return False
            else:
                logger.info("Resume dialog chose restart for %s / %s", self.webtoon.name, chapter)

        self._unpack_restore(packed)
        self._load_chapter_no_prompt(index)
        return True

    def _load_chapter_no_prompt(self, index):
        if not self.webtoon:
            return
        self._progress_save_timer.stop()
        self._manual_navigation_since_chapter_open = False
        self.current_chapter_index = index
        chapter = self.webtoon.chapters[index]
        logger.info("Viewer loading chapter without prompt: %s / %s", self.webtoon.name, chapter)

        self.chapter_selector.blockSignals(True)
        if self._chapter_index_map:
            # Find the selector position for this real index
            selector_pos = next(
                (i for i, real in enumerate(self._chapter_index_map) if real == index),
                None
            )
            if selector_pos is not None:
                self.chapter_selector.setCurrentIndex(selector_pos)
        else:
            self.chapter_selector.setCurrentIndex(index)
        self.chapter_selector.blockSignals(False)

        chapter_path = os.path.join(self.webtoon.path, chapter)
        if self._has_text_chapter_content(chapter_path):
            self._load_text_chapter(chapter, chapter_path)
        else:
            self._load_chapter_images(chapter)
        self._refresh_scene_marks()
        self.update_nav_buttons()

    def _sync_chapter_selector_visibility(self) -> None:
        selector = getattr(self, "chapter_selector", None)
        if selector is None:
            return
        selector.setVisible(selector.count() > 1)

        if self._chapter_mode == "image" and self._restore_image_index is None:
            if self._is_manga_image_mode():
                self._set_manga_page(0, 0.0)
            else:
                self.scroll.verticalScrollBar().setValue(0)

    def clear_images(self):
        self._batch_timer.stop()
        self._panel_warm_timer.stop()
        self._preview_timer.stop()
        self._pending_batch.clear()
        self._pending_preview_queue.clear()
        self._queued_preview_indexes.clear()
        self._did_immediate_first_paint = False

        self.loader.cancel()
        self.loader.reset()
        while self.image_layout.count():
            item = self.image_layout.takeAt(0)
            widget = item.widget()
            if widget is None:
                continue
            if widget is self.manga_spread_label:
                widget.clear()
                widget.hide()
                continue
            widget.clear()
            widget.hide()
            widget._source_pixmap = None
            widget._preview_pixmap = None
            widget._natural_width = 0
            widget._natural_height = 0
            widget._file_size = 0
            widget.img_path = ""
            self._label_pool.append(widget)
        self.image_labels = []
        self._manga_page_index = 0
        self._reset_layout_metrics()
        self.manga_spread_label.clear()
        self.manga_spread_label.hide()
        self.image_layout.addWidget(self.manga_spread_label, 0, Qt.AlignHCenter)

        self.preview.set_image_labels([])
        self.manga_preview.set_image_labels([])
        self.preview.set_scene_marks([])
        self.manga_preview.set_scene_marks([])
        self._chapter_scene_marks = []
        self.scene_list_btn.setToolTip("No saved scenes for this chapter")
        self.scene_list_btn.setEnabled(False)
        self.save_scene_btn.setEnabled(False)
        self._update_session_overlay()

        self._panel_ranges = []
        self._panel_ranges_dirty = True
        self._panel_build_generation += 1
        self._panel_build_inflight = False

        self.text_title_label.clear()
        self.text_progress_label.setText("0% read")
        self.text_progress_bar.setValue(0)
        self.text_side_progress_percent.setText("0%")
        self._update_text_side_progress_fill(0)
        self.text_content_label.clear()
        self._text_loaded_segments = []
        self._text_segment_bounds = []

    def _set_chapter_mode(self, mode: str):
        mode = "text" if str(mode).strip().casefold() == "text" else "image"
        self._chapter_mode = mode
        target = self.text_container if mode == "text" else self.container
        if self.scroll.widget() is not target:
            self.scroll.takeWidget()
            self.scroll.setWidget(target)
        self._apply_reader_session_state(persist=False)

    def _has_text_chapter_content(self, chapter_path: str) -> bool:
        for filename in ("chapter.json", "chapter.html", "chapter.txt"):
            if os.path.isfile(os.path.join(chapter_path, filename)):
                return True
        return False

    def _load_text_chapter(self, chapter: str, chapter_path: str):
        self.clear_images()
        self._set_chapter_mode("text")
        segment = self._build_text_segment(self.current_chapter_index)
        if segment is None:
            logger.warning("Viewer text chapter is empty: %s", chapter_path)
            QMessageBox.information(self, "Chapter empty", f"'{chapter}' has no readable text.")
            return
        self._text_loaded_segments = [segment]
        self._render_text_segments()
        self._apply_text_reader_style()
        self._sync_text_active_chapter(0.0, force=True)
        self._update_text_progress_indicator()
        self._update_session_overlay()
        self.setFocus()
        if self._pending_text_bookmark and int(self._pending_text_bookmark[0]) == int(self.current_chapter_index):
            pending_index, pending_progress = self._pending_text_bookmark
            self._pending_text_bookmark = None
            QTimer.singleShot(0, lambda idx=pending_index, prog=pending_progress: self._jump_to_text_segment_progress(idx, prog))
        QTimer.singleShot(0, self._sync_text_content_height)
        QTimer.singleShot(0, self._update_text_progress_indicator)
        QTimer.singleShot(0, self._apply_text_restore)

    def _build_text_segment(self, chapter_index: int) -> dict | None:
        if not self.webtoon or chapter_index < 0 or chapter_index >= len(self.webtoon.chapters):
            return None
        chapter = self.webtoon.chapters[chapter_index]
        chapter_path = os.path.join(self.webtoon.path, chapter)
        payload = self._read_text_chapter_payload(chapter_path)
        title = str(chapter or "").strip() or chapter
        html_body = str(payload.get("html") or "").strip()
        text_body = str(payload.get("text") or "").strip()
        if not html_body:
            paragraphs = [
                f"<p>{self._escape_html(line)}</p>"
                for line in text_body.splitlines()
                if line.strip()
            ]
            html_body = "\n".join(paragraphs)
        if not html_body:
            return None
        return {
            "index": int(chapter_index),
            "chapter": chapter,
            "title": title,
            "html_body": html_body,
        }

    def _render_text_segments(self):
        if not self._text_loaded_segments:
            self.text_title_label.clear()
            self.text_content_label.clear()
            self._text_segment_bounds = []
            return
        self.text_title_label.setText(self._render_text_chapter_title(str(self._text_loaded_segments[0]["title"])))
        self.text_content_label.setHtml(self._render_continuous_text_document())
        self._sync_text_content_height()
        self._rebuild_text_segment_bounds()

    def _render_continuous_text_document(self) -> str:
        parts = ["<div style='max-width:860px;margin:0 auto;'>", self._text_document_style_block()]
        for pos, segment in enumerate(self._text_loaded_segments):
            parts.append(self._render_text_segment_fragment(segment, include_break=(pos > 0)))
        parts.append("</div>")
        return "".join(parts)

    def _text_document_style_block(self) -> str:
        text_color = self._escape_html(self._text_color)
        return (
            "<style>"
            f"p{{margin:0 0 1.1em 0;color:{text_color};}}"
            f"h1,h2,h3{{color:{text_color};line-height:1.25;margin:0 0 0.7em 0;}}"
            "blockquote{margin:1.2em 0;padding:0.8em 1em;border-left:3px solid rgba(255,255,255,0.18);background:rgba(255,255,255,0.03);}"
            "hr{border:none;border-top:1px solid rgba(255,255,255,0.12);margin:1.8em 0 1.2em 0;}"
            ".chapter-bridge{padding:0.6em 0 0.8em 0;}"
            f".chapter-bridge h2{{margin:0;color:{text_color};font-size:24px;font-weight:700;}}"
            "</style>"
        )

    def _render_text_segment_fragment(self, segment: dict, *, include_break: bool) -> str:
        title_html = self._escape_html(str(segment.get("title") or segment.get("chapter") or "Chapter"))
        body_html = str(segment.get("html_body") or "")
        if include_break:
            return (
                "<hr>"
                "<div class='chapter-bridge'>"
                f"<h2>{title_html}</h2>"
                "</div>"
                f"{body_html}"
            )
        return body_html

    def _estimate_text_segment_height(self, segment: dict, *, include_break: bool, width: int) -> int:
        doc = QTextDocument()
        doc.setDefaultStyleSheet("")
        doc.setHtml(
            "<div style='max-width:860px;margin:0 auto;'>"
            + self._text_document_style_block()
            + self._render_text_segment_fragment(segment, include_break=include_break)
            + "</div>"
        )
        doc.setTextWidth(max(200, width))
        return max(1, int(doc.size().height()))

    def _rebuild_text_segment_bounds(self):
        width = max(200, self.text_content_label.viewport().width())
        bounds = []
        current = 0
        for pos, segment in enumerate(self._text_loaded_segments):
            height = self._estimate_text_segment_height(segment, include_break=(pos > 0), width=width)
            start = current
            end = current + height
            bounds.append({
                "index": int(segment["index"]),
                "chapter": segment["chapter"],
                "title": segment["title"],
                "start": start,
                "end": end,
            })
            current = end
        self._text_segment_bounds = bounds

    def _active_text_segment(self, scroll_value: int | float | None = None) -> dict | None:
        if not self._text_segment_bounds:
            return None
        bar = self.scroll.verticalScrollBar()
        position = int(bar.value() if scroll_value is None else scroll_value)
        for bound in self._text_segment_bounds:
            if position < int(bound["end"]):
                span = max(1, int(bound["end"]) - int(bound["start"]))
                progress = max(0.0, min(1.0, (position - int(bound["start"])) / span))
                active = dict(bound)
                active["progress"] = progress
                return active
        bound = dict(self._text_segment_bounds[-1])
        bound["progress"] = 1.0
        return bound

    def _sync_text_active_chapter(self, scroll_value: int | float | None = None, *, force: bool = False):
        active = self._active_text_segment(scroll_value)
        if active is None:
            return
        active_index = int(active["index"])
        if not force and active_index == self.current_chapter_index:
            return
        self.current_chapter_index = active_index
        self.text_title_label.setText(self._render_text_chapter_title(str(active["title"])))
        self.chapter_selector.blockSignals(True)
        if self._chapter_index_map:
            selector_pos = next((i for i, real in enumerate(self._chapter_index_map) if real == active_index), None)
            if selector_pos is not None:
                self.chapter_selector.setCurrentIndex(selector_pos)
        else:
            self.chapter_selector.setCurrentIndex(active_index)
        self.chapter_selector.blockSignals(False)
        self.update_nav_buttons()

    def _maybe_append_next_text_segment(self):
        if self._chapter_mode != "text" or not self.webtoon or not self._text_loaded_segments:
            return False
        active = self._active_text_segment(self.scroll.verticalScrollBar().value())
        if active is None:
            return False
        last_index = int(self._text_loaded_segments[-1]["index"])
        if int(active["index"]) != last_index or float(active.get("progress") or 0.0) < self._text_append_threshold:
            return False
        next_index = self._next_chapter_index(last_index)
        if next_index is None or any(int(seg["index"]) == next_index for seg in self._text_loaded_segments):
            return False
        segment = self._build_text_segment(next_index)
        if segment is None:
            return False
        self._text_loaded_segments.append(segment)
        return True

    def _maybe_prepend_previous_text_segment(self):
        if self._chapter_mode != "text" or not self.webtoon or not self._text_loaded_segments:
            return False
        active = self._active_text_segment(self.scroll.verticalScrollBar().value())
        if active is None:
            return False
        first_index = int(self._text_loaded_segments[0]["index"])
        if int(active["index"]) != first_index or float(active.get("progress") or 0.0) > self._text_prepend_threshold:
            return False
        prev_index = self._prev_chapter_index(first_index)
        if prev_index is None or any(int(seg["index"]) == prev_index for seg in self._text_loaded_segments):
            return False
        segment = self._build_text_segment(prev_index)
        if segment is None:
            return False
        self._text_loaded_segments.insert(0, segment)
        return True

    def _ensure_text_segment_window(self):
        if self._chapter_mode != "text" or not self._text_loaded_segments:
            return
        bar = self.scroll.verticalScrollBar()
        active = self._active_text_segment(bar.value())
        if active is None:
            return
        active_index = int(active["index"])
        previous_bounds = {int(bound["index"]): dict(bound) for bound in self._text_segment_bounds}
        previous_offset = 0
        if active_index in previous_bounds:
            previous_offset = max(0, int(bar.value()) - int(previous_bounds[active_index]["start"]))

        changed = self._maybe_prepend_previous_text_segment()
        changed = self._maybe_append_next_text_segment() or changed

        if len(self._text_loaded_segments) > self._text_max_loaded_segments:
            active_pos = next((i for i, seg in enumerate(self._text_loaded_segments) if int(seg["index"]) == active_index), 0)
            keep_start = max(0, active_pos - 1)
            keep_end = min(len(self._text_loaded_segments), keep_start + self._text_max_loaded_segments)
            keep_start = max(0, keep_end - self._text_max_loaded_segments)
            trimmed = self._text_loaded_segments[keep_start:keep_end]
            if len(trimmed) != len(self._text_loaded_segments):
                self._text_loaded_segments = trimmed
                changed = True

        if not changed:
            return

        self._render_text_segments()
        self._rebuild_text_segment_bounds()
        new_bound = next((bound for bound in self._text_segment_bounds if int(bound["index"]) == active_index), None)
        if new_bound is not None:
            target = int(new_bound["start"]) + previous_offset
            QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(max(0, min(target, self.scroll.verticalScrollBar().maximum()))))

    def _read_text_chapter_payload(self, chapter_path: str) -> dict:
        json_path = os.path.join(chapter_path, "chapter.json")
        if os.path.isfile(json_path):
            with open(json_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return data

        html_path = os.path.join(chapter_path, "chapter.html")
        txt_path = os.path.join(chapter_path, "chapter.txt")
        payload = {}
        if os.path.isfile(html_path):
            with open(html_path, "r", encoding="utf-8") as handle:
                html = handle.read()
            soup = BeautifulSoup(html, "html.parser")
            body = soup.body
            payload["html"] = body.decode_contents() if body is not None else html
        if os.path.isfile(txt_path):
            with open(txt_path, "r", encoding="utf-8") as handle:
                payload["text"] = handle.read()
        return payload

    def _apply_text_restore(self):
        if self._chapter_mode != "text":
            return
        bar = self.scroll.verticalScrollBar()
        if bar.maximum() <= 0:
            bar.setValue(0)
            self._update_text_progress_indicator()
            return
        target = int(max(0.0, min(1.0, self._restore_text_scroll)) * bar.maximum())
        bar.setValue(max(0, min(target, bar.maximum())))
        self._update_text_progress_indicator()

    def _update_text_side_progress_fill(self, value: int):
        track = getattr(self, "text_side_progress_track", None)
        fill = getattr(self, "text_side_progress_fill", None)
        if track is None or fill is None:
            return
        track_width = max(1, track.width())
        track_height = max(1, track.height())
        inset = 2
        inner_width = max(1, track_width - (inset * 2))
        inner_height = max(1, track_height - (inset * 2))
        clamped = max(0, min(1000, int(value)))
        fill_height = max(0, int(inner_height * (clamped / 1000.0)))
        if fill_height <= 0:
            fill.setGeometry(inset, inset, inner_width, 0)
            fill.hide()
            return
        fill.setGeometry(inset, inset, inner_width, fill_height)
        fill.show()

    def _update_text_progress_indicator(self):
        if not hasattr(self, "text_progress_bar"):
            return
        visible = self._chapter_mode == "text" and self._text_progress_visible
        self.text_progress_label.setVisible(False)
        self.text_progress_bar.setVisible(False)
        self.text_progress_panel.setVisible(visible)
        if self._chapter_mode != "text":
            return
        self._rebuild_text_segment_bounds()
        self._ensure_text_segment_window()
        self._rebuild_text_segment_bounds()
        bar = self.scroll.verticalScrollBar()
        active = self._active_text_segment(bar.value())
        if active is None:
            progress = 0.0 if bar.maximum() <= 0 else max(0.0, min(1.0, bar.value() / bar.maximum()))
            percent = int(progress * 100)
            value = int(progress * 1000)
        else:
            self._sync_text_active_chapter(bar.value())
            percent = int(float(active["progress"]) * 100)
            value = int(float(active["progress"]) * 1000)
        self.text_progress_label.setText(f"{percent}% read")
        self.text_progress_bar.setValue(value)
        self.text_side_progress_percent.setText(f"{percent}%")
        self._update_text_side_progress_fill(value)

    @staticmethod
    def _escape_html(value: str) -> str:
        return (
            str(value or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def _render_text_chapter_title(self, title: str) -> str:
        return f"<div style='font-size:28px;font-weight:700;color:{self._escape_html(self._text_color)};'>{self._escape_html(title)}</div>"

    def _apply_text_reader_style(self):
        page_color = str(self._text_page_color or "#140e0c")
        text_color = str(self._text_color or "#f6ece5")
        font_size = max(12, min(32, int(self._text_font_size or 18)))
        self.text_title_label.setStyleSheet(
            f"color: {text_color}; font-size: 28px; font-weight: 700; padding: 0 0 4px 0;"
        )
        self.text_content_label.setStyleSheet(
            "QTextBrowser {"
            f"background-color: {page_color};"
            f"color: {text_color};"
            "border: 1px solid rgba(255, 255, 255, 0.07);"
            "border-radius: 20px;"
            "padding: 20px 22px;"
            f"font-size: {font_size}px;"
            "line-height: 1.75;"
            "}"
        )
        self.text_progress_panel.setStyleSheet(
            "background: rgba(14, 12, 11, 0.88); border-left: 1px solid rgba(255, 255, 255, 0.05);"
        )
        self.text_side_progress_percent.setStyleSheet(
            f"color: {text_color}; font-size: 24px; font-weight: 700;"
        )
        self.text_side_progress_label.setStyleSheet(
            f"color: {text_color}; font-size: 11px; font-weight: 700; letter-spacing: 0.18em;"
        )

    def _render_text_chapter_body(self, html_body: str) -> str:
        return (
            "<div style='max-width:860px;margin:0 auto;'>"
            "<style>"
            "p{margin:0 0 1.1em 0;}"
            "h1,h2,h3{color:#fff4ef;line-height:1.25;margin:0 0 0.7em 0;}"
            "blockquote{margin:1.2em 0;padding:0.8em 1em;border-left:3px solid rgba(255,255,255,0.18);background:rgba(255,255,255,0.03);}"
            "hr{border:none;border-top:1px solid rgba(255,255,255,0.12);margin:1.6em 0;}"
            "</style>"
            f"{html_body}"
            "</div>"
        )

    def _sync_text_content_height(self):
        if getattr(self, "_chapter_mode", "image") != "text":
            return
        doc = self.text_content_label.document()
        viewport_width = max(200, self.text_content_label.viewport().width())
        doc.setTextWidth(viewport_width)
        doc_height = int(doc.documentLayout().documentSize().height())
        frame = self.text_content_label.frameWidth() * 2
        target_height = max(220, doc_height + frame + 12)
        self.text_content_label.setMinimumHeight(target_height)
        self.text_content_label.setMaximumHeight(target_height)


    def _show_loading_overlay(self, chapter: str, total_images: int = 0):
        self._chapter_load_total = max(0, int(total_images))
        self._chapter_load_loaded = 0
        self._chapter_loading_active = True
        if self.webtoon is not None:
            self.chapter_loading_started.emit(self.webtoon.name, chapter)
        self.loading_spinner.set_spinning()
        self.loading_label.setText(f"Loading {chapter}...")
        if self._chapter_load_total > 0:
            self.loading_detail_label.setText(f"0 / {self._chapter_load_total} images decoded")
        else:
            self.loading_detail_label.setText("Preparing images...")
        self._position_loading_overlay()
        self.loading_overlay.show()
        self.loading_overlay.raise_()
        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)

    def _update_loading_overlay(self):
        if not self._chapter_loading_active:
            return
        if self._chapter_load_total > 0:
            self.loading_detail_label.setText(
                f"{self._chapter_load_loaded} / {self._chapter_load_total} images decoded"
            )
        else:
            self.loading_detail_label.setText(f"{self._chapter_load_loaded} images decoded")

    def _hide_loading_overlay(self):
        chapter = None
        if self.webtoon is not None and 0 <= self.current_chapter_index < len(self.webtoon.chapters):
            chapter = self.webtoon.chapters[self.current_chapter_index]
        self._chapter_loading_active = False
        self.loading_overlay.hide()
        if self.webtoon is not None and chapter is not None:
            self.chapter_loading_finished.emit(self.webtoon.name, chapter)

    def _position_loading_overlay(self):
        if not hasattr(self, "loading_overlay"):
            return
        self.loading_overlay.setGeometry(self.scroll.viewport().rect())
        self._position_session_overlay()

    def _acquire_image_label(self) -> QLabel:
        if self._label_pool:
            label = self._label_pool.pop()
        else:
            label = QLabel(self.container)
            label.setAlignment(Qt.AlignCenter)
            label.setMinimumHeight(400)
            label.setMouseTracking(True)
            label.installEventFilter(self)
        label.show()
        return label

    def shutdown(self):
        logger.info("Shutting down viewer background workers")
        self._batch_timer.stop()
        self._panel_warm_timer.stop()
        self._preview_timer.stop()
        self._zoom_persist_timer.stop()
        self._hide_loading_overlay()
        self.loader.shutdown()

    def _load_chapter_images(self, chapter):
        self.clear_images()
        self._set_chapter_mode("image")
        self._manual_navigation_since_chapter_open = False
        self._show_loading_overlay(chapter)

        chapter_path = os.path.join(self.webtoon.path, chapter)
        if not os.path.isdir(chapter_path):
            logger.warning("Viewer chapter path missing: %s", chapter_path)
            self._hide_loading_overlay()
            QMessageBox.information(
                self,
                "Chapter missing",
                f"'{chapter}' no longer exists on disk.",
            )
            return

        image_infos = self._get_chapter_image_infos(chapter)

        if not image_infos:
            logger.warning("Viewer chapter has no readable images: %s", chapter_path)
            self._hide_loading_overlay()
            QMessageBox.information(
                self,
                "Chapter empty",
                f"'{chapter}' has no readable images.",
            )
            return

        self._show_loading_overlay(chapter, total_images=len(image_infos))
        for image_index, (img_path, natural_w, natural_h, file_size) in enumerate(image_infos):
            label = self._acquire_image_label()
            label.img_path = img_path
            label._source_pixmap = None
            label._preview_pixmap = None
            label._natural_width = natural_w
            label._natural_height = natural_h
            label._file_size = file_size
            target_width = self._manga_display_page_width(image_index) if self._is_manga_image_mode() else self._image_width()
            if natural_w > 0 and natural_h > 0:
                placeholder_height = max(100, int(target_width * (natural_h / natural_w)))
            else:
                placeholder_height = 400
            label.setFixedHeight(placeholder_height)
            self.image_layout.addWidget(label)
            self.image_labels.append(label)
        self._label_heights = [label.height() for label in self.image_labels]
        self._rebuild_prefix_heights()
        if self._is_manga_image_mode():
            initial_page = self._restore_image_index if self._restore_image_index is not None else 0
            self._manga_page_index = max(0, min(len(self.image_labels) - 1, int(initial_page)))
            self._sync_manga_page_visibility()
        logger.info("Viewer queued %d images for %s / %s", len(self.image_labels), self.webtoon.name, chapter)

        self.preview.set_image_labels(self.image_labels)
        self.manga_preview.set_image_labels(self.image_labels)
        self._sync_manga_page_visibility()

        self.check_visible_images()
        QTimer.singleShot(0, self.check_visible_images)
        QTimer.singleShot(50, self.check_visible_images)

        self._queue_initial_previews()

        if self._restore_image_index is not None:
            self._preload_restore_target()

        self.setFocus()

    def _chapter_cache_entry(self, chapter: str) -> tuple[str, int]:
        chapter_path = os.path.join(self.webtoon.path, chapter)
        try:
            mtime_ns = os.stat(chapter_path).st_mtime_ns
        except OSError:
            mtime_ns = -1
        return chapter_path, mtime_ns

    def _get_chapter_image_paths(self, chapter: str) -> list[str]:
        chapter_path, mtime_ns = self._chapter_cache_entry(chapter)
        cached = self._chapter_image_cache.get(chapter_path)
        if cached is not None and cached[0] == mtime_ns:
            return list(cached[1])

        image_paths = sorted(
            entry.path
            for entry in os.scandir(chapter_path)
            if entry.is_file() and entry.name.lower().endswith(SUPPORTED_VIEWER_EXTENSIONS)
        )
        self._chapter_image_cache[chapter_path] = (mtime_ns, image_paths)
        return list(image_paths)

    def _get_chapter_image_infos(self, chapter: str) -> list[tuple[str, int, int, int]]:
        chapter_path, mtime_ns = self._chapter_cache_entry(chapter)
        cached = self._chapter_image_info_cache.get(chapter_path)
        if cached is not None and cached[0] == mtime_ns:
            return list(cached[1])

        infos = []
        for index, path in enumerate(self._get_chapter_image_paths(chapter), start=1):
            reader = QImageReader(path)
            size = reader.size()
            if not size.isValid():
                size = QSize(0, 0)
            try:
                file_size = os.path.getsize(path)
            except OSError:
                file_size = 0
            infos.append((path, max(0, size.width()), max(0, size.height()), file_size))
            if index % 8 == 0 and self._chapter_loading_active:
                QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)

        self._chapter_image_info_cache[chapter_path] = (mtime_ns, infos)
        return list(infos)

    def _queue_preview_index(self, index: int):
        if index < 0 or index >= len(self.image_labels) or index in self._queued_preview_indexes:
            return
        self._queued_preview_indexes.add(index)
        preview_w = max(50, PAGE_COLUMN_W - 8) if self._is_manga_image_mode() else 50
        self.loader.load_preview(index, self.image_labels[index].img_path, max_w=preview_w)

    def _queue_initial_previews(self):
        eager_count = min(len(self.image_labels), PREVIEW_EAGER_COUNT)
        for index in range(eager_count):
            self._queue_preview_index(index)

        self._pending_preview_queue = list(range(eager_count, len(self.image_labels)))
        if self._pending_preview_queue:
            self._preview_timer.start()

    def _drain_preview_queue(self):
        if not self._pending_preview_queue:
            return

        batch = self._pending_preview_queue[:PREVIEW_BATCH_SIZE]
        del self._pending_preview_queue[:PREVIEW_BATCH_SIZE]

        for index in batch:
            self._queue_preview_index(index)

        if self._pending_preview_queue:
            self._preview_timer.start()

    def _preload_restore_target(self):
        idx = self._restore_image_index
        if idx is None or idx >= len(self.image_labels):
            return
        visible_indexes = self._manga_visible_indexes(idx) if self._is_manga_image_mode() else [idx]
        end = min(len(self.image_labels), (max(visible_indexes) if visible_indexes else idx) + 3)
        for i in range(end):
            self.loader.load(i, self.image_labels[i].img_path, 0)

    def check_visible_images(self):
        if not self.image_labels:
            return
        if self._is_manga_image_mode():
            visible_indexes = self._manga_visible_indexes()
            start_index = max(0, min(visible_indexes) - 1) if visible_indexes else 0
            end_index = min(len(self.image_labels) - 1, max(visible_indexes) + 1) if visible_indexes else 0
        else:
            viewport_top = self.scroll.verticalScrollBar().value()
            viewport_bottom = viewport_top + self.scroll.viewport().height()
            start_index = self.image_index_at_offset(max(0, viewport_top - LAZY_WINDOW))
            end_index = min(
                len(self.image_labels) - 1,
                self.image_index_at_offset(viewport_bottom + LAZY_WINDOW),
            )

        for i in range(start_index, end_index + 1):
            label = self.image_labels[i]
            if getattr(label, '_source_pixmap', None) is not None:
                self._queue_preview_index(i)
                continue

            self._queue_preview_index(i)
            self.loader.load(i, label.img_path, 0)

    def _zoom_out(self):
        self._set_zoom(self._zoom - 0.05)
        self._schedule_zoom_override_persist()

    def _zoom_in(self):
        self._set_zoom(self._zoom + 0.05)
        self._schedule_zoom_override_persist()

    def _on_zoom_slider(self, value: int):
        self._set_zoom(value / 100.0, update_slider=False)
        self._schedule_zoom_override_persist()

    def _schedule_zoom_override_persist(self):
        if not self.webtoon:
            return
        self._zoom_override_active = True
        self._zoom_reset_btn.setEnabled(True)
        self._zoom_persist_timer.start()

    def _persist_zoom_override_now(self):
        """Save current zoom as a per-webtoon override after user interaction settles."""
        if not self.webtoon:
            return
        logger.info("Persisting viewer zoom override for %s to %.2f", self.webtoon.name, self._zoom)
        self.settings_store.set_zoom_override(self.webtoon.name, self._zoom)
        self._zoom_override_active = True
        self._zoom_reset_btn.setEnabled(True)

    def _set_zoom(self, zoom: float, update_slider: bool = True, rescale_existing: bool = True):
        previous_zoom = self._zoom
        next_zoom = max(0.15, min(1.0, zoom))
        changed = abs(next_zoom - previous_zoom) > 0.0001
        self._zoom = next_zoom

        self._zoom_slider.blockSignals(True)
        self._zoom_slider.setValue(int(self._zoom * 100))
        self._zoom_slider.blockSignals(False)

        self._zoom_label.setText(f"{int(self._zoom * 100)}%")

        self.preview.set_zoom(self._zoom)
        if rescale_existing and changed and self.image_labels:
            self.rescale_images(previous_zoom)

    def _image_width(self) -> int:
        return max(1, int(self.scroll.viewport().width() * self._zoom))

    def _apply_pixmap_to_label(self, label):
        src = getattr(label, '_source_pixmap', None)
        if src is None or src.isNull():
            return
        try:
            index = self.image_labels.index(label)
        except ValueError:
            index = -1
        target_width = self._manga_display_page_width(index) if self._is_manga_image_mode() and index >= 0 else self._image_width()
        scaled = src.scaledToWidth(target_width, Qt.SmoothTransformation)
        label.setPixmap(scaled)
        label.setFixedHeight(scaled.height())

    def rescale_images(self, previous_zoom: float | None = None):
        # Capture position as a fraction of total content height - this is
        # invariant across rescales, unlike packed position which depends on
        # individual label heights that are about to change.
        bar = self.scroll.verticalScrollBar()
        self._resize_anchor_px = 0
        self._resize_packed = self._packed_position_at(bar.value(), previous_zoom)

        self.container.setUpdatesEnabled(False)
        try:
            for index, label in enumerate(self.image_labels):
                self._apply_pixmap_to_label(label)
                if self._is_manga_image_mode() and getattr(label, "_source_pixmap", None) is None:
                    target_width = self._manga_display_page_width(index)
                    natural_w = getattr(label, "_natural_width", 0)
                    natural_h = getattr(label, "_natural_height", 0)
                    if natural_w > 0 and natural_h > 0:
                        label.setFixedHeight(max(100, int(target_width * (natural_h / natural_w))))
                self._set_label_height_cache(index, label.height())
        finally:
            self.container.setUpdatesEnabled(True)

        # Defer the jump so Qt finishes reflowing label geometry first.
        def _restore():
            if self._resize_packed is None or not self.image_labels or self._manual_navigation_since_chapter_open:
                return
            idx = int(self._resize_packed)
            frac = self._resize_packed - idx
            if idx < len(self.image_labels):
                self._jump_to_packed(idx, frac, self._resize_anchor_px)
                self._resize_packed = None

        QTimer.singleShot(0, _restore)

        self.preview.update()
        if self._is_manga_image_mode():
            self._update_manga_spread_label()
        self._invalidate_panel_cache()
        self._panel_warm_timer.start()

    def next_chapter(self):
        next_idx = self._next_chapter_index(self.current_chapter_index)
        if next_idx is not None:
            logger.info("Viewer moving to next chapter for %s", self.webtoon.name if self.webtoon else "<none>")
            self._progress_save_timer.stop()
            self._save_progress()
            self._restore_image_index = None
            self._open_chapter_from_viewer(next_idx)

    def prev_chapter(self):
        prev_idx = self._prev_chapter_index(self.current_chapter_index)
        if prev_idx is not None:
            logger.info("Viewer moving to previous chapter for %s", self.webtoon.name if self.webtoon else "<none>")
            self._progress_save_timer.stop()
            self._save_progress()
            self._restore_image_index = None
            self._open_chapter_from_viewer(prev_idx)

    def _next_chapter_index(self, from_index: int) -> int | None:
        """Return the next chapter index, skipping specials if the toggle is on."""
        chapters = self.webtoon.chapters
        candidates = range(from_index + 1, len(chapters))
        for i in candidates:
            if not self.skip_specials_enabled or not SPECIAL_CHAPTER_RE.search(chapters[i]):
                return i
        return None

    def _prev_chapter_index(self, from_index: int) -> int | None:
        """Return the previous chapter index, skipping specials if the toggle is on."""
        chapters = self.webtoon.chapters
        candidates = range(from_index - 1, -1, -1)
        for i in candidates:
            if not self.skip_specials_enabled or not SPECIAL_CHAPTER_RE.search(chapters[i]):
                return i
        return None

    def _repopulate_chapter_selector(self):
        """Fill the chapter selector, hiding special chapters when skip_specials is on."""
        if not self.webtoon:
            return
        self.chapter_selector.blockSignals(True)
        self.chapter_selector.clear()
        if self.skip_specials_enabled:
            self._chapter_index_map = [
                i for i, c in enumerate(self.webtoon.chapters)
                if not SPECIAL_CHAPTER_RE.search(c)
            ]
            self.chapter_selector.addItems(
                [self.webtoon.chapters[i] for i in self._chapter_index_map]
            )
        else:
            self._chapter_index_map = []
            self.chapter_selector.addItems(self.webtoon.chapters)
        self.chapter_selector.blockSignals(False)
        self._sync_chapter_selector_visibility()

    def update_nav_buttons(self):
        if not getattr(self, "webtoon", None):
            self.prev_button.setEnabled(False)
            self.next_button.setEnabled(False)
            return
        prev_available = self._prev_chapter_index(self.current_chapter_index) is not None
        next_available = self._next_chapter_index(self.current_chapter_index) is not None
        self.prev_button.setEnabled(prev_available)
        self.next_button.setEnabled(next_available)

    def _clear_zoom_override(self):
        if not self.webtoon:
            return
        self._zoom_persist_timer.stop()
        logger.info("Clearing viewer zoom override for %s", self.webtoon.name)
        self.settings_store.clear_zoom_override(self.webtoon.name)
        self._zoom_override_active = False
        self._zoom_reset_btn.setEnabled(False)
        # Snap back to the global default without saving it as global
        self._set_zoom(load_setting(VIEWER_ZOOM_KEY, 0.5))
        self.setFocus()

    def go_back(self):
        logger.info("Leaving viewer for detail page: %s", self.webtoon.name if self.webtoon else "<none>")
        self._save_progress()
        self.main_window.library.refresh_progress()
        self.main_window.open_detail(self.webtoon, force=True)

    def resizeEvent(self, event):
        self._resize_timer.start()
        self._position_loading_overlay()
        self._position_toolbar()
        if self._chapter_mode == "text":
            QTimer.singleShot(0, self._sync_text_content_height)
            QTimer.singleShot(0, self._update_text_progress_indicator)
        super().resizeEvent(event)

    def _invalidate_panel_cache(self):
        self._panel_ranges_dirty = True
        self._panel_build_generation += 1

    def _warm_panel_cache(self):
        if self._panel_build_inflight:
            return

        payload = []
        any_loaded = False

        for label in self.image_labels:
            src = getattr(label, '_source_pixmap', None)
            h = self._scaled_label_height(label)
            path = getattr(label, 'img_path', None)

            if src is None or src.isNull() or h <= 0 or not path:
                payload.append({"height": h, "path": None})
                continue

            any_loaded = True
            payload.append({"height": h, "path": path})

        if not any_loaded:
            return

        self._panel_build_inflight = True
        generation = self._panel_build_generation
        self.loader.build_panel_ranges(generation, payload)

    def _on_panel_ranges_ready(self, generation: int, ranges: list):
        self._panel_build_inflight = False

        if generation != self._panel_build_generation:
            return

        self._panel_ranges = ranges
        self._panel_ranges_dirty = False

    def _on_preview_ready(self, index: int, path: str, pixmap: QPixmap, natural_w: int, natural_h: int):
        """Thumbnail arrived - store it and set correct label height if not yet loaded."""
        if index >= len(self.image_labels):
            return
        label = self.image_labels[index]
        if str(getattr(label, "img_path", "") or "") != str(path or ""):
            logger.info(
                "Ignoring stale viewer preview load index=%d expected=%s got=%s",
                index,
                getattr(label, "img_path", ""),
                path,
            )
            return
        label._preview_pixmap = pixmap
        label._natural_width = natural_w
        label._natural_height = natural_h
        if getattr(label, '_source_pixmap', None) is None and natural_w > 0 and natural_h > 0:
            anchor = self._capture_layout_anchor([index])
            aspect = natural_h / natural_w
            target_width = self._manga_display_page_width(index) if self._is_manga_image_mode() else self._image_width()
            scaled_h = max(100, int(target_width * aspect))
            label.setFixedHeight(scaled_h)
            self._set_label_height_cache(index, scaled_h)
            self._restore_layout_anchor(anchor)
        self.preview.notify_image_loaded()
        self.manga_preview.notify_image_loaded()
        if self._is_manga_image_mode():
            self._update_manga_spread_label()

    def _get_panel_ranges(self) -> list[tuple[int, int]]:
        if self._panel_ranges_dirty:
            return []
        return self._panel_ranges

    def _total_content_height(self) -> int:
        return self.total_content_height()

    def _get_skip_targets(self) -> list[int]:
        return build_skip_targets(
            self._get_panel_ranges(),
            self._total_content_height(),
            self.scroll.viewport().height(),
            self.scroll.verticalScrollBar().maximum(),
        )

    def _jump_to_target(self, target_y: int):
        bar = self.scroll.verticalScrollBar()
        bar.setValue(max(0, min(target_y, bar.maximum())))

    def _navigate_webtoon_click(self, forward: bool) -> bool:
        if self._chapter_mode != "image" or self._is_manga_image_mode():
            return False

        self._mark_manual_navigation()
        bar = self.scroll.verticalScrollBar()
        view_h = self.scroll.viewport().height()
        pos = bar.value()

        if not self.auto_skip_enabled:
            delta = int(view_h * 0.9)
            if forward:
                bar.setValue(pos + delta)
            else:
                bar.setValue(max(0, pos - delta))
            return True

        targets = self._get_skip_targets()
        if not targets:
            delta = int(view_h * 0.9)
            if forward:
                bar.setValue(pos + delta)
            else:
                bar.setValue(max(0, pos - delta))
            return True

        SNAP = max(32, int(view_h * 0.07))
        MIN_MOVE = max(56, int(view_h * 0.10))

        if forward:
            next_index = bisect_right(targets, pos + SNAP)
            while next_index < len(targets) and (targets[next_index] - pos) < MIN_MOVE:
                next_index += 1
            next_target = targets[next_index] if next_index < len(targets) else None
            if next_target is not None:
                self._jump_to_target(next_target)
            else:
                bar.setValue(pos + int(view_h * 0.9))
            return True

        prev_index = bisect_right(targets, pos - SNAP) - 1
        while prev_index >= 0 and (pos - targets[prev_index]) < MIN_MOVE:
            prev_index -= 1
        prev_target = targets[prev_index] if prev_index >= 0 else None
        if prev_target is not None:
            self._jump_to_target(prev_target)
        else:
            bar.setValue(max(0, pos - int(view_h * 0.9)))
        return True

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()
        manga_mode = self._is_manga_image_mode()
        move_down = ((not manga_mode) and key in (Qt.Key_Down, Qt.Key_J, Qt.Key_PageDown)) or (
            key == Qt.Key_Space and not bool(modifiers & Qt.ShiftModifier)
        )
        move_up = ((not manga_mode) and key in (Qt.Key_Up, Qt.Key_K, Qt.Key_PageUp)) or (
            key == Qt.Key_Space and bool(modifiers & Qt.ShiftModifier)
        )
        forward_key = self._horizontal_forward_key()
        back_key = self._horizontal_back_key()
        page_forward = manga_mode and key in (forward_key, Qt.Key_Down, Qt.Key_J, Qt.Key_PageDown)
        page_back = manga_mode and key in (back_key, Qt.Key_Up, Qt.Key_K, Qt.Key_PageUp)
        chapter_forward = key in ((Qt.Key_BracketRight,) if manga_mode else (Qt.Key_Right, Qt.Key_BracketRight))
        chapter_back = key in ((Qt.Key_BracketLeft,) if manga_mode else (Qt.Key_Left, Qt.Key_BracketLeft))
        session_keys = {
            Qt.Key_M,
            Qt.Key_P,
            Qt.Key_T,
            Qt.Key_A,
            Qt.Key_F,
            Qt.Key_S,
            Qt.Key_G,
            Qt.Key_Home,
            Qt.Key_End,
            Qt.Key_Escape,
        }

        if self._restore_image_index is not None and not self._applying_restore:
            if move_down or move_up or page_forward or page_back or chapter_forward or chapter_back or key in session_keys:
                self._clear_pending_restore()

        bar = self.scroll.verticalScrollBar()
        view_h = self.scroll.viewport().height()
        pos = bar.value()
        if move_down or move_up:
            self._mark_manual_navigation()
            direction_key = Qt.Key_Down if move_down else Qt.Key_Up
            logger.info(
                "VIEWER DEBUG key_nav direction=%s pos=%d view_h=%d auto_skip=%s targets_pending=%s restore_idx=%s loaded=%d/%d",
                "down" if direction_key == Qt.Key_Down else "up",
                int(pos),
                int(view_h),
                self.auto_skip_enabled,
                "unknown",
                self._restore_image_index,
                int(self._chapter_load_loaded),
                int(self._chapter_load_total),
            )

            if self._chapter_mode == "text":
                if direction_key == Qt.Key_Down:
                    bar.setValue(pos + view_h)
                else:
                    bar.setValue(max(0, pos - view_h))
                return

            if not self.auto_skip_enabled:
                if direction_key == Qt.Key_Down:
                    bar.setValue(pos + int(view_h * 0.9))
                else:
                    bar.setValue(max(0, pos - int(view_h * 0.9)))
                return

            targets = self._get_skip_targets()

            if not targets:
                logger.info(
                    "VIEWER DEBUG key_nav_no_targets direction=%s pos=%d step=%d",
                    "down" if direction_key == Qt.Key_Down else "up",
                    int(pos),
                    int(view_h * 0.9),
                )
                if direction_key == Qt.Key_Down:
                    bar.setValue(pos + int(view_h * 0.9))
                else:
                    bar.setValue(max(0, pos - int(view_h * 0.9)))
                return

            SNAP = max(32, int(view_h * 0.07))
            MIN_MOVE = max(56, int(view_h * 0.10))

            if direction_key == Qt.Key_Down:
                if pos <= max(8, SNAP):
                    logger.info(
                        "VIEWER DEBUG key_nav_top_step pos=%d snap=%d step=%d max=%d",
                        int(pos),
                        int(SNAP),
                        int(view_h * 0.9),
                        int(bar.maximum()),
                    )
                    bar.setValue(pos + int(view_h * 0.9))
                    return
                next_index = bisect_right(targets, pos + SNAP)
                while next_index < len(targets) and (targets[next_index] - pos) < MIN_MOVE:
                    next_index += 1

                next_target = targets[next_index] if next_index < len(targets) else None
                logger.info(
                    "VIEWER DEBUG key_nav_targets pos=%d snap=%d min_move=%d next_index=%d target_count=%d next_target=%s first_targets=%s",
                    int(pos),
                    int(SNAP),
                    int(MIN_MOVE),
                    int(next_index),
                    len(targets),
                    next_target,
                    targets[:8],
                )

                if next_target is not None:
                    logger.info(
                        "Viewer auto-skip down pos=%d view_h=%d target=%d",
                        pos,
                        view_h,
                        next_target,
                    )
                    self._jump_to_target(next_target)
                else:
                    logger.info(
                        "Viewer auto-skip down fallback-scroll pos=%d view_h=%d",
                        pos,
                        view_h,
                    )
                    bar.setValue(pos + int(view_h * 0.9))

            else:
                prev_index = bisect_right(targets, pos - SNAP) - 1
                while prev_index >= 0 and (pos - targets[prev_index]) < MIN_MOVE:
                    prev_index -= 1

                prev_target = targets[prev_index] if prev_index >= 0 else None
                if prev_target is not None:
                    self._jump_to_target(prev_target)
                else:
                    bar.setValue(max(0, pos - int(view_h * 0.9)))

        elif page_forward:
            if self._step_manga_page(1):
                self.setFocus()

        elif page_back:
            if self._step_manga_page(-1):
                self.setFocus()

        elif chapter_forward:
            next_idx = self._next_chapter_index(self.current_chapter_index)
            if next_idx is not None:
                self._progress_save_timer.stop()
                self._save_progress()
                self._restore_image_index = None
                if self._open_chapter_from_viewer(next_idx):
                    self.setFocus()

        elif chapter_back:
            prev_idx = self._prev_chapter_index(self.current_chapter_index)
            if prev_idx is not None:
                self._progress_save_timer.stop()
                self._save_progress()
                self._restore_image_index = None
                if self._open_chapter_from_viewer(prev_idx):
                    self.setFocus()

        elif key == Qt.Key_Home:
            bar.setValue(0)

        elif key == Qt.Key_End:
            bar.setValue(bar.maximum())

        elif key == Qt.Key_S:
            self._save_scene_bookmark()

        elif key == Qt.Key_G:
            self._open_scene_bookmarks()

        elif key == Qt.Key_P and self._chapter_mode == "text":
            self._toggle_text_progress(not self._text_progress_visible)

        elif key == Qt.Key_T and self._chapter_mode == "text":
            self._open_text_reader_settings()

        elif key == Qt.Key_T and manga_mode:
            self._open_manga_reader_settings()

        elif key == Qt.Key_M:
            self._toggle_minimap(not self._minimap_visible)

        elif key == Qt.Key_A:
            self._toggle_scene_anchors(not self._scene_anchors_visible)

        elif key == Qt.Key_F:
            self._toggle_focus_mode(not self._focus_mode_enabled)

        elif key == Qt.Key_Escape:
            if self.auto_scroll:
                self._set_auto_scroll_enabled(False)
                self.scroll.viewport().update()
            elif self._focus_mode_enabled:
                self._toggle_focus_mode(False)
            else:
                super().keyPressEvent(event)

        else:
            super().keyPressEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self.setFocus()

    def _build_auto_scroll_cursor(self, direction: int) -> QCursor:
        size = VIEWER_AUTO_SCROLL_CURSOR_SIZE
        center = size // 2
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(VIEWER_AUTO_SCROLL_LINE), 2))
        painter.setBrush(QColor(VIEWER_AUTO_SCROLL_LINE))

        def draw_arrow_up() -> None:
            painter.drawLine(center, 5, center, 13)
            painter.drawLine(center, 5, center - 4, 9)
            painter.drawLine(center, 5, center + 4, 9)

        def draw_arrow_down() -> None:
            painter.drawLine(center, size - 6, center, size - 14)
            painter.drawLine(center, size - 6, center - 4, size - 10)
            painter.drawLine(center, size - 6, center + 4, size - 10)

        if direction <= 0:
            draw_arrow_up()
        if direction >= 0:
            draw_arrow_down()

        painter.end()
        return QCursor(pixmap, center, center)

    def _auto_scroll_cursor_targets(self) -> list[QWidget]:
        targets = []
        scroll_area = getattr(self, "scroll", None)
        viewport = scroll_area.viewport() if isinstance(scroll_area, QScrollArea) else None
        if viewport is not None:
            targets.append(viewport)
        for widget in (
            getattr(self, "container", None),
            getattr(self, "text_container", None),
            getattr(self, "text_title_label", None),
            getattr(self, "text_content_label", None),
            getattr(getattr(self, "text_content_label", None), "viewport", lambda: None)(),
            getattr(self, "preview", None),
        ):
            if isinstance(widget, QWidget) and widget not in targets:
                targets.append(widget)
        return targets

    def _apply_auto_scroll_cursor(self, cursor: QCursor | None) -> None:
        for widget in self._auto_scroll_cursor_targets():
            if cursor is None:
                widget.unsetCursor()
            else:
                widget.setCursor(cursor)

    def _set_auto_scroll_direction(self, direction: int) -> None:
        scroll_area = getattr(self, "scroll", None)
        viewport = scroll_area.viewport() if isinstance(scroll_area, QScrollArea) else None
        if viewport is None or not self.auto_scroll:
            return
        normalized = -1 if direction < 0 else 1 if direction > 0 else 0
        if normalized == self._auto_scroll_direction:
            return
        self._auto_scroll_direction = normalized
        self._apply_auto_scroll_cursor(self._auto_scroll_cursors[normalized])

    def _set_auto_scroll_enabled(self, enabled: bool, *, origin: QPoint | None = None):
        scroll_area = getattr(self, "scroll", None)
        viewport = scroll_area.viewport() if isinstance(scroll_area, QScrollArea) else None
        if viewport is None:
            return
        self.auto_scroll = enabled
        if enabled:
            point = origin if origin is not None else QPoint()
            self.auto_scroll_origin = QPoint(point)
            self.current_mouse_pos = QPoint(point)
            self._auto_scroll_direction = 0
            self._apply_auto_scroll_cursor(self._auto_scroll_cursors[0])
            if not self.scroll_timer.isActive():
                self.scroll_timer.start(16)
        else:
            self._auto_scroll_direction = 0
            self.scroll_timer.stop()
            self._apply_auto_scroll_cursor(None)

    def eventFilter(self, obj, event):
        if not hasattr(self, "top_bar_widget") or not hasattr(self, "_toolbar_hide_timer"):
            return super().eventFilter(obj, event)

        container = getattr(self, "container", None)
        preview = getattr(self, "preview", None)
        scroll_area = getattr(self, "scroll", None)
        viewport = scroll_area.viewport() if isinstance(scroll_area, QScrollArea) else None

        if isinstance(obj, QWidget):
            handles_toolbar_hover = (
                obj in (self, self.top_bar_widget)
                or obj == viewport
                or obj == container
                or obj == preview
                or (viewport is not None and viewport.isAncestorOf(obj))
            )
            if handles_toolbar_hover:
                event_type = event.type()
                if event_type in (QEvent.MouseMove, QEvent.Enter):
                    local_pos = self.mapFromGlobal(obj.mapToGlobal(event.pos())) if hasattr(event, "pos") else QPoint()
                    in_trigger_zone = 0 <= local_pos.y() <= VIEWER_TOOLBAR_TRIGGER_HEIGHT
                    in_toolbar = self.top_bar_widget.geometry().contains(local_pos)
                    if in_trigger_zone or in_toolbar or obj == self.top_bar_widget:
                        self._toolbar_hide_timer.stop()
                        self._set_toolbar_hover_active(True)
                    elif obj != self.top_bar_widget and not in_toolbar and not self._toolbar_popup_open():
                        self._toolbar_hide_timer.start()
                elif event_type == QEvent.Leave and not self._toolbar_popup_open():
                    self._toolbar_hide_timer.start()

        if isinstance(obj, QWidget) and (obj == viewport or obj == container or obj == preview or (viewport is not None and viewport.isAncestorOf(obj))):
            event_type = event.type()
            if event_type == QEvent.MouseButtonPress:
                self.setFocus()

            if (
                self._restore_image_index is not None
                and not self._applying_restore
                and event_type in (QEvent.Wheel, QEvent.MouseButtonPress)
            ):
                self._clear_pending_restore()

            if event_type in (QEvent.MouseButtonPress, QEvent.MouseMove):
                if obj == viewport:
                    event_pos = event.pos()
                else:
                    event_pos = viewport.mapFromGlobal(obj.mapToGlobal(event.pos()))

                if event_type == QEvent.MouseButtonPress and event.button() == Qt.MiddleButton:
                    self._set_auto_scroll_enabled(not self.auto_scroll, origin=event_pos)
                    viewport.update()
                    self.setFocus()
                    return True

                if event_type == QEvent.MouseMove and self.auto_scroll:
                    self.current_mouse_pos = event_pos
                    self._set_auto_scroll_direction(event_pos.y() - self.auto_scroll_origin.y())
                    viewport.update()
                    return True

                if (
                    event_type == QEvent.MouseButtonPress
                    and event.button() == Qt.LeftButton
                    and self.auto_scroll
                ):
                    self._set_auto_scroll_enabled(False)
                    viewport.update()
                    self.setFocus()
                    return True

                if (
                    event_type == QEvent.MouseButtonPress
                    and event.button() == Qt.LeftButton
                    and not self.auto_scroll
                    and obj != preview
                    and self._chapter_mode == "image"
                    and not self._is_manga_image_mode()
                ):
                    navigate_forward = event_pos.y() >= (viewport.height() // 2)
                    if self._navigate_webtoon_click(navigate_forward):
                        self.setFocus()
                        return True

            if event_type in (QEvent.Leave, QEvent.Hide, QEvent.FocusOut) and self.auto_scroll:
                self._set_auto_scroll_enabled(False)

        return super().eventFilter(obj, event)

    def perform_auto_scroll(self):
        dy = self.current_mouse_pos.y() - self.auto_scroll_origin.y()
        DEADZONE = 8
        if abs(dy) <= DEADZONE:
            self._set_auto_scroll_direction(0)
            return
        self._set_auto_scroll_direction(dy)
        speed = ((abs(dy) - DEADZONE) ** 1.4) * (0.08 if dy > 0 else -0.08)
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.value() + int(speed))
        self.setFocus()

    def _toggle_navigation_mode(self):
        self.auto_skip_enabled = self.nav_toggle.isChecked()
        save_setting(VIEWER_AUTO_SKIP_KEY, self.auto_skip_enabled)
        logger.info("Viewer navigation mode changed auto_skip=%s", self.auto_skip_enabled)
        self._apply_reader_session_state()
        self.setFocus()


for _name, _value in list(globals().items()):
    if _name.startswith("_trace_viewer_"):
        continue
    if isinstance(_value, type) and getattr(_value, "__module__", "") == __name__:
        for _attr_name, _attr_value in list(_value.__dict__.items()):
            if _attr_name.startswith("__"):
                continue
            if inspect.isfunction(_attr_value):
                setattr(_value, _attr_name, _trace_viewer_callable(f"{_value.__name__}.{_attr_name}", _attr_value))
    elif inspect.isfunction(_value) and getattr(_value, "__module__", "") == __name__:
        globals()[_name] = _trace_viewer_callable(_name, _value)



