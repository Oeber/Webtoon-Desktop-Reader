import os
import re
import time

from app_logging import get_logger
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QToolButton, QMessageBox
)
from PySide6.QtGui import QIcon, QPixmap, QPainter, QPainterPath, QFont, QPen, QColor
from PySide6.QtCore import Qt, QPoint, QSize, QTimer

import qtawesome as qta

from gui.common.chapter_utils import SPECIAL_CHAPTER_RE, chapter_sort_key
from gui.common.styles import CHAPTER_SCROLL_AREA_STYLE, PAGE_BG_STYLE
from gui.downloader.download_widgets import SpinnerCircle
from webtoon_settings_store import get_instance as get_webtoon_settings
from gui.library.edit_webtoon_dialog import EditWebtoonDialog
from gui.settings.settings_page import load_library_path

UPDATE_COOLDOWN_SECONDS = 30
logger = get_logger(__name__)


THUMB_W = 140
THUMB_H = 210
RADIUS  = 12
ACTION_BTN_H = 36
ACTION_BTN_W = 168
SUPPORTED_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".avif")


# ── Small circular progress indicator ────────────────────────────────────────
class ProgressCircle(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._percent = 0
        self.setFixedSize(32, 32)

    def set_percent(self, percent: int):
        self._percent = max(0, min(100, int(percent)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = self.rect().adjusted(2, 2, -2, -2)

        # Background ring
        painter.setPen(QPen(QColor("#333333"), 3))
        painter.drawEllipse(rect)

        # Progress arc (green)
        if self._percent > 0:
            pen = QPen(QColor("#22c55e"), 3)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            start_angle = -90 * 16
            span_angle = int(self._percent / 100.0 * 360 * 16)
            painter.drawArc(rect, start_angle, span_angle)

        # Center text
        font = painter.font()
        font.setPixelSize(9)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#e0e0e0"))
        painter.drawText(rect, Qt.AlignCenter, f"{self._percent}%")


class DetailPage(QWidget):

    def __init__(self, main_window):
        super().__init__()
        self.main_window    = main_window
        self.webtoon        = None
        self.progress_store = None
        self.progress_map   = {}
        self.hide_specials  = False
        self.show_only_bookmarked = False
        self.bookmarked_chapters = set()
        self.selected_chapters = set()
        self.latest_new_chapter = None
        self.webtoon_bookmarked = False
        self.settings_store = get_webtoon_settings()
        self._update_service = None
        self._chapter_display_order = []
        self._update_progress_current = 0
        self._update_progress_total = 0
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self._sync_update_button)
        self._update_timer.start(1000)

        self.setStyleSheet(PAGE_BG_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Top bar ──────────────────────────────────────────────────────
        top_bar = QWidget()
        top_bar.setFixedHeight(52)
        top_bar.setStyleSheet("background-color: #181818; border-bottom: 1px solid #222;")
        tb_layout = QHBoxLayout(top_bar)
        tb_layout.setContentsMargins(16, 0, 16, 0)

        self.back_btn = QPushButton("  Back")
        self.back_btn.setIcon(qta.icon("fa5s.arrow-left", color="#aaaaaa"))
        self.back_btn.setIconSize(QSize(14, 14))
        self.back_btn.setCursor(Qt.PointingHandCursor)
        self.back_btn.setStyleSheet("""
            QPushButton { background: transparent; color: #aaa; border: none; font-size: 14px; }
            QPushButton:hover { color: #fff; }
        """)
        self.back_btn.clicked.connect(self._go_back)

        self.edit_btn = QPushButton("  Edit")
        self.edit_btn.setIcon(qta.icon("fa5s.edit", color="#aaaaaa"))
        self.edit_btn.setIconSize(QSize(14, 14))
        self.edit_btn.setCursor(Qt.PointingHandCursor)
        self.edit_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #aaa;
                border: none;
                font-size: 14px;
            }
            QPushButton:hover { color: #fff; }
        """)
        self.edit_btn.clicked.connect(self._open_edit_dialog)

        self.bookmark_btn = QPushButton("  Bookmark")
        self.bookmark_btn.setIconSize(QSize(14, 14))
        self.bookmark_btn.setCursor(Qt.PointingHandCursor)
        self.bookmark_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #aaa;
                border: none;
                font-size: 14px;
            }
            QPushButton:hover { color: #fff; }
        """)
        self.bookmark_btn.clicked.connect(self._toggle_webtoon_bookmark)

        tb_layout.addWidget(self.back_btn)
        tb_layout.addStretch()
        tb_layout.addWidget(self.bookmark_btn)
        tb_layout.addWidget(self.edit_btn)
        root.addWidget(top_bar)

        # ── Hero ─────────────────────────────────────────────────────────
        hero = QWidget()
        hero.setStyleSheet("background-color: #181818;")
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(32, 28, 32, 28)
        hero_layout.setSpacing(28)

        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(THUMB_W, THUMB_H)
        self.thumb_label.setStyleSheet(f"""
            QLabel {{
                background: #1e1e1e;
                border-radius: {RADIUS}px;
                border: 1px solid #2a2a2a;
            }}
        """)

        info_widget = QWidget()
        info_widget.setStyleSheet("background: transparent;")
        info_layout = QVBoxLayout(info_widget)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(10)
        info_layout.setAlignment(Qt.AlignTop)

        self.title_label = QLabel()
        self.title_label.setWordWrap(False)
        self.title_label.setStyleSheet("color: #fff; font-size: 28px; font-weight: 700;")
        self.title_label.setFont(QFont("Segoe UI", 24, QFont.Bold))
        self.title_label.setMinimumHeight(36)

        self.last_read_label = QLabel()
        self.last_read_label.setStyleSheet("color: #aaa; font-size: 13px;")

        self.chapter_count_label = QLabel()
        self.chapter_count_label.setStyleSheet("color: #888; font-size: 12px;")

        self.update_progress_label = QLabel("")
        self.update_progress_label.setStyleSheet("color: #f0a500; font-size: 12px; font-weight: 600;")
        self.update_progress_label.hide()

        self.update_progress_circle = ProgressCircle()
        self.update_progress_circle.hide()

        self.continue_btn = QPushButton("  Continue reading")
        self.continue_btn.setIcon(qta.icon("fa5s.play", color="#ffffff"))
        self.continue_btn.setIconSize(QSize(12, 12))
        self.continue_btn.setCursor(Qt.PointingHandCursor)
        self.continue_btn.setFixedSize(ACTION_BTN_W, ACTION_BTN_H)
        self.continue_btn.setStyleSheet("""
            QPushButton { background: #2979ff; color: #fff; border: none; border-radius: 6px;
                          font-size: 13px; font-weight: 600; }
            QPushButton:hover { background: #448aff; }
        """)
        self.continue_btn.clicked.connect(self._continue_reading)
        self.continue_btn.hide()

        self.start_btn = QPushButton("  Start from beginning")
        self.start_btn.setIcon(qta.icon("fa5s.step-backward", color="#ffffff"))
        self.start_btn.setIconSize(QSize(12, 12))
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.setFixedSize(ACTION_BTN_W, ACTION_BTN_H)
        self.start_btn.setStyleSheet("""
            QPushButton { background: #2979ff; color: #fff; border: none; border-radius: 6px;
                        font-size: 13px; font-weight: 600; }
            QPushButton:hover { background: #448aff; }
        """)
        self.start_btn.clicked.connect(self._start_from_beginning)

        self.update_btn = QPushButton("  Update")
        self.update_btn.setIcon(qta.icon("fa5s.sync", color="#ffffff"))
        self.update_btn.setIconSize(QSize(12, 12))
        self.update_btn.setCursor(Qt.PointingHandCursor)
        self.update_btn.setFixedSize(ACTION_BTN_W, ACTION_BTN_H)
        self.update_btn.setStyleSheet("""
            QPushButton { background: #2a2a2a; color: #fff; border: none; border-radius: 6px;
                          font-size: 13px; font-weight: 600; }
            QPushButton:hover { background: #333333; }
            QPushButton:disabled { background: #232323; color: #777; }
        """)
        self.update_btn.clicked.connect(self._start_update)
        self.update_btn.hide()

        info_layout.addWidget(self.title_label)
        info_layout.addWidget(self.last_read_label)
        info_layout.addWidget(self.chapter_count_label)
        progress_row = QHBoxLayout()
        progress_row.setContentsMargins(0, 0, 0, 0)
        progress_row.setSpacing(8)
        progress_row.addWidget(self.update_progress_circle, 0, Qt.AlignVCenter)
        progress_row.addWidget(self.update_progress_label, 0, Qt.AlignVCenter)
        progress_row.addStretch()
        info_layout.addLayout(progress_row)
        info_layout.addSpacing(12)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addWidget(self.continue_btn)
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.update_btn)
        btn_row.addStretch()
        info_layout.addLayout(btn_row)

        hero_layout.addWidget(self.thumb_label)
        hero_layout.addWidget(info_widget, 1)
        root.addWidget(hero)

        # ── Section header ───────────────────────────────────────────────
        section_header = QWidget()
        section_header.setStyleSheet("background: #121212;")

        sh_layout = QHBoxLayout(section_header)
        sh_layout.setContentsMargins(32, 20, 32, 8)

        chapters_lbl = QLabel("CHAPTERS")
        chapters_lbl.setStyleSheet(
            "color: #555; font-size: 11px; font-weight: 700; letter-spacing: 2px;"
        )

        self.sort_btn = QPushButton("  Latest")
        self.sort_btn.setIcon(qta.icon("fa5s.sort-amount-down", color="#888888"))
        self.sort_btn.setIconSize(QSize(12, 12))
        self.sort_btn.setCursor(Qt.PointingHandCursor)
        self.sort_btn.setFixedHeight(24)
        self.sort_btn.setStyleSheet("""
        QPushButton {
            background: transparent;
            color: #888;
            border: 1px solid #2a2a2a;
            border-radius: 4px;
            padding: 2px 8px;
            font-size: 11px;
        }
        QPushButton:hover {
            background: #1a1a1a;
            color: #fff;
        }
        """)
        self.sort_latest_first = True
        self.sort_btn.clicked.connect(self._toggle_sort)

        self.hide_specials_btn = QPushButton("  Hide Filler")
        self.hide_specials_btn.setIcon(qta.icon("fa5s.eye-slash", color="#888888"))
        self.hide_specials_btn.setIconSize(QSize(12, 12))
        self.hide_specials_btn.setCursor(Qt.PointingHandCursor)
        self.hide_specials_btn.setCheckable(True)
        self.hide_specials_btn.setFixedHeight(24)
        self.hide_specials_btn.setStyleSheet("""
        QPushButton {
            background: transparent;
            color: #888;
            border: 1px solid #2a2a2a;
            border-radius: 4px;
            padding: 2px 8px;
            font-size: 11px;
        }
        QPushButton:hover {
            background: #1a1a1a;
            color: #fff;
        }
        QPushButton:checked {
            background: #1a2a3a;
            color: #2979ff;
            border-color: #2979ff;
        }
        """)
        self.hide_specials_btn.clicked.connect(self._toggle_hide_specials)

        self.bookmarks_filter_btn = QPushButton("  Bookmarked")
        self.bookmarks_filter_btn.setIcon(qta.icon("fa5s.star", color="#888888"))
        self.bookmarks_filter_btn.setIconSize(QSize(12, 12))
        self.bookmarks_filter_btn.setCursor(Qt.PointingHandCursor)
        self.bookmarks_filter_btn.setCheckable(True)
        self.bookmarks_filter_btn.setFixedHeight(24)
        self.bookmarks_filter_btn.setStyleSheet("""
        QPushButton {
            background: transparent;
            color: #888;
            border: 1px solid #2a2a2a;
            border-radius: 4px;
            padding: 2px 8px;
            font-size: 11px;
        }
        QPushButton:hover {
            background: #1a1a1a;
            color: #fff;
        }
        QPushButton:checked {
            background: #2f2815;
            color: #f5c451;
            border-color: #f5c451;
        }
        """)
        self.bookmarks_filter_btn.clicked.connect(self._toggle_bookmarks_filter)

        sh_layout.addWidget(chapters_lbl)
        sh_layout.addStretch()
        sh_layout.addWidget(self.bookmarks_filter_btn)
        sh_layout.addSpacing(6)
        sh_layout.addWidget(self.hide_specials_btn)
        sh_layout.addSpacing(6)
        sh_layout.addWidget(self.sort_btn)
        root.addWidget(section_header)

        self.chapter_batch_bar = QWidget()
        self.chapter_batch_bar.setStyleSheet("""
            QWidget {
                background: #171717;
                border-top: 1px solid #242424;
                border-bottom: 1px solid #242424;
            }
        """)
        batch_layout = QHBoxLayout(self.chapter_batch_bar)
        batch_layout.setContentsMargins(32, 10, 32, 10)
        batch_layout.setSpacing(10)

        self.chapter_batch_label = QLabel("")
        self.chapter_batch_label.setStyleSheet("color: #d0d0d0; font-size: 12px;")
        batch_layout.addWidget(self.chapter_batch_label)

        chapter_batch_btn_style = """
            QPushButton {
                background: #2a2a2a;
                color: #f0f0f0;
                border: 1px solid #343434;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover { background: #333333; }
        """
        self.select_all_chapters_btn = QPushButton("Select All")
        self.select_all_chapters_btn.setStyleSheet(chapter_batch_btn_style)
        self.select_all_chapters_btn.clicked.connect(self._select_all_chapters)
        batch_layout.addWidget(self.select_all_chapters_btn)

        self.mark_read_btn = QPushButton("Mark Read")
        self.mark_read_btn.setStyleSheet(chapter_batch_btn_style)
        self.mark_read_btn.clicked.connect(self._mark_selected_chapters_read)
        batch_layout.addWidget(self.mark_read_btn)

        self.mark_unread_btn = QPushButton("Mark Unread")
        self.mark_unread_btn.setStyleSheet(chapter_batch_btn_style)
        self.mark_unread_btn.clicked.connect(self._mark_selected_chapters_unread)
        batch_layout.addWidget(self.mark_unread_btn)

        self.delete_chapters_btn = QPushButton("Delete")
        self.delete_chapters_btn.setStyleSheet("""
            QPushButton {
                background: #4a1f1f;
                color: #ffffff;
                border: 1px solid #703030;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover { background: #5a2727; }
        """)
        self.delete_chapters_btn.clicked.connect(self._delete_selected_chapters)
        batch_layout.addWidget(self.delete_chapters_btn)

        self.clear_chapter_selection_btn = QPushButton("Clear")
        self.clear_chapter_selection_btn.setStyleSheet(chapter_batch_btn_style)
        self.clear_chapter_selection_btn.clicked.connect(self._clear_chapter_selection)
        batch_layout.addWidget(self.clear_chapter_selection_btn)
        batch_layout.addStretch()

        self.chapter_batch_bar.hide()

        # ── Chapter list ─────────────────────────────────────────────────
        self.chapter_scroll = QScrollArea()
        self.chapter_scroll.setWidgetResizable(True)
        self.chapter_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chapter_scroll.setStyleSheet(CHAPTER_SCROLL_AREA_STYLE)

        self.chapter_list_widget = QWidget()
        self.chapter_list_widget.setStyleSheet("background: #121212;")
        self.chapter_list_layout = QVBoxLayout(self.chapter_list_widget)
        self.chapter_list_layout.setContentsMargins(32, 0, 32, 24)
        self.chapter_list_layout.setSpacing(0)
        self.chapter_list_layout.setAlignment(Qt.AlignTop)

        self.chapter_scroll.setWidget(self.chapter_list_widget)
        root.addWidget(self.chapter_scroll, 1)
        root.addWidget(self.chapter_batch_bar)

    def _chapter_selection_visible(self) -> bool:
        return bool(self.selected_chapters)

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def load_webtoon(self, webtoon, progress_store):
        logger.info("Loading detail page for %s", webtoon.name)
        self.webtoon        = webtoon
        self.progress_store = progress_store
        self.progress_map   = progress_store.get_progress_map(webtoon.name)
        self.bookmarked_chapters = self.settings_store.get_bookmarked_chapters(webtoon.name)
        self.selected_chapters = set()
        self.latest_new_chapter = self.settings_store.get_latest_new_chapter(webtoon.name)
        self.webtoon_bookmarked = self.settings_store.get_bookmarked(webtoon.name)
        self._chapter_display_order = self._ordered_chapters_for_display(webtoon.chapters)
        self._update_progress_current = 0
        self._update_progress_total = 0
        self.show_only_bookmarked = False
        self.bookmarks_filter_btn.setChecked(False)

        # Restore per-webtoon hide-filler setting
        self.hide_specials = self.settings_store.get_hide_filler(webtoon.name)
        self.hide_specials_btn.setChecked(self.hide_specials)
        icon_name = "fa5s.eye-slash" if self.hide_specials else "fa5s.eye"
        self.hide_specials_btn.setIcon(qta.icon(icon_name, color="#888888"))
        self.hide_specials_btn.setIconSize(QSize(12, 12))

        self.title_label.setText(webtoon.name)
        self._sync_webtoon_bookmark_button()
        self._update_chapter_count_label()
        self._sync_update_button()

        # Thumbnail
        pixmap = QPixmap(webtoon.thumbnail)
        if not pixmap.isNull():
            pixmap = pixmap.scaled(THUMB_W, THUMB_H,
                                   Qt.KeepAspectRatioByExpanding,
                                   Qt.SmoothTransformation)
            x = (pixmap.width()  - THUMB_W) // 2
            y = (pixmap.height() - THUMB_H) // 2
            pixmap = pixmap.copy(x, y, THUMB_W, THUMB_H)

            rounded = QPixmap(THUMB_W, THUMB_H)
            rounded.fill(Qt.transparent)
            p = QPainter(rounded)
            p.setRenderHint(QPainter.Antialiasing)
            path = QPainterPath()
            path.addRoundedRect(0, 0, THUMB_W, THUMB_H, RADIUS, RADIUS)
            p.setClipPath(path)
            p.drawPixmap(0, 0, pixmap)
            p.end()
            self.thumb_label.setPixmap(rounded)

        # Last read label
        progress = progress_store.get(webtoon.name)
        if progress:
            ch = progress["chapter"]
            scroll, total = self.progress_map.get(ch, (0.0, 0))
            percent = self._calc_percent(scroll, total)
            self.last_read_label.setText(f"Last read: {ch} ({percent}%)")
            self.continue_btn.show()
        else:
            self.last_read_label.setText("Not started")
            self.continue_btn.hide()

        self._build_chapter_list(progress)
        self._sync_chapter_batch_actions()

    def _calc_percent(self, scroll: float, total_images: int) -> int:
        if total_images <= 0:
            return 0
        # sentinel: viewer saves scroll == total_images when scrollbar is at max
        if scroll >= total_images:
            return 100
        return min(99, int((scroll / total_images) * 100))

    def _build_chapter_list(self, progress):
        while self.chapter_list_layout.count():
            item = self.chapter_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        last_read_chapter = progress["chapter"] if progress else None
        chapters = list(self._chapter_display_order or self.webtoon.chapters)

        if self.hide_specials:
            chapters = [c for c in chapters if not SPECIAL_CHAPTER_RE.search(c)]
        if self.show_only_bookmarked:
            chapters = [c for c in chapters if c in self.bookmarked_chapters]

        for chapter in chapters:
            data = self.progress_map.get(chapter, (0.0, 0))
            scroll, total = data
            is_last_read = (chapter == last_read_chapter)
            row = self._make_chapter_row(chapter, is_last_read, scroll, total)
            self.chapter_list_layout.addWidget(row)

    def _make_chapter_row(self, chapter: str, is_last_read: bool, scroll: float, total: int) -> QWidget:
        row = QWidget()
        row.setCursor(Qt.PointingHandCursor)
        row.setStyleSheet("""
            QWidget { background: transparent; border-bottom: 1px solid #1e1e1e; }
            QWidget:hover { background: #1a1a1a; }
        """)
        row.setFixedHeight(52)

        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(12)

        select_slot = QWidget()
        select_slot.setFixedWidth(22)
        select_slot.setStyleSheet("background: transparent; border: none;")
        select_slot_layout = QHBoxLayout(select_slot)
        select_slot_layout.setContentsMargins(0, 0, 0, 0)
        select_slot_layout.setSpacing(0)

        select_btn = QToolButton()
        select_btn.setCursor(Qt.PointingHandCursor)
        select_btn.setAutoRaise(True)
        select_btn.setCheckable(True)
        select_btn.setChecked(chapter in self.selected_chapters)
        select_btn.setIconSize(QSize(14, 14))
        select_btn.setStyleSheet("""
            QToolButton {
                border: none;
                padding: 4px;
                background: transparent;
            }
            QToolButton:hover {
                background: #222222;
                border-radius: 8px;
            }
        """)
        select_btn.setProperty("chapter_name", chapter)
        self._apply_select_icon(select_btn, select_btn.isChecked())
        self._set_chapter_select_visibility(row, select_btn, force=self._chapter_selection_visible())
        select_btn.clicked.connect(
            lambda checked, ch=chapter, btn=select_btn: self._toggle_chapter_selected(ch, btn, checked)
        )
        select_slot_layout.addWidget(select_btn, 0, Qt.AlignCenter)
        layout.addWidget(select_slot)

        color = "#2979ff" if is_last_read else "#cccccc"
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)

        name_lbl = QLabel(chapter)
        name_lbl.setStyleSheet(f"color: {color}; font-size: 14px; border: none;")
        title_row.addWidget(name_lbl)

        if chapter == self.latest_new_chapter:
            new_chip = QLabel("NEW")
            new_chip.setStyleSheet("""
                QLabel {
                    color: #ffffff;
                    background: #c62828;
                    border: 1px solid #e53935;
                    border-radius: 6px;
                    padding: 0 5px;
                    font-size: 8px;
                    font-weight: 700;
                }
            """)
            new_chip.setAlignment(Qt.AlignCenter)
            new_chip.setFixedHeight(14)
            title_row.addWidget(new_chip)

        title_row.addStretch()
        layout.addLayout(title_row, 1)

        bookmark_btn = QToolButton()
        bookmark_btn.setCursor(Qt.PointingHandCursor)
        bookmark_btn.setAutoRaise(True)
        bookmark_btn.setCheckable(True)
        bookmark_btn.setChecked(chapter in self.bookmarked_chapters)
        bookmark_btn.setIconSize(QSize(14, 14))
        bookmark_btn.setStyleSheet("""
            QToolButton {
                border: none;
                padding: 4px;
                background: transparent;
            }
            QToolButton:hover {
                background: #222222;
                border-radius: 8px;
            }
        """)
        self._apply_bookmark_icon(bookmark_btn, bookmark_btn.isChecked())
        bookmark_btn.clicked.connect(
            lambda checked, ch=chapter, btn=bookmark_btn: self._toggle_chapter_bookmark(ch, btn)
        )

        # ── Last-read bookmark icon (new) ─────────────────────────────────

        # ── Progress circle ───────────────────────────────────────────────
        percent = self._calc_percent(scroll, total)
        if percent > 0:
            circle = ProgressCircle()
            circle.set_percent(percent)
            layout.addWidget(circle)

        if is_last_read:
            last_read_icon = QLabel()
            last_read_icon.setPixmap(qta.icon("fa5s.bookmark", color="#2979ff").pixmap(QSize(14, 14)))
            last_read_icon.setStyleSheet("padding-right: 4px;")
            layout.addWidget(last_read_icon)

        layout.addWidget(bookmark_btn)

        row.enterEvent = lambda event, btn=select_btn, widget=row: self._on_chapter_row_hover(widget, btn, True, event)
        row.leaveEvent = lambda event, btn=select_btn, widget=row: self._on_chapter_row_hover(widget, btn, False, event)
        row.mousePressEvent = lambda e, ch=chapter: self._open_chapter(ch)
        return row

    # ------------------------------------------------------------------ #
    #  Actions                                                             #
    # ------------------------------------------------------------------ #

    def _visible_chapters_count(self, chapters: list) -> int:
        """Count chapters that are not special (.5-style) chapters."""
        return sum(1 for c in chapters if not SPECIAL_CHAPTER_RE.search(c))

    def _filtered_chapters(self) -> list[str]:
        if self.webtoon is None:
            return []

        chapters = list(self._chapter_display_order or self.webtoon.chapters)
        if self.hide_specials:
            chapters = [c for c in chapters if not SPECIAL_CHAPTER_RE.search(c)]
        if self.show_only_bookmarked:
            chapters = [c for c in chapters if c in self.bookmarked_chapters]
        return chapters

    def _update_chapter_count_label(self):
        if self.webtoon is None:
            self.chapter_count_label.clear()
            return

        total_count = len(self.webtoon.chapters)
        visible_count = len(self._filtered_chapters())
        hidden_specials = total_count - self._visible_chapters_count(self.webtoon.chapters)

        if self.show_only_bookmarked:
            parts = [f"{visible_count} chapters shown"]
        elif self.hide_specials and hidden_specials > 0:
            parts = [f"{visible_count} chapters"]
        else:
            parts = [f"{total_count} chapters"]
        if self.hide_specials and hidden_specials > 0:
            parts.append(f"{hidden_specials} special hidden")
        if self.show_only_bookmarked:
            parts.append(f"{len(self.bookmarked_chapters)} bookmarked")

        self.chapter_count_label.setText(" | ".join(parts))

    def _sync_webtoon_bookmark_button(self):
        if self.webtoon_bookmarked:
            self.bookmark_btn.setText("  Bookmarked")
        else:
            self.bookmark_btn.setText("  Bookmark")
        color = "#f5c451" if self.webtoon_bookmarked else "#aaaaaa"
        self.bookmark_btn.setIcon(qta.icon("fa5s.star", color=color))

    def _toggle_webtoon_bookmark(self):
        if self.webtoon is None:
            return
        self.webtoon_bookmarked = self.settings_store.toggle_bookmarked(self.webtoon.name)
        self.webtoon.is_bookmarked = self.webtoon_bookmarked
        logger.info("Detail page toggled webtoon bookmark for %s to %s", self.webtoon.name, self.webtoon_bookmarked)
        self._sync_webtoon_bookmark_button()
        self.main_window.library.refresh_dynamic_state()

    def _apply_bookmark_icon(self, button: QToolButton, is_bookmarked: bool):
        color = "#f5c451" if is_bookmarked else "#666666"
        button.setIcon(qta.icon("fa5s.star", color=color))

    def _apply_select_icon(self, button: QToolButton, is_selected: bool):
        color = "#2979ff" if is_selected else "#666666"
        icon_name = "fa5s.check-circle" if is_selected else "fa5s.circle"
        button.setIcon(qta.icon(icon_name, color=color))

    def _set_chapter_select_visibility(self, row: QWidget, button: QToolButton, force: bool = False):
        show_checkbox = force or button.isChecked() or row.underMouse()
        if show_checkbox:
            self._apply_select_icon(button, button.isChecked())
            button.setEnabled(True)
            button.setCursor(Qt.PointingHandCursor)
        else:
            button.setIcon(QIcon())
            button.setEnabled(False)
            button.setCursor(Qt.ArrowCursor)

    def _on_chapter_row_hover(self, row: QWidget, button: QToolButton, hovered: bool, event):
        self._set_chapter_select_visibility(row, button, force=self._chapter_selection_visible() or hovered)
        QWidget.enterEvent(row, event) if hovered else QWidget.leaveEvent(row, event)

    def _refresh_chapter_selection_visibility(self):
        force = self._chapter_selection_visible()
        for button in self.chapter_list_widget.findChildren(QToolButton):
            if button.property("chapter_name") is None:
                continue
            row = button.parentWidget()
            if row is None:
                continue
            self._set_chapter_select_visibility(row, button, force=force)

    def _toggle_chapter_selected(self, chapter: str, button: QToolButton, is_selected: bool):
        if is_selected:
            self.selected_chapters.add(chapter)
        else:
            self.selected_chapters.discard(chapter)
        self._apply_select_icon(button, is_selected)
        self._sync_chapter_batch_actions()
        self._refresh_chapter_selection_visibility()

    def _sync_chapter_batch_actions(self):
        count = len(self.selected_chapters)
        self.chapter_batch_bar.setVisible(count > 0)
        self._refresh_chapter_selection_visibility()
        if count <= 0:
            return
        self.chapter_batch_label.setText(f"{count} chapters selected")

    def _select_all_chapters(self):
        self.selected_chapters = set(self._filtered_chapters())
        progress = self.progress_store.get(self.webtoon.name) if self.webtoon and self.progress_store else None
        self._build_chapter_list(progress)
        self._sync_chapter_batch_actions()

    def _toggle_chapter_bookmark(self, chapter: str, button: QToolButton):
        if self.webtoon is None:
            return

        is_bookmarked = self.settings_store.toggle_bookmarked_chapter(self.webtoon.name, chapter)
        logger.info(
            "Bookmark toggled for %s chapter=%s bookmarked=%s",
            self.webtoon.name,
            chapter,
            is_bookmarked,
        )
        if is_bookmarked:
            self.bookmarked_chapters.add(chapter)
        else:
            self.bookmarked_chapters.discard(chapter)

        button.blockSignals(True)
        button.setChecked(is_bookmarked)
        button.blockSignals(False)
        self._apply_bookmark_icon(button, is_bookmarked)
        self._update_chapter_count_label()

        if self.show_only_bookmarked:
            progress = self.progress_store.get(self.webtoon.name)
            self._build_chapter_list(progress)

    def _clear_chapter_selection(self):
        self.selected_chapters.clear()
        progress = self.progress_store.get(self.webtoon.name) if self.webtoon else None
        self._build_chapter_list(progress)
        self._sync_chapter_batch_actions()

    def _chapter_total_images(self, chapter: str) -> int:
        scroll, total = self.progress_map.get(chapter, (0.0, 0))
        if total > 0:
            return total
        if self.webtoon is None:
            return 0
        chapter_path = os.path.join(self.webtoon.path, chapter)
        if not os.path.isdir(chapter_path):
            return 0
        return sum(
            1 for filename in os.listdir(chapter_path)
            if os.path.isfile(os.path.join(chapter_path, filename))
            and filename.lower().endswith(SUPPORTED_IMAGE_EXTENSIONS)
        )

    def _mark_selected_chapters_read(self):
        if self.webtoon is None or self.progress_store is None or not self.selected_chapters:
            return
        entries = []
        for chapter in sorted(self.selected_chapters, key=chapter_sort_key):
            total = self._chapter_total_images(chapter)
            entries.append((chapter, float(total), total))
        self.progress_store.save_many(self.webtoon.name, entries)
        for chapter, scroll, total in entries:
            self.progress_map[chapter] = (float(total), total)
        self.latest_new_chapter = self.settings_store.get_latest_new_chapter(self.webtoon.name)
        self.selected_chapters.clear()
        progress = self.progress_store.get(self.webtoon.name)
        self._build_chapter_list(progress)
        self._sync_chapter_batch_actions()

    def _mark_selected_chapters_unread(self):
        if self.webtoon is None or self.progress_store is None or not self.selected_chapters:
            return
        chapters = sorted(self.selected_chapters, key=chapter_sort_key)
        self.progress_store.clear_chapters(self.webtoon.name, chapters)
        for chapter in chapters:
            self.progress_map.pop(chapter, None)
        self.selected_chapters.clear()
        progress = self.progress_store.get(self.webtoon.name)
        self._build_chapter_list(progress)
        self._sync_chapter_batch_actions()

    def _delete_selected_chapters(self):
        if self.webtoon is None or self.progress_store is None or not self.selected_chapters:
            return
        selected = sorted(self.selected_chapters, key=chapter_sort_key)
        answer = QMessageBox.question(
            self,
            "Delete selected chapters",
            f"Delete {len(selected)} selected chapters from disk?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return

        import shutil

        for chapter in selected:
            chapter_path = os.path.join(self.webtoon.path, chapter)
            if os.path.isdir(chapter_path):
                shutil.rmtree(chapter_path, ignore_errors=True)
            self.progress_store.clear_chapter(self.webtoon.name, chapter)
            self.progress_map.pop(chapter, None)
            self.bookmarked_chapters.discard(chapter)
            if self.latest_new_chapter == chapter:
                self.settings_store.clear_latest_new_chapter(self.webtoon.name)
                self.latest_new_chapter = None

        self._refresh_webtoon_from_disk()
        self._clear_chapter_selection()

    def _toggle_hide_specials(self):
        self.hide_specials = self.hide_specials_btn.isChecked()
        logger.info("Hide filler toggled for %s: %s", self.webtoon.name if self.webtoon else "<none>", self.hide_specials)
        icon_name = "fa5s.eye-slash" if self.hide_specials else "fa5s.eye"
        self.hide_specials_btn.setIcon(qta.icon(icon_name, color="#888888"))
        self.hide_specials_btn.setIconSize(QSize(12, 12))

        if self.webtoon:
            self.settings_store.set_hide_filler(self.webtoon.name, self.hide_specials)
            self._update_chapter_count_label()

        progress = self.progress_store.get(self.webtoon.name) if self.webtoon else None
        self._build_chapter_list(progress)

    def _toggle_bookmarks_filter(self):
        self.show_only_bookmarked = self.bookmarks_filter_btn.isChecked()
        logger.info(
            "Bookmarked-only filter toggled for %s: %s",
            self.webtoon.name if self.webtoon else "<none>",
            self.show_only_bookmarked,
        )
        self._update_chapter_count_label()
        progress = self.progress_store.get(self.webtoon.name) if self.webtoon else None
        self._build_chapter_list(progress)

    def _ordered_chapters_for_display(self, chapters: list[str]) -> list[str]:
        base = sorted(chapters, key=chapter_sort_key)
        if self.sort_latest_first:
            base.reverse()
        return base

    def _refresh_webtoon_from_disk(self, preserve_display_order: bool = False) -> bool:
        if self.webtoon is None:
            return False

        chapter_dirs = [
            entry for entry in os.listdir(self.webtoon.path)
            if os.path.isdir(os.path.join(self.webtoon.path, entry))
        ]
        chapter_dirs.sort(key=chapter_sort_key)

        if chapter_dirs == list(self.webtoon.chapters):
            return True

        logger.info("Detail page refreshed chapter list from disk for %s", self.webtoon.name)
        previous_display = list(self._chapter_display_order or self._ordered_chapters_for_display(self.webtoon.chapters))
        self.webtoon.chapters = chapter_dirs
        if preserve_display_order:
            existing = set(chapter_dirs)
            kept = [chapter for chapter in previous_display if chapter in existing]
            new_chapters = [chapter for chapter in chapter_dirs if chapter not in kept]
            self._chapter_display_order = kept + new_chapters
        else:
            self._chapter_display_order = self._ordered_chapters_for_display(chapter_dirs)
        self.selected_chapters &= set(chapter_dirs)
        self.latest_new_chapter = self.settings_store.get_latest_new_chapter(self.webtoon.name)
        progress = self.progress_store.get(self.webtoon.name) if self.progress_store else None
        self._update_chapter_count_label()
        self._build_chapter_list(progress)
        self._sync_chapter_batch_actions()
        return True

    def _open_chapter(self, chapter: str):
        logger.info("Opening chapter from detail page: %s / %s", self.webtoon.name if self.webtoon else "<none>", chapter)
        if not self._refresh_webtoon_from_disk():
            return
        if chapter not in self.webtoon.chapters:
            QMessageBox.information(
                self,
                "Chapter removed",
                f"'{chapter}' no longer exists on disk. The chapter list has been refreshed.",
            )
            return
        if self.latest_new_chapter == chapter:
            self.settings_store.clear_latest_new_chapter(self.webtoon.name)
            self.latest_new_chapter = None
        idx = self.webtoon.chapters.index(chapter)
        self.main_window.open_chapter_with_prompt(self.webtoon, idx)

    def _continue_reading(self):
        logger.info("Continue reading requested for %s", self.webtoon.name if self.webtoon else "<none>")
        if not self._refresh_webtoon_from_disk():
            return
        progress = self.progress_store.get(self.webtoon.name)
        if not progress:
            self.main_window.open_chapter(self.webtoon, 0, 0.0)
            return
        chapter = progress["chapter"]
        scroll_pct = progress.get("scroll", 0.0)
        if chapter in self.webtoon.chapters:
            idx = self.webtoon.chapters.index(chapter)
            self.main_window.open_chapter(self.webtoon, idx, scroll_pct)
        else:
            QMessageBox.information(
                self,
                "Chapter removed",
                f"The saved chapter '{chapter}' no longer exists on disk.",
            )

    def _go_back(self):
        logger.info("Returning from detail page to library")
        self.main_window.open_library()

    def attach_update_service(self, service):
        if self._update_service is service:
            return
        logger.info("Attaching shared update service to detail page")
        self._update_service = service
        self._update_service.download_started.connect(self._on_update_started)
        self._update_service.download_finished.connect(self._on_update_finished)
        self._update_service.status_changed.connect(self._on_update_status_changed)
        self._update_service.progress_changed.connect(self._on_update_progress_changed)
        self._update_service.library_changed.connect(self._on_update_library_changed)
        self._sync_update_button()

    def _cooldown_remaining(self) -> int:
        if self.webtoon is None:
            return 0
        last_update_at = self.settings_store.get_last_update_at(self.webtoon.name)
        if last_update_at is None:
            return 0
        elapsed = int(time.time()) - int(last_update_at)
        return max(0, UPDATE_COOLDOWN_SECONDS - elapsed)

    def _start_update(self):
        if self.webtoon is None or self._update_service is None:
            return
        if self.settings_store.get_completed(self.webtoon.name):
            logger.info("Detail page update blocked for completed webtoon %s", self.webtoon.name)
            self._sync_update_button()
            return
        source_url = self.settings_store.get_source_url(self.webtoon.name)
        if not source_url:
            return
        if self._cooldown_remaining() > 0:
            logger.info("Detail page update blocked by cooldown for %s", self.webtoon.name)
            self._sync_update_button()
            return
        logger.info("Starting detail-page update for %s", self.webtoon.name)
        error = self._update_service.start_download(
            source_url,
            load_library_path(),
            preferred_name=self.webtoon.name,
        )
        if error:
            logger.warning("Failed to start detail-page update for %s: %s", self.webtoon.name, error)
            self._sync_update_button()
            return
        self._sync_update_button()

    def _sync_update_button(self):
        if self.webtoon is None:
            self.update_btn.hide()
            self.update_progress_label.hide()
            self.update_progress_circle.hide()
            return
        if self.settings_store.get_completed(self.webtoon.name):
            self.update_btn.hide()
            self.update_progress_label.hide()
            self.update_progress_circle.hide()
            return
        source_url = self.settings_store.get_source_url(self.webtoon.name)
        if not source_url:
            self.update_btn.hide()
            self.update_progress_label.hide()
            self.update_progress_circle.hide()
            return

        self.update_btn.show()
        if self._update_service is not None and self._update_service.has_active_download(self.webtoon.name):
            self.update_btn.setEnabled(False)
            self.update_btn.setText("  Updating...")
            self._show_update_progress()
            return

        self._update_progress_current = 0
        self._update_progress_total = 0
        self.update_progress_label.hide()
        self.update_progress_circle.hide()
        remaining = self._cooldown_remaining()
        self.update_btn.setEnabled(remaining == 0)
        self.update_btn.setText(f"  {remaining}s" if remaining > 0 else "  Update")

    def _on_update_started(self, name: str):
        if self.webtoon and name == self.webtoon.name:
            self._update_progress_current = 0
            self._update_progress_total = 0
        self._sync_update_button()

    def _on_update_finished(self, name: str, status: str):
        logger.info("Detail page received update finished for %s with status=%s", name, status)
        if status == "Completed" and self.webtoon and name == self.webtoon.name:
            self.settings_store.set_last_update_at(name, int(time.time()))
            self.latest_new_chapter = self.settings_store.get_latest_new_chapter(name)
            self._refresh_webtoon_from_disk(preserve_display_order=True)
        self._sync_update_button()

    def _on_update_status_changed(self, name: str, status: str):
        if self.webtoon and name == self.webtoon.name:
            self._sync_update_button()

    def _on_update_progress_changed(self, name: str, current: int, total: int):
        if not self.webtoon or name != self.webtoon.name:
            return
        self._update_progress_current = max(0, int(current))
        self._update_progress_total = max(0, int(total))
        self._show_update_progress()

    def _on_update_library_changed(self, name: str):
        if self.webtoon and name == self.webtoon.name:
            self._refresh_webtoon_from_disk(preserve_display_order=True)
            self._sync_update_button()

    def _open_edit_dialog(self):
        if self.webtoon is None or self.progress_store is None:
            return
        logger.info("Opening edit dialog for %s", self.webtoon.name)

        dlg = EditWebtoonDialog(
            self.webtoon,
            settings_store=self.settings_store,
            progress_store=self.progress_store,
            parent=self,
        )
        if dlg.exec() != QDialog.Accepted:
            return

        self.main_window.library.load_library()
        self.main_window.library.refresh_progress()

        if dlg.deleted:
            self.main_window.stack.setCurrentWidget(self.main_window.library)
            return

        updated = next(
            (w for w in self.main_window.library._webtoons if w.name == self.webtoon.name),
            None,
        )
        if updated is None:
            self.main_window.stack.setCurrentWidget(self.main_window.library)
            return

        self.load_webtoon(updated, self.progress_store)
        self.main_window.stack.setCurrentWidget(self)

    def _toggle_sort(self):
        self.sort_latest_first = not self.sort_latest_first
        logger.info(
            "Detail page sort toggled for %s latest_first=%s",
            self.webtoon.name if self.webtoon else "<none>",
            self.sort_latest_first,
        )
        if self.sort_latest_first:
            self.sort_btn.setText("  Latest")
            self.sort_btn.setIcon(qta.icon("fa5s.sort-amount-down", color="#888888"))
        else:
            self.sort_btn.setText("  Oldest")
            self.sort_btn.setIcon(qta.icon("fa5s.sort-amount-up", color="#888888"))
        self.sort_btn.setIconSize(QSize(12, 12))
        if self.webtoon is not None:
            self._chapter_display_order = self._ordered_chapters_for_display(self.webtoon.chapters)
        progress = self.progress_store.get(self.webtoon.name)
        self._build_chapter_list(progress)

    def _show_update_progress(self):
        if self.webtoon is None:
            self.update_progress_label.hide()
            self.update_progress_circle.hide()
            return
        if self._update_progress_total > 0:
            percent = int((max(0, min(self._update_progress_current, self._update_progress_total)) / self._update_progress_total) * 100)
            self.update_progress_circle.set_percent(percent)
            self.update_progress_label.setText(
                f"Downloading {self._update_progress_current} / {self._update_progress_total}"
            )
        else:
            self.update_progress_circle.set_percent(0)
            self.update_progress_label.setText("Downloading...")
        self.update_progress_circle.show()
        self.update_progress_label.show()

    def _start_from_beginning(self): 
        logger.info("Start from beginning requested for %s", self.webtoon.name if self.webtoon else "<none>")
        self.main_window.open_chapter(self.webtoon, 0)
