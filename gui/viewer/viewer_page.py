import os
import time
from pathlib import Path
from bisect import bisect_right

import qtawesome as qta
from core.app_logging import get_logger
from core.app_paths import data_path
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QScrollArea,
    QPushButton, QComboBox, QHBoxLayout, QSlider, QMessageBox, QDialog, QInputDialog
)
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QCursor, QImageReader
from PySide6.QtCore import Qt, QPoint, QEvent, QEventLoop, QTimer, Signal, QSize

from gui.common.scene_bookmark_dialog import SceneBookmarksDialog
from gui.common.styles import (
    LOADING_DETAIL_LABEL_STYLE,
    LOADING_TITLE_LABEL_STYLE,
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
    VIEWER_CHROME_VISIBLE_KEY,
    VIEWER_FOCUS_MODE_KEY,
    VIEWER_MINIMAP_VISIBLE_KEY,
    VIEWER_SCENE_ANCHORS_VISIBLE_KEY,
    VIEWER_ZOOM_KEY,
    load_setting,
    save_setting,
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
logger = get_logger(__name__)


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
        self._resize_packed = None
        self._resize_anchor_px = 0
        self._applying_restore = False

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
        self._chrome_visible = bool(load_setting(VIEWER_CHROME_VISIBLE_KEY, True))
        self._minimap_visible = bool(load_setting(VIEWER_MINIMAP_VISIBLE_KEY, True))
        self._scene_anchors_visible = bool(load_setting(VIEWER_SCENE_ANCHORS_VISIBLE_KEY, True))
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

        self.back_button = self._make_toolbar_button("fa5s.arrow-left", "Back to details", self.go_back)
        self.prev_button = self._make_toolbar_button("fa5s.chevron-left", "Previous chapter", self.prev_chapter)
        self.next_button = self._make_toolbar_button("fa5s.chevron-right", "Next chapter", self.next_chapter)

        self.chapter_selector = QComboBox()
        self.chapter_selector.setFocusPolicy(Qt.NoFocus)
        self.chapter_selector.setStyleSheet(VIEWER_TOOLBAR_COMBO_STYLE)
        self.chapter_selector.setMinimumWidth(170)
        self.chapter_selector.setMaximumWidth(240)
        self.chapter_selector.currentIndexChanged.connect(self.load_selected_chapter)

        self.nav_toggle = self._make_toolbar_button("fa5s.magic", "Auto Skip", self._toggle_navigation_mode, checkable=True)
        self.nav_toggle.setChecked(self.auto_skip_enabled)

        self.save_scene_btn = self._make_toolbar_button("fa5s.bookmark", "Save the current scene with an optional note", self._save_scene_bookmark)
        self.scene_list_btn = self._make_toolbar_button("fa5s.images", "Open saved scenes for this chapter", self._open_scene_bookmarks)
        self.focus_mode_btn = self._make_toolbar_button("fa5s.bullseye", "Focused reading mode", self._toggle_focus_mode, checkable=True)
        self.focus_mode_btn.setChecked(self._focus_mode_enabled)
        self.chrome_btn = self._make_toolbar_button("fa5s.window-minimize", "Show or hide the reader toolbar", self._toggle_chrome, checkable=True)
        self.chrome_btn.setChecked(self._chrome_visible)
        self.minimap_btn = self._make_toolbar_button("fa5s.map", "Show or hide the reading mini-map", self._toggle_minimap, checkable=True)
        self.minimap_btn.setChecked(self._minimap_visible)
        self.anchors_btn = self._make_toolbar_button("fa5s.map-pin", "Show or hide saved scene anchors on the mini-map", self._toggle_scene_anchors, checkable=True)
        self.anchors_btn.setChecked(self._scene_anchors_visible)
        self.zoom_out_btn = self._make_toolbar_button("fa5s.search-minus", "Decrease image width", self._zoom_out)
        self.zoom_in_btn = self._make_toolbar_button("fa5s.search-plus", "Increase image width", self._zoom_in)

        self._zoom_slider = QSlider(Qt.Horizontal)
        self._zoom_slider.setFixedWidth(100)
        self._zoom_slider.setMinimum(15)
        self._zoom_slider.setMaximum(100)
        self._zoom_slider.setValue(int(self._zoom * 100))
        self._zoom_slider.setFocusPolicy(Qt.NoFocus)
        self._zoom_slider.setToolTip("Image width")
        self._zoom_slider.valueChanged.connect(self._on_zoom_slider)

        self._zoom_label = QLabel(f"{int(self._zoom * 100)}%")
        self._zoom_label.setFixedWidth(36)
        self._zoom_label.setAlignment(Qt.AlignCenter)
        self._zoom_label.setStyleSheet(VIEWER_ZOOM_LABEL_STYLE)

        self._zoom_reset_btn = QPushButton("Reset zoom")
        self._zoom_reset_btn.setFocusPolicy(Qt.NoFocus)
        self._zoom_reset_btn.setToolTip("Remove webtoon zoom override and use global default")
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
        top_bar.addWidget(self.chrome_btn)
        top_bar.addWidget(self.minimap_btn)
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

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.preview = ChapterPreview(self.scroll, metrics_provider=self, scene_jump_callback=self._jump_to_current_scene_mark)

        content_row.addWidget(self.scroll)
        content_row.addWidget(self.preview)
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
        self.scroll.setWidget(self.container)

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

        self.scroll.verticalScrollBar().valueChanged.connect(self.check_visible_images)
        self.scroll.verticalScrollBar().valueChanged.connect(self.preview.update)
        self.scroll.verticalScrollBar().valueChanged.connect(self._update_session_overlay)

        self._progress_save_timer = QTimer()
        self._progress_save_timer.setSingleShot(True)
        self._progress_save_timer.setInterval(1000)
        self._progress_save_timer.timeout.connect(self._save_progress)
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
        self._apply_reader_session_state(persist=False)

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
        should_show = self._chrome_visible and (
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
        self.preview.setVisible(self._minimap_visible)
        self.preview.set_scene_marks_visible(self._scene_anchors_visible)
        self.focus_mode_btn.setChecked(self._focus_mode_enabled)
        self.chrome_btn.setChecked(self._chrome_visible)
        self.minimap_btn.setChecked(self._minimap_visible)
        self.anchors_btn.setChecked(self._scene_anchors_visible)
        self.nav_toggle.setChecked(self.auto_skip_enabled)
        self.nav_toggle.setToolTip("Auto Skip enabled" if self.auto_skip_enabled else "Standard page navigation")
        self.focus_mode_btn.setToolTip("Focused reading mode on" if self._focus_mode_enabled else "Focused reading mode off")
        self.chrome_btn.setToolTip("Reader toolbar hover reveal on" if self._chrome_visible else "Reader toolbar hidden")
        self.minimap_btn.setToolTip("Mini-map visible" if self._minimap_visible else "Mini-map hidden")
        self.anchors_btn.setToolTip("Scene anchors visible" if self._scene_anchors_visible else "Scene anchors hidden")
        self.anchors_btn.setEnabled(self._minimap_visible)
        self._position_session_overlay()
        self._update_session_overlay()
        if persist:
            save_setting(VIEWER_FOCUS_MODE_KEY, self._focus_mode_enabled)
            save_setting(VIEWER_CHROME_VISIBLE_KEY, self._chrome_visible)
            save_setting(VIEWER_MINIMAP_VISIBLE_KEY, self._minimap_visible)
            save_setting(VIEWER_SCENE_ANCHORS_VISIBLE_KEY, self._scene_anchors_visible)

    def _toggle_focus_mode(self, checked: bool):
        self._focus_mode_enabled = bool(checked)
        if self._focus_mode_enabled:
            self._chrome_visible = False
            self._minimap_visible = True
        else:
            self._chrome_visible = True
        self._apply_reader_session_state()
        self.setFocus()

    def _toggle_chrome(self, checked: bool):
        self._chrome_visible = bool(checked)
        self._apply_reader_session_state()
        self.setFocus()

    def _toggle_minimap(self, checked: bool):
        self._minimap_visible = bool(checked)
        self._apply_reader_session_state()
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
        total = max(1, len(self.image_labels))
        progress = max(0.0, min(1.0, self._current_packed_position() / total)) if self.image_labels else 0.0
        scene_count = len(self._chapter_scene_marks)
        self.session_overlay.setText(
            f"{chapter} | {int(progress * 100)}%\n"
            f"Scenes {scene_count} | H UI | M Map | S Save | G List | [ ] Chapter | Esc Exit"
        )
        show_overlay = self._focus_mode_enabled or not self._chrome_visible
        self.session_overlay.setVisible(show_overlay)
        self._position_session_overlay()

    def _refresh_scene_marks(self):
        if not self.webtoon or not (0 <= self.current_chapter_index < len(self.webtoon.chapters)):
            self._chapter_scene_marks = []
            self.preview.set_scene_marks([])
            self.scene_list_btn.setToolTip("No saved scenes for this chapter")
            self.scene_list_btn.setEnabled(False)
            self._update_session_overlay()
            return
        chapter = self.webtoon.chapters[self.current_chapter_index]
        marks = sorted(
            self.scene_bookmark_store.list_for_chapter(self.webtoon.name, chapter),
            key=lambda item: float(item.get("packed") or 0.0),
        )
        self._chapter_scene_marks = marks
        self.preview.set_scene_marks(marks)
        count = len(marks)
        self.scene_list_btn.setToolTip(f"Open saved scenes for this chapter ({count})" if count else "Open saved scenes for this chapter")
        self.scene_list_btn.setEnabled(True)
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
        self._zoom_reset_btn.setEnabled(self._zoom_override_active)

    def _current_packed_position(self) -> float:
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
        return self._prefix_heights[-1] if self._prefix_heights else 0

    def image_index_at_offset(self, scroll_top: int) -> int:
        if not self.image_labels:
            return 0
        idx = bisect_right(self._prefix_heights, max(0, int(scroll_top))) - 1
        return max(0, min(len(self.image_labels) - 1, idx))

    def _packed_position_at(self, scroll_top: int, zoom: float | None = None) -> float:
        if not self.image_labels:
            return 0.0
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
        total = len(self.image_labels)
        packed = max(0.0, float(packed))
        if packed >= total:
            return self.total_content_height()
        idx = max(0, min(total - 1, int(packed)))
        frac = max(0.0, min(1.0, packed - int(packed)))
        base = self.cumulative_height_before(idx)
        height = self._label_heights[idx] if idx < len(self._label_heights) else self._scaled_label_height(self.image_labels[idx])
        return base + int(height * frac)

    def _save_progress(self):
        if not self.webtoon or not self.image_labels:
            return
        chapter = self.webtoon.chapters[self.current_chapter_index]
        total = len(self.image_labels)
        bar = self.scroll.verticalScrollBar()
        if bar.value() >= bar.maximum() and bar.maximum() > 0:
            packed = float(total)
        else:
            packed = self._current_packed_position()
        logger.info(
            "Viewer saving progress for %s chapter=%s packed=%.3f total=%d",
            self.webtoon.name,
            chapter,
            packed,
            total,
        )
        self.progress_store.save(self.webtoon.name, chapter, packed, total)

    def _current_scene_bookmark_payload(self) -> tuple[str, float, int, float] | None:
        if not self.webtoon or not self.image_labels:
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
        note, accepted = QInputDialog.getText(
            self,
            "Save Scene",
            "Optional note for this scene:",
        )
        if not accepted:
            return
        thumbnail_path = self._save_scene_thumbnail(image_index, offset_frac)
        self.scene_bookmark_store.save(
            self.webtoon.name,
            chapter,
            packed,
            image_index + 1,
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
        payload = self._current_scene_bookmark_payload()
        if payload is None:
            return
        chapter, _packed, _image_index, _offset_frac = payload
        dialog = SceneBookmarksDialog(
            self.webtoon,
            chapter,
            self.scene_bookmark_store,
            lambda packed: self._jump_to_saved_scene(chapter, packed),
            parent=self,
        )
        dialog.exec()
        self._refresh_scene_marks()
        self.setFocus()

    def _jump_to_saved_scene(self, chapter: str, packed: float):
        if not self.webtoon:
            return
        current_chapter = self.webtoon.chapters[self.current_chapter_index]
        if chapter != current_chapter:
            return
        self._unpack_restore(float(packed))
        self._apply_restore()
        if self._restore_image_index is not None:
            self._progress_save_timer.start()

    def _unpack_restore(self, packed: float):
        if packed < 0.005:
            self._restore_image_index = None
            self._restore_image_offset = 0.0
        else:
            self._restore_image_index = int(packed)
            self._restore_image_offset = packed - int(packed)

    def _clear_pending_restore(self):
        self._restore_image_index = None
        self._restore_image_offset = 0.0

    def _apply_restore(self):
        idx = self._restore_image_index
        if idx is None or idx >= len(self.image_labels):
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
        cumulative = self.cumulative_height_before(idx)
        height = self._label_heights[idx] if idx < len(self._label_heights) else self._scaled_label_height(self.image_labels[idx])
        target_px = cumulative + int(height * offset_frac) - max(0, anchor_px)

        bar = self.scroll.verticalScrollBar()
        bar.setValue(max(0, min(target_px, bar.maximum())))

        return not (bar.value() < target_px - 5)

    def _capture_layout_anchor(self, changed_indexes: list[int]) -> float | None:
        if (
            not changed_indexes
            or not self.image_labels
            or self._restore_image_index is not None
            or self._applying_restore
            or self._resize_packed is not None
        ):
            return None

        current_index = self.image_index_at_offset(self.scroll.verticalScrollBar().value())
        if min(changed_indexes) > current_index:
            return None
        return self._current_packed_position()

    def _restore_layout_anchor(self, packed: float | None) -> None:
        if packed is None or not self.image_labels:
            return
        idx = max(0, min(len(self.image_labels) - 1, int(packed)))
        self._jump_to_packed(idx, packed - int(packed))

    def _on_image_ready(self, index: int, pixmap: QPixmap):
        if index >= len(self.image_labels):
            return
        self._chapter_load_loaded += 1
        self._update_loading_overlay()

        # Only do one immediate paint per chapter load.
        # Everything else should go through the batch path so restore logic runs.
        if not self._did_immediate_first_paint and not self._pending_batch:
            label = self.image_labels[index]
            label._source_pixmap = pixmap
            label._natural_width = pixmap.width()
            label._natural_height = pixmap.height()
            self._apply_pixmap_to_label(label)
            self._set_label_height_cache(index, label.height())

            self._did_immediate_first_paint = True

            self.preview.notify_image_loaded()
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
        self._invalidate_panel_cache()
        self.check_visible_images()
        self._panel_warm_timer.start()
        self._restore_layout_anchor(anchor)

        if self._resize_packed is not None:
            idx = int(self._resize_packed)
            frac = self._resize_packed - idx
            if (
                self.image_labels
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
        self._load_chapter_with_prompt(real_index)

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

        self._load_chapter_images(chapter)
        self._refresh_scene_marks()
        self.update_nav_buttons()

        if self._restore_image_index is None:
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
        self._reset_layout_metrics()

        self.preview.set_image_labels([])
        self.preview.set_scene_marks([])
        self._chapter_scene_marks = []
        self.scene_list_btn.setToolTip("No saved scenes for this chapter")
        self.scene_list_btn.setEnabled(False)
        self._update_session_overlay()

        self._panel_ranges = []
        self._panel_ranges_dirty = True
        self._panel_build_generation += 1
        self._panel_build_inflight = False

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
        target_width = self._image_width()

        for img_path, natural_w, natural_h, file_size in image_infos:
            label = self._acquire_image_label()
            label.img_path = img_path
            label._source_pixmap = None
            label._preview_pixmap = None
            label._natural_width = natural_w
            label._natural_height = natural_h
            label._file_size = file_size
            if natural_w > 0 and natural_h > 0:
                placeholder_height = max(100, int(target_width * (natural_h / natural_w)))
            else:
                placeholder_height = 400
            label.setFixedHeight(placeholder_height)
            self.image_layout.addWidget(label)
            self.image_labels.append(label)
        self._label_heights = [label.height() for label in self.image_labels]
        self._rebuild_prefix_heights()
        logger.info("Viewer queued %d images for %s / %s", len(self.image_labels), self.webtoon.name, chapter)

        self.preview.set_image_labels(self.image_labels)

        self.check_visible_images()
        QTimer.singleShot(0, self.check_visible_images)

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
        self.loader.load_preview(index, self.image_labels[index].img_path)

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
        end = min(len(self.image_labels), idx + 3)
        for i in range(end):
            self.loader.load(i, self.image_labels[i].img_path, 0)

    def check_visible_images(self):
        viewport_top = self.scroll.verticalScrollBar().value()
        viewport_bottom = viewport_top + self.scroll.viewport().height()
        if not self.image_labels:
            return

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
        scaled = src.scaledToWidth(self._image_width(), Qt.SmoothTransformation)
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
                self._set_label_height_cache(index, label.height())
        finally:
            self.container.setUpdatesEnabled(True)

        # Defer the jump so Qt finishes reflowing label geometry first.
        def _restore():
            if self._resize_packed is None or not self.image_labels:
                return
            idx = int(self._resize_packed)
            frac = self._resize_packed - idx
            if idx < len(self.image_labels):
                self._jump_to_packed(idx, frac, self._resize_anchor_px)

        QTimer.singleShot(0, _restore)

        self.preview.update()
        self._invalidate_panel_cache()
        self._panel_warm_timer.start()

    def next_chapter(self):
        next_idx = self._next_chapter_index(self.current_chapter_index)
        if next_idx is not None:
            logger.info("Viewer moving to next chapter for %s", self.webtoon.name if self.webtoon else "<none>")
            self._progress_save_timer.stop()
            self._save_progress()
            self._restore_image_index = None
            self._load_chapter_with_prompt(next_idx)

    def prev_chapter(self):
        prev_idx = self._prev_chapter_index(self.current_chapter_index)
        if prev_idx is not None:
            logger.info("Viewer moving to previous chapter for %s", self.webtoon.name if self.webtoon else "<none>")
            self._progress_save_timer.stop()
            self._save_progress()
            self._restore_image_index = None
            self._load_chapter_with_prompt(prev_idx)

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

    def update_nav_buttons(self):
        self.prev_button.setEnabled(self._prev_chapter_index(self.current_chapter_index) is not None)
        self.next_button.setEnabled(self._next_chapter_index(self.current_chapter_index) is not None)

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

    def _on_preview_ready(self, index: int, pixmap: QPixmap, natural_w: int, natural_h: int):
        """Thumbnail arrived - store it and set correct label height if not yet loaded."""
        if index >= len(self.image_labels):
            return
        label = self.image_labels[index]
        label._preview_pixmap = pixmap
        label._natural_width = natural_w
        label._natural_height = natural_h
        if getattr(label, '_source_pixmap', None) is None and natural_w > 0 and natural_h > 0:
            anchor = self._capture_layout_anchor([index])
            aspect = natural_h / natural_w
            scaled_h = max(100, int(self._image_width() * aspect))
            label.setFixedHeight(scaled_h)
            self._set_label_height_cache(index, scaled_h)
            self._restore_layout_anchor(anchor)
        self.preview.notify_image_loaded()

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

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()
        move_down = key in (Qt.Key_Down, Qt.Key_J, Qt.Key_PageDown) or (
            key == Qt.Key_Space and not bool(modifiers & Qt.ShiftModifier)
        )
        move_up = key in (Qt.Key_Up, Qt.Key_K, Qt.Key_PageUp) or (
            key == Qt.Key_Space and bool(modifiers & Qt.ShiftModifier)
        )
        chapter_forward = key in (Qt.Key_Right, Qt.Key_BracketRight)
        chapter_back = key in (Qt.Key_Left, Qt.Key_BracketLeft)
        session_keys = {
            Qt.Key_H,
            Qt.Key_M,
            Qt.Key_A,
            Qt.Key_F,
            Qt.Key_S,
            Qt.Key_G,
            Qt.Key_Home,
            Qt.Key_End,
            Qt.Key_Escape,
        }

        if self._restore_image_index is not None and not self._applying_restore:
            if move_down or move_up or chapter_forward or chapter_back or key in session_keys:
                self._clear_pending_restore()

        bar = self.scroll.verticalScrollBar()
        view_h = self.scroll.viewport().height()
        pos = bar.value()
        if move_down or move_up:
            direction_key = Qt.Key_Down if move_down else Qt.Key_Up

            if not self.auto_skip_enabled:
                if direction_key == Qt.Key_Down:
                    bar.setValue(pos + int(view_h * 0.9))
                else:
                    bar.setValue(max(0, pos - int(view_h * 0.9)))
                return

            targets = self._get_skip_targets()

            if not targets:
                if direction_key == Qt.Key_Down:
                    bar.setValue(pos + int(view_h * 0.9))
                else:
                    bar.setValue(max(0, pos - int(view_h * 0.9)))
                return

            SNAP = max(32, int(view_h * 0.07))
            MIN_MOVE = max(56, int(view_h * 0.10))

            if direction_key == Qt.Key_Down:
                next_index = bisect_right(targets, pos + SNAP)
                while next_index < len(targets) and (targets[next_index] - pos) < MIN_MOVE:
                    next_index += 1

                next_target = targets[next_index] if next_index < len(targets) else None

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

        elif chapter_forward:
            next_idx = self._next_chapter_index(self.current_chapter_index)
            if next_idx is not None:
                self._progress_save_timer.stop()
                self._save_progress()
                self._restore_image_index = None
                if self._load_chapter_with_prompt(next_idx):
                    self.setFocus()

        elif chapter_back:
            prev_idx = self._prev_chapter_index(self.current_chapter_index)
            if prev_idx is not None:
                self._progress_save_timer.stop()
                self._save_progress()
                self._restore_image_index = None
                if self._load_chapter_with_prompt(prev_idx):
                    self.setFocus()

        elif key == Qt.Key_Home:
            bar.setValue(0)

        elif key == Qt.Key_End:
            bar.setValue(bar.maximum())

        elif key == Qt.Key_S:
            self._save_scene_bookmark()

        elif key == Qt.Key_G:
            self._open_scene_bookmarks()

        elif key == Qt.Key_H:
            self._toggle_chrome(not self._chrome_visible)

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
            elif not self._chrome_visible:
                self._toggle_chrome(True)
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

    def _set_auto_scroll_direction(self, direction: int) -> None:
        scroll_area = getattr(self, "scroll", None)
        viewport = scroll_area.viewport() if isinstance(scroll_area, QScrollArea) else None
        if viewport is None or not self.auto_scroll:
            return
        normalized = -1 if direction < 0 else 1 if direction > 0 else 0
        if normalized == self._auto_scroll_direction:
            return
        self._auto_scroll_direction = normalized
        viewport.setCursor(self._auto_scroll_cursors[normalized])

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
            viewport.setCursor(self._auto_scroll_cursors[0])
            if not self.scroll_timer.isActive():
                self.scroll_timer.start(16)
        else:
            self._auto_scroll_direction = 0
            self.scroll_timer.stop()
            viewport.unsetCursor()

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
                    if self._chrome_visible and (in_trigger_zone or in_toolbar or obj == self.top_bar_widget):
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



