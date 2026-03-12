import json
import os
import shutil
import time
from types import SimpleNamespace

from PySide6.QtCore import QEvent, QMimeData, QPoint, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QColor, QDrag, QFont, QFontMetrics, QPainter, QPen, QPixmap

from core.app_logging import get_logger
from gui.common.styles import (
    BATCH_BAR_STYLE,
    BATCH_LABEL_STYLE,
    BUTTON_STYLE,
    DELETE_BUTTON_STYLE,
    ACCENT_MUTED,
    BG,
    BG_ALT,
    BORDER,
    PAGE_BG_STYLE,
    SCROLL_AREA_STYLE,
    SEARCH_INPUT_STYLE,
    SLIDER_STYLE,
    SURFACE,
    SURFACE_SOFT,
    SECTION_HEADER_BUTTON_STYLE,
    SECTION_MENU_BUTTON_STYLE,
    TRANSPARENT_BG_STYLE,
    TEXT_DIM_LABEL_STYLE,
    TEXT_MUTED_LABEL_STYLE,
    section_empty_state_style,
)
from gui.library.webtoon_card import WebtoonCard, CARD_WIDTH
from gui.search.global_search import rank_webtoons
from gui.settings.settings_page import (
    LIBRARY_SHOW_DOWNLOADS_SECTION_KEY,
    LIBRARY_SHOW_NEW_SECTION_KEY,
    LIBRARY_USE_CATEGORIES_KEY,
    load_library_path,
    load_setting,
    save_setting,
)
from library.library_categories import (
    load_custom_categories,
    load_section_order,
    save_custom_categories,
    save_section_order,
)
from library.library_manager import build_webtoon_from_folder, scan_library
from stores.progress_store import get_instance as get_progress_store
from core.update_utils import cooldown_remaining
from stores.webtoon_settings_store import get_instance as get_webtoon_settings


CARD_SPACING = 16
PAGE_PADDING = 24
CARD_SCALE_MIN = 70
CARD_SCALE_MAX = 140
CARD_SCALE_KEY = "library_card_scale"
SECTION_DRAG_DISTANCE = 5
SECTION_REORDER_EDGE = 18
SECTION_DOWNLOADS = "__downloads__"
SECTION_NEW = "__new__"
SECTION_BOOKMARKED = "__bookmarked__"
SECTION_LIBRARY = "__library__"
SECTION_UNCATEGORIZED = "__uncategorized__"
logger = get_logger(__name__)


class CategorySection(QFrame):

    def __init__(
        self,
        section_key: str,
        title: str,
        on_toggle,
        on_drop,
        on_reorder=None,
        on_menu=None,
        parent=None,
    ):
        super().__init__(parent)
        self.section_key = section_key
        self._title = title
        self._on_toggle = on_toggle
        self._on_drop = on_drop
        self._on_reorder = on_reorder
        self._on_menu = on_menu
        self._collapsed = False
        self._drop_active = False
        self._reorder_drop_active = False
        self._drop_side = None
        self._drag_start_pos = None
        self._header_dragging = False

        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(TRANSPARENT_BG_STYLE)
        self.setAcceptDrops(callable(on_drop) or callable(on_reorder))

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)

        self.drag_handle_btn = QPushButton("::", self)
        self.drag_handle_btn.setCursor(Qt.OpenHandCursor if callable(on_reorder) else Qt.ArrowCursor)
        self.drag_handle_btn.setFixedSize(24, 24)
        self.drag_handle_btn.setStyleSheet(SECTION_MENU_BUTTON_STYLE)
        self.drag_handle_btn.setVisible(callable(on_reorder))
        self.drag_handle_btn.installEventFilter(self)
        header_row.addWidget(self.drag_handle_btn, 0, Qt.AlignVCenter)

        self.header_btn = QPushButton(self)
        self.header_btn.setCursor(Qt.PointingHandCursor)
        self.header_btn.setStyleSheet(SECTION_HEADER_BUTTON_STYLE)
        self.header_btn.clicked.connect(self._toggle)
        header_row.addWidget(self.header_btn, 1)

        self.menu_btn = QPushButton("...", self)
        self.menu_btn.setCursor(Qt.PointingHandCursor)
        self.menu_btn.setFixedSize(28, 24)
        self.menu_btn.setStyleSheet(SECTION_MENU_BUTTON_STYLE)
        self.menu_btn.clicked.connect(self._show_menu)
        self.menu_btn.setVisible(callable(on_menu))
        header_row.addWidget(self.menu_btn, 0, Qt.AlignVCenter)

        root.addLayout(header_row)

        self.content = QWidget(self)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 8)
        self.content_layout.setSpacing(10)

        self.empty_state = QLabel("Drop titles here", self.content)
        self.empty_state.setAlignment(Qt.AlignCenter)
        self.empty_state.setMinimumSize(CARD_WIDTH + 16, int(CARD_WIDTH * (270 / 180)) + 16)
        self.empty_state.setStyleSheet("")
        self.empty_state.hide()
        self.content_layout.addWidget(self.empty_state)

        self.grid_host = QWidget(self.content)
        self.grid_host.setStyleSheet(TRANSPARENT_BG_STYLE)
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(CARD_SPACING)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.content_layout.addWidget(self.grid_host)
        root.addWidget(self.content)

        self.drop_indicator = QFrame(self)
        self.drop_indicator.hide()
        self.drop_indicator.setStyleSheet("background: rgba(255, 138, 122, 0.95); border-radius: 2px;")

        self._apply_drop_style()
        self.set_title(title, 0)

    def set_title(self, title: str, count: int):
        self._title = title
        prefix = "▸" if self._collapsed else "▾"
        self.header_btn.setText(f"{prefix}  {title} ({count})")

    def set_collapsed(self, collapsed: bool):
        self._collapsed = bool(collapsed)
        self.content.setVisible(not self._collapsed)
        self.set_title(self._title, self._current_count())

    def set_empty_state(self, visible: bool):
        self.empty_state.setVisible(visible)
        self.grid_host.setVisible(not visible)

    def set_empty_card_size(self, card_width: int):
        width = max(120, int(card_width)) + 16
        height = int(max(120, int(card_width)) * (270 / 180)) + 16
        self.empty_state.setMinimumSize(width, height)
        self.empty_state.setMaximumWidth(width)

    def _current_count(self) -> int:
        text = self.header_btn.text()
        if "(" not in text or not text.endswith(")"):
            return 0
        try:
            return int(text.rsplit("(", 1)[-1][:-1])
        except ValueError:
            return 0

    def _toggle(self):
        self.set_collapsed(not self._collapsed)
        if callable(self._on_toggle):
            self._on_toggle(self.section_key, self._collapsed)

    def _show_menu(self):
        if callable(self._on_menu):
            self._on_menu(self)

    def dragEnterEvent(self, event):
        if callable(self._on_reorder) and event.mimeData().hasFormat("application/x-library-category-section"):
            dragged_key = bytes(event.mimeData().data("application/x-library-category-section")).decode("utf-8", errors="ignore")
            drop_side = self._drop_side_for_pos(event.position().toPoint())
            if dragged_key and dragged_key != self.section_key and drop_side is not None:
                self._set_reorder_drop_state(True, drop_side)
                event.acceptProposedAction()
                return
        if callable(self._on_drop) and event.mimeData().hasFormat("application/x-webtoon-names"):
            self._drop_active = True
            self._apply_drop_style()
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if callable(self._on_reorder) and event.mimeData().hasFormat("application/x-library-category-section"):
            drop_side = self._drop_side_for_pos(event.position().toPoint())
            if drop_side is not None:
                self._set_reorder_drop_state(True, drop_side)
                event.acceptProposedAction()
                return
            self._set_reorder_drop_state(False, None)
            event.ignore()
            return
        if callable(self._on_drop) and event.mimeData().hasFormat("application/x-webtoon-names"):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        self._drop_active = False
        self._set_reorder_drop_state(False, None)
        self._apply_drop_style()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        if callable(self._on_reorder) and event.mimeData().hasFormat("application/x-library-category-section"):
            dragged_key = bytes(event.mimeData().data("application/x-library-category-section")).decode("utf-8", errors="ignore")
            insert_after = self._drop_side == "right"
            self._set_reorder_drop_state(False, None)
            if dragged_key and dragged_key != self.section_key and self._on_reorder(dragged_key, self.section_key, insert_after):
                event.acceptProposedAction()
                return
            event.ignore()
            return
        if not callable(self._on_drop) or not event.mimeData().hasFormat("application/x-webtoon-names"):
            super().dropEvent(event)
            return
        try:
            names = json.loads(bytes(event.mimeData().data("application/x-webtoon-names")).decode("utf-8"))
        except Exception:
            self._drop_active = False
            self._apply_drop_style()
            event.ignore()
            return
        if self._on_drop(self.section_key, [str(name) for name in names if str(name).strip()]):
            self._drop_active = False
            self._apply_drop_style()
            event.acceptProposedAction()
            return
        self._drop_active = False
        self._apply_drop_style()
        event.ignore()

    def eventFilter(self, watched, event):
        if watched is self.drag_handle_btn and callable(self._on_reorder):
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._drag_start_pos = event.position().toPoint()
                self._header_dragging = False
                self.drag_handle_btn.setCursor(Qt.ClosedHandCursor)
            elif event.type() == QEvent.MouseMove:
                if (
                    self._drag_start_pos is not None
                    and (event.buttons() & Qt.LeftButton)
                    and (event.position().toPoint() - self._drag_start_pos).manhattanLength() >= SECTION_DRAG_DISTANCE
                ):
                    self._header_dragging = True
                    self._start_section_drag()
                    self._drag_start_pos = None
                    return True
            elif event.type() == QEvent.MouseButtonRelease:
                self._drag_start_pos = None
                self.drag_handle_btn.setCursor(Qt.OpenHandCursor)
                if self._header_dragging:
                    self._header_dragging = False
                    return True
                self._header_dragging = False
        return super().eventFilter(watched, event)

    def _start_section_drag(self):
        mime = QMimeData()
        mime.setData("application/x-library-category-section", self.section_key.encode("utf-8"))
        drag = QDrag(self.header_btn)
        drag.setMimeData(mime)
        preview = self._build_drag_preview()
        drag.setPixmap(preview)
        drag.setHotSpot(QPoint(min(24, preview.width() // 4), min(16, preview.height() // 2)))
        drag.exec(Qt.MoveAction)

    def _build_drag_preview(self) -> QPixmap:
        title = self._title or self.section_key
        count_text = self.header_btn.text().rsplit("(", 1)[-1].rstrip(")") if "(" in self.header_btn.text() else ""
        subtitle = f"{title} ({count_text})" if count_text else title
        font = QFont(self.header_btn.font())
        font.setPointSize(max(font.pointSize(), 11))
        font.setBold(True)
        metrics = QFontMetrics(font)
        width = min(320, max(180, metrics.horizontalAdvance(subtitle) + 34))
        height = 40
        preview = QPixmap(width, height)
        preview.fill(Qt.transparent)

        painter = QPainter(preview)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(28, 18, 17, 240))
        painter.setPen(QPen(QColor(255, 138, 122, 220), 2))
        painter.drawRoundedRect(1, 1, width - 2, height - 2, 10, 10)
        painter.setPen(QColor("#fff0ec"))
        painter.setFont(font)
        painter.drawText(14, 26, subtitle)
        painter.end()
        return preview

    def _apply_drop_style(self):
        border = "rgba(255, 138, 122, 0.75)" if self._drop_active else "rgba(255, 255, 255, 0.08)"
        background = "rgba(120, 53, 46, 0.18)" if self._drop_active else "rgba(255, 255, 255, 0.025)"
        text = "#ffd7cf" if self._drop_active else "#9b7670"
        self.empty_state.setStyleSheet(section_empty_state_style(border, background, text))
        self._update_drop_indicator()

    def _set_reorder_drop_state(self, active: bool, side: str | None):
        active = bool(active)
        if self._reorder_drop_active == active and self._drop_side == side:
            return
        self._reorder_drop_active = active
        self._drop_side = side
        self._update_drop_indicator()

    def _update_drop_indicator(self):
        if self._reorder_drop_active and self._drop_side in {"left", "right"}:
            self.drop_indicator.setGeometry(
                0 if self._drop_side == "left" else max(0, self.width() - 4),
                6,
                4,
                max(24, self.height() - 12),
            )
            self.drop_indicator.show()
            self.drop_indicator.raise_()
        else:
            self.drop_indicator.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_drop_indicator()

    def _drop_side_for_pos(self, pos: QPoint) -> str | None:
        if pos.x() <= SECTION_REORDER_EDGE:
            return "left"
        if pos.x() >= max(SECTION_REORDER_EDGE, self.width() - SECTION_REORDER_EDGE):
            return "right"
        return None


class FlatLibrarySection(QFrame):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.section_key = SECTION_LIBRARY
        self._title = ""
        self._collapsed = False

        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(TRANSPARENT_BG_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.empty_state = QLabel("No webtoons found", self)
        self.empty_state.setAlignment(Qt.AlignCenter)
        self.empty_state.hide()
        root.addWidget(self.empty_state)

        self.grid_host = QWidget(self)
        self.grid_host.setStyleSheet(TRANSPARENT_BG_STYLE)
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(CARD_SPACING)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        root.addWidget(self.grid_host)

    def set_title(self, title: str, count: int):
        self._title = title

    def set_collapsed(self, collapsed: bool):
        self._collapsed = bool(collapsed)
        self.grid_host.setVisible(True)

    def set_empty_state(self, visible: bool):
        self.empty_state.setVisible(visible)
        self.grid_host.setVisible(not visible)

    def set_empty_card_size(self, card_width: int):
        width = max(120, int(card_width)) + 16
        height = int(max(120, int(card_width)) * (270 / 180)) + 16
        self.empty_state.setMinimumSize(width, height)
        self.empty_state.setMaximumWidth(width)


class LibraryPage(QWidget):

    def __init__(self, main_window):
        super().__init__()

        self.main_window = main_window
        self.progress_store = get_progress_store()
        self.settings_store = get_webtoon_settings()
        self._webtoons = []
        self._cards = []
        self._cards_by_name = {}
        self._current_cols = 0
        self._pending_search = ""
        self._update_service = None
        self._active_updates = {}
        self._manual_download_service = None
        self._active_manual_downloads = {}
        self._ignore_open_until = 0.0
        self._block_input_until = 0.0
        self._pending_reload = False
        self._pending_incremental_refresh_names = set()
        self._card_scale = int(load_setting(CARD_SCALE_KEY, 100))
        self._pending_card_scale = self._card_scale
        self._selected_webtoons = set()
        self._category_names = []
        self._section_order = []
        self._collapsed_sections = {}
        self._section_widgets = {}
        self._section_cards = {}
        self._section_layout_cols = 0
        self.setStyleSheet(PAGE_BG_STYLE)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(12)

        self.controls_bar = QWidget(self)
        self.controls_bar.setStyleSheet(
            f"background: {BG_ALT}; border-bottom: 1px solid {BORDER};"
        )
        controls = QHBoxLayout(self.controls_bar)
        controls.setContentsMargins(PAGE_PADDING, 16, PAGE_PADDING, 14)
        controls.setSpacing(16)

        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search webtoons...")
        self.search.setFixedHeight(38)
        self.search.setStyleSheet(SEARCH_INPUT_STYLE)
        controls.addWidget(self.search, 1)

        scale_panel = QWidget(self.controls_bar)
        scale_panel.setStyleSheet(
            f"background: {BG_ALT}; border: none; border-radius: 12px;"
        )
        scale_layout = QVBoxLayout(scale_panel)
        scale_layout.setContentsMargins(12, 8, 12, 8)
        scale_layout.setSpacing(4)

        self.size_label = QLabel("Library size", self)
        self.size_label.setStyleSheet(TEXT_MUTED_LABEL_STYLE)
        scale_layout.addWidget(self.size_label)

        scale_row = QHBoxLayout()
        scale_row.setContentsMargins(0, 0, 0, 0)
        scale_row.setSpacing(10)

        self.size_slider = QSlider(Qt.Horizontal, self)
        self.size_slider.setRange(CARD_SCALE_MIN, CARD_SCALE_MAX)
        self.size_slider.setValue(self._card_scale)
        self.size_slider.setFixedWidth(160)
        self.size_slider.setToolTip("Smaller cards fit more items per row")
        self.size_slider.setStyleSheet(f"""
            QSlider {{
                background: {SURFACE};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 4px 6px;
            }}
            QSlider::groove:horizontal {{
                height: 6px;
                border-radius: 3px;
                background: {SURFACE_SOFT};
            }}
            QSlider::sub-page:horizontal {{
                border-radius: 3px;
                background: {ACCENT_MUTED};
            }}
            QSlider::add-page:horizontal {{
                border-radius: 3px;
                background: {BG_ALT};
            }}
            QSlider::handle:horizontal {{
                width: 12px;
                margin: -3px 0;
                border-radius: 6px;
                border: 1px solid #ffe5de;
                background: #ffd4cb;
            }}
        """)
        self.size_slider.valueChanged.connect(self._on_size_slider_changed)
        self.size_slider.sliderReleased.connect(self._apply_pending_card_scale)
        scale_row.addWidget(self.size_slider)

        self.size_value_label = QLabel(f"{self._card_scale}%", self)
        self.size_value_label.setAlignment(Qt.AlignCenter)
        self.size_value_label.setMinimumWidth(42)
        self.size_value_label.setStyleSheet(
            f"color: {ACCENT_MUTED}; font-size: 12px; font-weight: 700;"
            f"background: {BG_ALT}; border: none; padding: 2px 0;"
        )
        scale_row.addWidget(self.size_value_label, 0, Qt.AlignVCenter)
        scale_layout.addLayout(scale_row)
        controls.addWidget(scale_panel, 0, Qt.AlignVCenter)

        root_layout.addWidget(self.controls_bar)

        self.batch_bar = QWidget(self)
        self.batch_bar.setStyleSheet(BATCH_BAR_STYLE)
        batch_layout = QHBoxLayout(self.batch_bar)
        batch_layout.setContentsMargins(PAGE_PADDING, 10, PAGE_PADDING, 10)
        batch_layout.setSpacing(10)

        self.batch_label = QLabel("", self.batch_bar)
        self.batch_label.setStyleSheet(BATCH_LABEL_STYLE)
        batch_layout.addWidget(self.batch_label)

        self.mark_completed_btn = QPushButton("Mark Completed", self.batch_bar)
        self.mark_completed_btn.setStyleSheet(BUTTON_STYLE)
        self.mark_completed_btn.clicked.connect(self._mark_selected_completed)
        batch_layout.addWidget(self.mark_completed_btn)

        self.bookmark_selected_btn = QPushButton("Bookmark Selected", self.batch_bar)
        self.bookmark_selected_btn.setStyleSheet(self.mark_completed_btn.styleSheet())
        self.bookmark_selected_btn.clicked.connect(self._toggle_selected_bookmarked)
        batch_layout.addWidget(self.bookmark_selected_btn)

        self.update_selected_btn = QPushButton("Update Selected", self.batch_bar)
        self.update_selected_btn.setStyleSheet(self.mark_completed_btn.styleSheet())
        self.update_selected_btn.clicked.connect(self._update_selected)
        batch_layout.addWidget(self.update_selected_btn)

        self.move_selected_btn = QPushButton("Move to Category", self.batch_bar)
        self.move_selected_btn.setStyleSheet(self.mark_completed_btn.styleSheet())
        self.move_selected_btn.clicked.connect(self._show_move_selected_menu)
        batch_layout.addWidget(self.move_selected_btn)

        self.delete_selected_btn = QPushButton("Delete Selected", self.batch_bar)
        self.delete_selected_btn.setStyleSheet(DELETE_BUTTON_STYLE)
        self.delete_selected_btn.clicked.connect(self._delete_selected)
        batch_layout.addWidget(self.delete_selected_btn)

        self.clear_selection_btn = QPushButton("Clear", self.batch_bar)
        self.clear_selection_btn.setStyleSheet(self.mark_completed_btn.styleSheet())
        self.clear_selection_btn.clicked.connect(self._clear_selection)
        batch_layout.addWidget(self.clear_selection_btn)
        batch_layout.addStretch()

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(SCROLL_AREA_STYLE)

        self.container = QWidget(self.scroll)
        self.container.setStyleSheet(PAGE_BG_STYLE)
        self.container.setContextMenuPolicy(Qt.CustomContextMenu)
        self.container.customContextMenuRequested.connect(self._show_library_context_menu)

        self.sections_layout = QGridLayout(self.container)
        self.sections_layout.setContentsMargins(PAGE_PADDING, PAGE_PADDING, PAGE_PADDING, PAGE_PADDING)
        self.sections_layout.setSpacing(18)
        self.sections_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.scroll.setWidget(self.container)
        root_layout.addWidget(self.scroll, 1)

        self.batch_bar.hide()
        root_layout.addWidget(self.batch_bar)

        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_filter)
        self.search.textChanged.connect(self._schedule_filter)

        self._scale_timer = QTimer(self)
        self._scale_timer.setSingleShot(True)
        self._scale_timer.timeout.connect(self._apply_pending_card_scale)

        self._cooldown_timer = QTimer(self)
        self._cooldown_timer.timeout.connect(self._sync_update_controls)
        self._cooldown_timer.start(1000)

        self._incremental_refresh_timer = QTimer(self)
        self._incremental_refresh_timer.setSingleShot(True)
        self._incremental_refresh_timer.timeout.connect(self._flush_incremental_refreshes)

        self._live_progress_timer = QTimer(self)
        self._live_progress_timer.timeout.connect(self._poll_live_progress)
        self._live_progress_timer.start(250)

        self._input_blocker = QWidget(self)
        self._input_blocker.hide()
        self._input_blocker.setStyleSheet(TRANSPARENT_BG_STYLE)

        self.load_library()

    def load_library(self):
        logger.info("Loading library page contents")
        self._pending_reload = False
        self._webtoons = self._filter_in_progress_manual_downloads(
            scan_library(load_library_path(), self.settings_store)
        )
        self._category_names = self._load_custom_categories()
        self._section_order = self._reconcile_section_order(load_section_order())
        self._prune_selection()
        self._rebuild_sections()

    def showEvent(self, event):
        super().showEvent(event)
        if self._pending_reload and (self._update_service is None or not self._update_service.is_busy()):
            self.load_library()
        self._poll_live_progress()

    def refresh_progress(self):
        for card in self._cards:
            card._refresh_badges()

    def refresh_dynamic_state(self):
        self._refresh_webtoon_flags()
        self._sync_update_controls()
        self._sync_manual_download_cards()

    def _refresh_webtoon_flags(self):
        section_changed = False
        for webtoon in self._webtoons:
            old_section = self._section_key_for_webtoon(webtoon)
            webtoon.category = self.settings_store.get_category(webtoon.name)
            webtoon.is_bookmarked = self.settings_store.get_bookmarked(webtoon.name)
            webtoon.has_new_chapter = bool(self.settings_store.get_latest_new_chapter(webtoon.name))
            new_section = self._section_key_for_webtoon(webtoon)
            section_changed = section_changed or old_section != new_section
            card = self._cards_by_name.get(webtoon.name)
            if card is not None:
                card.refresh_webtoon(webtoon)
        if section_changed:
            self._rebuild_sections()
            return
        self._relayout_sections(self._current_cols or self._columns_for_width(self.width()))

    def _load_custom_categories(self) -> list[str]:
        return load_custom_categories()

    def _save_custom_categories(self):
        save_custom_categories(self._category_names)

    def _save_section_order(self):
        save_section_order(self._section_order)

    def _card_width(self) -> int:
        return max(120, int(CARD_WIDTH * (self._card_scale / 100.0)))

    def _columns_for_width(self, width: int) -> int:
        card_width = self._card_width()
        available = max(width - PAGE_PADDING * 2, card_width + 16)
        return max(1, available // (card_width + 16 + CARD_SPACING))

    def _clear_sections(self):
        while self.sections_layout.count():
            item = self.sections_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._section_widgets = {}
        self._section_cards = {}
        self._cards = []
        self._cards_by_name = {}

    def _build_section_defs(self):
        defs = []
        placeholders = []
        names_in_library = {webtoon.name for webtoon in self._webtoons}
        if self._show_downloads_section():
            for name, state in self._active_manual_downloads.items():
                if name in names_in_library:
                    continue
                placeholders.append(SimpleNamespace(
                    name=name,
                    path="",
                    thumbnail=state.get("thumbnail", "") or "",
                    chapters=[],
                    _download_placeholder=True,
                    category=None,
                ))
            placeholders.sort(key=lambda item: item.name.lower())
            if placeholders:
                defs.append((SECTION_DOWNLOADS, "Active Downloads", placeholders, None))

        if self._show_new_section():
            new_webtoons = [
                webtoon for webtoon in self._webtoons
                if self._section_key_for_webtoon(webtoon) == SECTION_NEW
            ]
            defs.append((SECTION_NEW, "New", new_webtoons, None))

        bookmarked_webtoons = [
            webtoon for webtoon in self._webtoons
            if self._section_key_for_webtoon(webtoon) == SECTION_BOOKMARKED
        ]
        defs.append((SECTION_BOOKMARKED, "Bookmarked", bookmarked_webtoons, None))

        if self._categories_enabled():
            uncategorized = [
                webtoon for webtoon in self._webtoons
                if self._section_key_for_webtoon(webtoon) == SECTION_UNCATEGORIZED
            ]
            defs.append((SECTION_UNCATEGORIZED, "Uncategorized", uncategorized, SECTION_UNCATEGORIZED))

            for category in self._category_names:
                webtoons = [
                    webtoon for webtoon in self._webtoons
                    if self._section_key_for_webtoon(webtoon) == category
                ]
                defs.append((category, category, webtoons, category))
        else:
            library_webtoons = [
                webtoon for webtoon in self._webtoons
                if self._section_key_for_webtoon(webtoon) == SECTION_LIBRARY
            ]
            defs.append((SECTION_LIBRARY, "Library", library_webtoons, None))
        return defs

    def _section_key_for_webtoon(self, webtoon) -> str:
        if getattr(webtoon, "_download_placeholder", False):
            return SECTION_DOWNLOADS
        if self._show_new_section() and getattr(webtoon, "has_new_chapter", False):
            return SECTION_NEW
        if getattr(webtoon, "is_bookmarked", False):
            return SECTION_BOOKMARKED
        if not self._categories_enabled():
            return SECTION_LIBRARY
        category = getattr(webtoon, "category", None)
        return category or SECTION_UNCATEGORIZED

    def _rebuild_sections(self):
        self._clear_sections()
        self._current_cols = self._columns_for_width(self.width())
        for section_key, title, webtoons, drop_key in self._build_section_defs():
            if section_key == SECTION_LIBRARY:
                section = FlatLibrarySection(parent=self.container)
            else:
                section = CategorySection(
                    section_key,
                    title,
                    on_toggle=self._on_section_toggled,
                    on_drop=self._handle_section_drop if drop_key is not None else None,
                    on_reorder=self._handle_section_reorder,
                    on_menu=self._show_section_menu if section_key not in {
                        SECTION_DOWNLOADS,
                        SECTION_NEW,
                        SECTION_BOOKMARKED,
                        SECTION_UNCATEGORIZED,
                    } else None,
                    parent=self.container,
                )
            self._section_widgets[section_key] = section
            section.set_empty_card_size(self._card_width())

            cards = []
            for webtoon in webtoons:
                is_download_placeholder = bool(getattr(webtoon, "_download_placeholder", False))
                card = WebtoonCard(
                    webtoon,
                    settings_store=self.settings_store,
                    progress_store=self.progress_store,
                    on_open=self._open_detail,
                    on_changed=self._reload_after_edit,
                    on_update=self._start_update,
                    on_delete=self._delete_single_webtoon,
                    on_cancel_download=self._cancel_manual_download,
                    on_select=self._on_card_selected,
                    get_drag_names=self._drag_selection_for,
                    card_width=self._card_width(),
                    download_placeholder=is_download_placeholder,
                    parent=section.grid_host,
                )
                self._cards_by_name[webtoon.name] = card
                if is_download_placeholder:
                    state = self._active_manual_downloads.get(webtoon.name, {})
                    card.set_download_progress(state.get("current", 0), state.get("total", 0))
                else:
                    card.set_selected(webtoon.name in self._selected_webtoons)
                cards.append(card)
                self._cards.append(card)

            self._section_cards[section_key] = cards
            section.set_collapsed(self._collapsed_sections.get(section_key, False))

        self._relayout_sections(self._current_cols)
        self._sync_update_controls()
        self._sync_manual_download_cards()
        self._sync_batch_actions()

    def _add_section_widget(self, section_key: str):
        if section_key in self._section_widgets:
            return self._section_widgets[section_key]
        if section_key == SECTION_LIBRARY:
            section = FlatLibrarySection(parent=self.container)
            self._section_widgets[section_key] = section
            self._section_cards[section_key] = []
            return section
        title = {
            SECTION_NEW: "New",
            SECTION_BOOKMARKED: "Bookmarked",
            SECTION_LIBRARY: "Library",
            SECTION_UNCATEGORIZED: "Uncategorized",
        }.get(section_key, section_key)
        section = CategorySection(
            section_key,
            title,
            on_toggle=self._on_section_toggled,
            on_drop=self._handle_section_drop if section_key not in {SECTION_DOWNLOADS, SECTION_NEW, SECTION_BOOKMARKED} else None,
            on_reorder=self._handle_section_reorder,
            on_menu=self._show_section_menu if section_key not in {SECTION_DOWNLOADS, SECTION_NEW, SECTION_BOOKMARKED, SECTION_UNCATEGORIZED} else None,
            parent=self.container,
        )
        self._section_widgets[section_key] = section
        self._section_cards[section_key] = []
        section.set_collapsed(self._collapsed_sections.get(section_key, False))
        return section

    def _remove_empty_custom_sections(self):
        for section_key in list(self._section_widgets.keys()):
            if section_key in {SECTION_DOWNLOADS, SECTION_NEW, SECTION_BOOKMARKED, SECTION_LIBRARY, SECTION_UNCATEGORIZED}:
                continue
            if self._section_cards.get(section_key):
                continue
            section = self._section_widgets.pop(section_key)
            self._section_cards.pop(section_key, None)
            section.deleteLater()

    def _relayout_sections(self, columns: int | None = None):
        if columns is None:
            columns = self._columns_for_width(self.width())
        columns = max(1, columns)
        self._current_cols = columns
        self._clear_section_positions()
        self._section_layout_cols = columns
        for col in range(columns):
            self.sections_layout.setColumnStretch(col, 1)

        scores = {}
        search_text = self._pending_search.strip()
        if search_text:
            scores = {
                webtoon.name: score
                for score, webtoon in rank_webtoons(self._all_display_webtoons(), search_text)
            }

        layout_row = 0
        layout_col = 0
        for section_key in self._ordered_section_keys():
            section = self._section_widgets[section_key]
            while section.grid.count():
                section.grid.takeAt(0)

            cards = list(self._section_cards.get(section_key, []))
            visible_cards = []
            for card in cards:
                card.update_card_size(self._card_width())
                visible = card.webtoon.name in scores if search_text else True
                card.setVisible(visible)
                if visible:
                    visible_cards.append(card)

            if search_text:
                visible_cards.sort(
                    key=lambda card: (-scores.get(card.webtoon.name, 0), card.webtoon.name.lower())
                )

            section_cols = self._section_span_for(section_key, len(visible_cards), columns)
            for index, card in enumerate(visible_cards):
                row = index // section_cols
                col = index % section_cols
                section.grid.addWidget(card, row, col, Qt.AlignTop | Qt.AlignLeft)

            hide_empty = bool(search_text) or section_key in {
                SECTION_DOWNLOADS,
                SECTION_NEW,
                SECTION_BOOKMARKED,
                SECTION_LIBRARY,
                SECTION_UNCATEGORIZED,
            }
            should_show = bool(visible_cards) or not hide_empty
            section.setVisible(should_show)
            section.set_empty_card_size(self._card_width())
            section.set_empty_state(
                not search_text
                and section_key not in {
                    SECTION_DOWNLOADS,
                    SECTION_NEW,
                    SECTION_BOOKMARKED,
                    SECTION_LIBRARY,
                    SECTION_UNCATEGORIZED,
                }
                and len(cards) == 0
            )
            section.set_title(section._title, len(visible_cards) if search_text else len(cards))
            section.set_collapsed(self._collapsed_sections.get(section_key, False))
            if not should_show:
                continue
            if layout_col + section_cols > columns:
                layout_row += 1
                layout_col = 0
            self.sections_layout.addWidget(section, layout_row, layout_col, 1, section_cols, Qt.AlignTop | Qt.AlignLeft)
            layout_col += section_cols
            if layout_col >= columns:
                layout_row += 1
                layout_col = 0

        self.container.updateGeometry()

    def _clear_section_positions(self):
        while self.sections_layout.count():
            self.sections_layout.takeAt(0)

    def _section_span_for(self, section_key: str, visible_count: int, total_columns: int) -> int:
        if total_columns <= 1:
            return 1
        if self._collapsed_sections.get(section_key, False):
            return 1
        if visible_count <= 0:
            return 1
        return max(1, min(total_columns, visible_count))

    def _ordered_section_keys(self) -> list[str]:
        ordered = []
        for section_key in self._section_order:
            if section_key in self._section_widgets and section_key not in ordered:
                ordered.append(section_key)
        for section_key in self._section_widgets:
            if section_key not in ordered:
                ordered.append(section_key)
        return ordered

    def _all_display_webtoons(self):
        return list(self._webtoons)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._input_blocker.setGeometry(self.rect())
        if self._input_blocker.isVisible():
            self._input_blocker.raise_()
        new_cols = self._columns_for_width(event.size().width())
        if new_cols != self._current_cols and self._section_widgets:
            self._relayout_sections(new_cols)

    def _on_section_toggled(self, section_key: str, collapsed: bool):
        self._collapsed_sections[section_key] = bool(collapsed)

    def _show_library_context_menu(self, pos):
        if not self._categories_enabled():
            return
        menu = QMenu(self)
        new_category_action = menu.addAction("New Category")
        chosen = menu.exec(self.container.mapToGlobal(pos))
        if chosen == new_category_action:
            self._prompt_new_category()

    def _show_section_menu(self, section: CategorySection):
        category = section.section_key
        menu = QMenu(self)
        rename_action = menu.addAction("Rename Category")
        delete_action = menu.addAction("Delete Category")
        chosen = menu.exec(section.menu_btn.mapToGlobal(section.menu_btn.rect().bottomLeft()))
        if chosen == rename_action:
            new_name, ok = QInputDialog.getText(self, "Rename category", "Category name:", text=category)
            if ok:
                self._rename_category(category, new_name)
        elif chosen == delete_action:
            self._delete_category(category)

    def _prompt_new_category(self):
        if not self._categories_enabled():
            return
        name, ok = QInputDialog.getText(self, "New category", "Category name:")
        if not ok:
            return
        category = self._create_category(name)
        if category:
            self._block_library_input(0.3)
            self._suppress_card_open(0.3)
            self._rebuild_sections()

    def _create_category(self, raw_name: str) -> str | None:
        if not self._categories_enabled():
            return None
        category = str(raw_name).strip()
        if not category:
            return None
        if category not in self._category_names:
            self._category_names.append(category)
            self._save_custom_categories()
            if category not in self._section_order:
                self._section_order.append(category)
                self._save_section_order()
        return category

    def _rename_category(self, old_name: str, new_name: str):
        normalized = str(new_name).strip()
        if not normalized or normalized == old_name:
            return
        if normalized in self._category_names:
            QMessageBox.information(self, "Category exists", "A category with that name already exists.")
            return
        self._category_names = [normalized if name == old_name else name for name in self._category_names]
        self._section_order = [normalized if name == old_name else name for name in self._section_order]
        for webtoon in self._webtoons:
            if getattr(webtoon, "category", None) == old_name:
                self.settings_store.set_category(webtoon.name, normalized)
                webtoon.category = normalized
        self._save_custom_categories()
        self._save_section_order()
        self._rebuild_sections()

    def _delete_category(self, category: str):
        answer = QMessageBox.question(
            self,
            "Delete category",
            f"Delete '{category}'? Titles in it will move to Uncategorized.",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        self._category_names = [name for name in self._category_names if name != category]
        self._section_order = [name for name in self._section_order if name != category]
        for webtoon in self._webtoons:
            if getattr(webtoon, "category", None) == category:
                self.settings_store.clear_category(webtoon.name)
                webtoon.category = None
        self._save_custom_categories()
        self._save_section_order()
        self._rebuild_sections()

    def _drag_selection_for(self, webtoon_name: str) -> list[str]:
        if webtoon_name in self._selected_webtoons:
            return sorted(self._selected_webtoons)
        return [webtoon_name]

    def _handle_section_drop(self, section_key: str, names: list[str]) -> bool:
        if not names:
            return False
        if section_key == SECTION_UNCATEGORIZED:
            self._assign_category_to_webtoons(names, None)
            return True
        if section_key in self._category_names:
            self._assign_category_to_webtoons(names, section_key)
            return True
        return False

    def _handle_section_reorder(self, dragged_key: str, target_key: str, insert_after: bool = False) -> bool:
        if dragged_key == target_key:
            return False
        if dragged_key not in self._section_order or target_key not in self._section_order:
            return False

        ordered = [name for name in self._section_order if name != dragged_key]
        target_index = ordered.index(target_key)
        if insert_after:
            target_index += 1
        ordered.insert(target_index, dragged_key)
        self._section_order = ordered
        self._save_section_order()
        self._relayout_sections(self._current_cols or self._columns_for_width(self.width()))
        return True

    def _reconcile_section_order(self, stored_order: list[str]) -> list[str]:
        available = []
        if self._show_downloads_section():
            available.append(SECTION_DOWNLOADS)
        if self._show_new_section():
            available.append(SECTION_NEW)
        available.append(SECTION_BOOKMARKED)
        if self._categories_enabled():
            available.extend([SECTION_UNCATEGORIZED, *self._category_names])
        else:
            available.append(SECTION_LIBRARY)
        ordered = [section for section in stored_order if section in available]
        for section in available:
            if section not in ordered:
                ordered.append(section)
        return ordered

    def _assign_category_to_webtoons(self, names: list[str], category: str | None):
        if not self._categories_enabled():
            return
        normalized = self._create_category(category) if category else None
        targets = {name for name in names}
        self._block_library_input(0.25)
        self._suppress_card_open(0.25)
        for webtoon in self._webtoons:
            if webtoon.name not in targets:
                continue
            old_section = self._section_key_for_webtoon(webtoon)
            if normalized:
                self.settings_store.set_category(webtoon.name, normalized)
                webtoon.category = normalized
            else:
                self.settings_store.clear_category(webtoon.name)
                webtoon.category = None
            new_section = self._section_key_for_webtoon(webtoon)
            if old_section == new_section:
                continue
            card = self._cards_by_name.get(webtoon.name)
            if card is None:
                continue
            if old_section in self._section_cards and card in self._section_cards[old_section]:
                self._section_cards[old_section].remove(card)
            self._add_section_widget(new_section)
            self._section_cards.setdefault(new_section, []).append(card)
        self._relayout_sections(self._current_cols or self._columns_for_width(self.width()))
        self._clear_selection()

    def _show_move_selected_menu(self):
        if not self._selected_webtoons or not self._categories_enabled():
            return
        menu = QMenu(self)
        uncategorized_action = menu.addAction("Uncategorized")
        menu.addSeparator()
        actions = {}
        for category in self._category_names:
            actions[menu.addAction(category)] = category
        menu.addSeparator()
        new_category_action = menu.addAction("New Category...")
        chosen = menu.exec(self.move_selected_btn.mapToGlobal(self.move_selected_btn.rect().bottomLeft()))
        if chosen == uncategorized_action:
            self._assign_category_to_webtoons(sorted(self._selected_webtoons), None)
            return
        if chosen == new_category_action:
            name, ok = QInputDialog.getText(self, "New category", "Category name:")
            if ok:
                category = self._create_category(name)
                if category:
                    self._assign_category_to_webtoons(sorted(self._selected_webtoons), category)
            return
        category = actions.get(chosen)
        if category:
            self._assign_category_to_webtoons(sorted(self._selected_webtoons), category)

    def _open_detail(self, webtoon):
        if time.monotonic() < self._ignore_open_until:
            return
        if getattr(webtoon, "_download_placeholder", False):
            if self._manual_download_service is None:
                return
            resolved = build_webtoon_from_folder(
                load_library_path(),
                webtoon.name,
                self.settings_store,
            )
            if resolved is None:
                return
            webtoon = resolved
        logger.info("Opening detail from library card for %s", webtoon.name)
        self.main_window.open_detail(webtoon)

    def _reload_after_edit(self):
        logger.info("Reloading library after edit")
        self.load_library()
        self._apply_filter()

    def attach_update_service(self, service):
        if self._update_service is service:
            return
        logger.info("Attaching shared update service to library page")
        self._update_service = service
        self._update_service.status_changed.connect(self._on_update_status_changed)
        self._update_service.progress_changed.connect(self._on_update_progress_changed)
        self._update_service.download_started.connect(self._on_update_started)
        self._update_service.download_finished.connect(self._on_update_finished)
        self._update_service.library_changed.connect(self._on_update_library_changed)
        self._sync_update_controls()

    def attach_manual_download_service(self, service):
        if self._manual_download_service is service:
            return
        logger.info("Attaching manual download service to library page")
        self._manual_download_service = service
        service.download_started.connect(self._on_manual_download_started)
        service.name_resolved.connect(self._on_manual_download_renamed)
        service.progress_changed.connect(self._on_manual_download_progress_changed)
        service.thumbnail_resolved.connect(self._on_manual_download_thumbnail_resolved)
        service.download_finished.connect(self._on_manual_download_finished)
        service.library_changed.connect(self._on_manual_library_changed)

    def _start_update(self, webtoon_name: str):
        if self._update_service is None:
            return
        if self.settings_store.get_completed(webtoon_name):
            self._sync_update_controls()
            return
        source_url = self.settings_store.get_source_url(webtoon_name)
        if not source_url:
            return
        remaining = self._cooldown_remaining(webtoon_name)
        if remaining > 0:
            self._sync_update_controls()
            return
        error = self._update_service.start_download(
            source_url,
            load_library_path(),
            preferred_name=webtoon_name,
        )
        if error:
            logger.warning("Failed to start update for %s: %s", webtoon_name, error)
            self._sync_update_controls()
            return
        self._sync_update_controls()

    def _cooldown_remaining(self, webtoon_name: str) -> int:
        return cooldown_remaining(self.settings_store.get_last_update_at(webtoon_name))

    def _sync_update_controls(self):
        for card in self._cards:
            if getattr(card, "_download_placeholder", False):
                continue
            if card.webtoon.name in self._active_manual_downloads:
                continue
            active_update = self._active_updates.get(card.webtoon.name)
            has_source = bool(self.settings_store.get_source_url(card.webtoon.name))
            is_completed = self.settings_store.get_completed(card.webtoon.name)
            update_allowed = has_source and not is_completed
            card.set_update_available(has_source)
            if is_completed:
                card.set_update_available(False)
                card.set_update_status("Ready")
                continue
            if not update_allowed:
                continue
            if active_update is not None or (
                self._update_service is not None and self._update_service.has_active_download(card.webtoon.name)
            ):
                card.set_update_enabled(False, "Update in progress")
                card.set_update_status("Downloading")
                current = active_update.get("current", 0) if active_update is not None else 0
                total = active_update.get("total", 0) if active_update is not None else 0
                if self._update_service is not None:
                    service_current, service_total = self._update_service.get_progress(card.webtoon.name)
                    if service_total > 0:
                        current = service_current
                        total = service_total
                        self._active_updates[card.webtoon.name] = {"current": current, "total": total}
                if total > 0:
                    card.set_update_progress(current, total)
                continue
            remaining = self._cooldown_remaining(card.webtoon.name)
            if remaining > 0:
                card.set_update_enabled(
                    False,
                    f"Wait {remaining}s before updating again",
                    cooldown_text=f"{remaining}s",
                )
            else:
                card.set_update_enabled(True, "Update this webtoon")
            card.set_update_status("Ready")

    def _sync_manual_download_cards(self):
        for card in self._cards:
            state = self._active_manual_downloads.get(card.webtoon.name)
            if state is None:
                card.clear_download_progress()
                continue
            if self._manual_download_service is not None:
                service_current, service_total = self._manual_download_service.get_progress(card.webtoon.name)
                if service_total > 0:
                    state["current"] = service_current
                    state["total"] = service_total
            card.set_download_progress(state.get("current", 0), state.get("total", 0))

    def _poll_live_progress(self):
        if not self.isVisible():
            return
        self._sync_update_controls()
        self._sync_manual_download_cards()

    def _on_card_selected(self, webtoon_name: str, selected: bool):
        if selected:
            self._selected_webtoons.add(webtoon_name)
        else:
            self._selected_webtoons.discard(webtoon_name)
        self._sync_batch_actions()

    def _sync_batch_actions(self):
        count = len(self._selected_webtoons)
        self.batch_bar.setVisible(count > 0)
        self.move_selected_btn.setVisible(self._categories_enabled())
        for card in self._cards:
            if getattr(card, "_download_placeholder", False):
                continue
            card.set_selection_controls_visible(count > 0)
        if count <= 0:
            return
        self.batch_label.setText(f"{count} selected")
        all_completed = all(self.settings_store.get_completed(name) for name in self._selected_webtoons)
        self.mark_completed_btn.setText("Mark Ongoing" if all_completed else "Mark Completed")
        all_bookmarked = all(self.settings_store.get_bookmarked(name) for name in self._selected_webtoons)
        self.bookmark_selected_btn.setText("Remove Bookmark" if all_bookmarked else "Bookmark Selected")
        updatable = any(
            self.settings_store.get_source_url(name) and not self.settings_store.get_completed(name)
            for name in self._selected_webtoons
        )
        self.update_selected_btn.setEnabled(updatable)
        self.bookmark_selected_btn.setEnabled(True)
        self.move_selected_btn.setEnabled(self._categories_enabled())

    def _clear_selection(self):
        self._selected_webtoons.clear()
        for card in self._cards:
            if getattr(card, "_download_placeholder", False):
                continue
            card.set_selected(False)
        self._sync_batch_actions()

    def _prune_selection(self):
        valid_names = {webtoon.name for webtoon in self._webtoons}
        self._selected_webtoons = {name for name in self._selected_webtoons if name in valid_names}

    def _mark_selected_completed(self):
        selected = sorted(self._selected_webtoons)
        if not selected:
            return
        all_completed = all(self.settings_store.get_completed(name) for name in selected)
        for name in selected:
            self.settings_store.set_completed(name, not all_completed)
        self.load_library()
        self._apply_filter()
        self._clear_selection()

    def _toggle_selected_bookmarked(self):
        selected = sorted(self._selected_webtoons)
        if not selected:
            return
        all_bookmarked = all(self.settings_store.get_bookmarked(name) for name in selected)
        new_value = not all_bookmarked
        for name in selected:
            self.settings_store.set_bookmarked(name, new_value)
        self.load_library()
        self._apply_filter()
        self._clear_selection()

    def _update_selected(self):
        selected = [webtoon.name for webtoon in self._webtoons if webtoon.name in self._selected_webtoons]
        if not selected:
            return
        for name in selected:
            if self.settings_store.get_completed(name):
                continue
            if not self.settings_store.get_source_url(name):
                continue
            self._start_update(name)
        self._clear_selection()

    def _delete_selected(self):
        selected = sorted(self._selected_webtoons)
        if not selected:
            return
        if self._delete_webtoons(selected):
            self._clear_selection()

    def _delete_single_webtoon(self, webtoon_name: str):
        if self._delete_webtoons([webtoon_name]):
            self._selected_webtoons.discard(webtoon_name)
            self._sync_batch_actions()

    def _delete_webtoons(self, names: list[str]) -> bool:
        if not names:
            return False
        if len(names) == 1:
            message = f"Delete '{names[0]}' from the library?\n\nThis removes the folder, progress, thumbnail overrides, and saved settings."
            title = "Delete webtoon"
        else:
            message = (
                f"Delete {len(names)} webtoons from the library?\n\n"
                "This removes their folders, progress, thumbnail overrides, and saved settings."
            )
            title = "Delete selected webtoons"
        answer = QMessageBox.question(self, title, message, QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if answer != QMessageBox.Yes:
            return False
        library_path = load_library_path()
        deleted_count = 0
        for name in names:
            try:
                webtoon_path = os.path.join(library_path, name)
                if os.path.isdir(webtoon_path):
                    shutil.rmtree(webtoon_path)
                self.progress_store.clear(name)
                self.settings_store.delete_webtoon(name)
                deleted_count += 1
            except Exception as e:
                logger.error("Failed to delete webtoon %s", name, exc_info=e)
        if deleted_count <= 0:
            return False
        self.load_library()
        self._apply_filter()
        return True

    def _on_update_started(self, name: str):
        self._active_updates[name] = {"current": 0, "total": 0}
        self._sync_update_controls()

    def _on_update_finished(self, name: str, status: str):
        self._active_updates.pop(name, None)
        if status == "Completed":
            self.settings_store.set_last_update_at(name, int(time.time()))
        self._sync_update_controls()

    def _on_update_library_changed(self, name: str):
        if self.isVisible():
            self._schedule_incremental_refresh(name)
        else:
            self._pending_reload = True

    def _on_update_status_changed(self, name: str, status: str):
        card = self._card_for(name)
        if card is None:
            return
        if status != "Downloading":
            self._active_updates.pop(name, None)
        if status == "Completed":
            card.set_update_progress(1, 1)
        card.set_update_status(status)

    def _on_update_progress_changed(self, name: str, current: int, total: int):
        state = self._active_updates.setdefault(name, {"current": 0, "total": 0})
        state["current"] = max(0, int(current))
        state["total"] = max(0, int(total))
        card = self._card_for(name)
        if card is not None:
            card.set_update_progress(current, total)

    def _on_manual_download_started(self, name: str):
        self._active_manual_downloads[name] = {
            "current": 0,
            "total": 0,
            "thumbnail": "",
            "existing": self._has_webtoon(name),
        }
        if self._sync_manual_download_placeholders():
            return
        self._sync_manual_download_cards()

    def _on_manual_download_renamed(self, old_name: str, name: str):
        state = self._active_manual_downloads.pop(
            old_name,
            {"current": 0, "total": 0, "thumbnail": "", "existing": False},
        )
        state["existing"] = bool(state.get("existing")) or self._has_webtoon(name)
        self._active_manual_downloads[name] = state
        if self._sync_manual_download_placeholders():
            return
        old_card = self._card_for(old_name)
        if old_card is not None:
            old_card.clear_download_progress()
        card = self._card_for(name)
        if card is not None:
            card.set_download_progress(state["current"], state["total"])
            if state.get("thumbnail"):
                card.set_thumbnail(state["thumbnail"])

    def _on_manual_download_progress_changed(self, name: str, current: int, total: int):
        state = self._active_manual_downloads.setdefault(
            name,
            {"current": 0, "total": 0, "thumbnail": "", "existing": self._has_webtoon(name)},
        )
        state["current"] = max(0, int(current))
        state["total"] = max(0, int(total))
        card = self._card_for(name)
        if card is not None:
            card.set_download_progress(state["current"], state["total"])

    def _on_manual_download_thumbnail_resolved(self, name: str, path: str):
        state = self._active_manual_downloads.setdefault(
            name,
            {"current": 0, "total": 0, "thumbnail": "", "existing": self._has_webtoon(name)},
        )
        state["thumbnail"] = path or ""
        if self._sync_manual_download_placeholders():
            return
        card = self._card_for(name)
        if card is not None:
            card.set_thumbnail(path)

    def _on_manual_download_finished(self, name: str, status: str):
        self._active_manual_downloads.pop(name, None)
        partial_webtoon = build_webtoon_from_folder(load_library_path(), name, self.settings_store)
        if status == "Completed" or partial_webtoon is not None:
            if self.isVisible():
                self.load_library()
            else:
                self._pending_reload = True
            return
        if self._sync_manual_download_placeholders():
            return
        card = self._card_for(name)
        if card is not None:
            card.clear_download_progress()

    def _on_manual_library_changed(self, name: str):
        if not self.isVisible():
            self._pending_reload = True
            return
        if not self._has_webtoon(name):
            return
        self._schedule_incremental_refresh(name)

    def _cancel_manual_download(self, webtoon_name: str):
        if self._manual_download_service is None:
            return
        self._manual_download_service.cancel_download(webtoon_name)

    def _card_for(self, webtoon_name: str) -> WebtoonCard | None:
        return self._cards_by_name.get(webtoon_name)

    def _refresh_updated_webtoon(self, webtoon_name: str, service=None) -> bool:
        service = service or self._update_service
        if service is None:
            return False
        if self._should_hide_in_progress_manual_download(webtoon_name):
            return False
        updated = service.build_webtoon_from_folder(load_library_path(), webtoon_name)
        if updated is None:
            updated = build_webtoon_from_folder(load_library_path(), webtoon_name, self.settings_store)
        if updated is None:
            return False
        for index, webtoon in enumerate(self._webtoons):
            if webtoon.name != webtoon_name:
                continue
            self._webtoons[index] = updated
            card = self._card_for(webtoon_name)
            if card is not None:
                card.refresh_webtoon(updated)
            self._sync_update_controls()
            return True
        return False

    def _schedule_incremental_refresh(self, webtoon_name: str):
        if webtoon_name:
            self._pending_incremental_refresh_names.add(webtoon_name)
        self._sync_update_controls()
        self._sync_manual_download_cards()
        if not self._incremental_refresh_timer.isActive():
            self._incremental_refresh_timer.start(150)

    def _flush_incremental_refreshes(self):
        pending = list(self._pending_incremental_refresh_names)
        self._pending_incremental_refresh_names.clear()
        for webtoon_name in pending:
            service = self._manual_download_service if webtoon_name in self._active_manual_downloads else self._update_service
            if self._refresh_updated_webtoon(webtoon_name, service=service):
                continue
            self.load_library()
            break

    def _has_webtoon(self, webtoon_name: str) -> bool:
        return any(webtoon.name == webtoon_name for webtoon in self._webtoons)

    def _active_placeholder_names(self) -> set[str]:
        return {
            card.webtoon.name
            for card in self._cards
            if getattr(card, "_download_placeholder", False)
        }

    def _expected_placeholder_names(self) -> set[str]:
        if not self._show_downloads_section():
            return set()
        return {
            name
            for name, state in self._active_manual_downloads.items()
            if not bool(state.get("existing"))
        }

    def _sync_manual_download_placeholders(self) -> bool:
        if self._active_placeholder_names() == self._expected_placeholder_names():
            return False
        self._rebuild_sections()
        return True

    def _should_hide_in_progress_manual_download(self, webtoon_name: str) -> bool:
        state = self._active_manual_downloads.get(webtoon_name)
        if not state:
            return False
        return not bool(state.get("existing"))

    def _filter_in_progress_manual_downloads(self, webtoons):
        return [
            webtoon
            for webtoon in webtoons
            if not self._should_hide_in_progress_manual_download(webtoon.name)
        ]

    def _suppress_card_open(self, seconds: float):
        self._ignore_open_until = max(self._ignore_open_until, time.monotonic() + seconds)

    def _block_library_input(self, seconds: float):
        self._block_input_until = max(self._block_input_until, time.monotonic() + seconds)
        self._input_blocker.setGeometry(self.rect())
        self._input_blocker.show()
        self._input_blocker.raise_()
        QTimer.singleShot(int(seconds * 1000) + 50, self._release_library_input_if_due)

    def _release_library_input_if_due(self):
        if time.monotonic() < self._block_input_until:
            QTimer.singleShot(100, self._release_library_input_if_due)
            return
        if QApplication.mouseButtons() != Qt.NoButton:
            QTimer.singleShot(100, self._release_library_input_if_due)
            return
        self._input_blocker.hide()

    def _schedule_filter(self, text):
        self._pending_search = text
        self._search_timer.start(150)

    def _on_size_slider_changed(self, value: int):
        value = max(CARD_SCALE_MIN, min(CARD_SCALE_MAX, value))
        self._pending_card_scale = value
        self.size_value_label.setText(f"{value}%")
        if value == self._card_scale:
            self._scale_timer.stop()
            save_setting(CARD_SCALE_KEY, value)
            return
        if self.size_slider.isSliderDown():
            self._scale_timer.start(24)
            return
        self._scale_timer.start(16)

    def _apply_pending_card_scale(self):
        value = max(CARD_SCALE_MIN, min(CARD_SCALE_MAX, self._pending_card_scale))
        if value == self._card_scale:
            self.size_value_label.setText(f"{value}%")
            save_setting(CARD_SCALE_KEY, value)
            return
        self._card_scale = value
        self.size_value_label.setText(f"{value}%")
        save_setting(CARD_SCALE_KEY, value)
        self._relayout_sections(self._columns_for_width(self.width()))

    def _apply_filter(self):
        self._relayout_sections(self._current_cols or self._columns_for_width(self.width()))

    def _categories_enabled(self) -> bool:
        return bool(load_setting(LIBRARY_USE_CATEGORIES_KEY, True))

    def _show_new_section(self) -> bool:
        return bool(load_setting(LIBRARY_SHOW_NEW_SECTION_KEY, True))

    def _show_downloads_section(self) -> bool:
        return bool(load_setting(LIBRARY_SHOW_DOWNLOADS_SECTION_KEY, True))
