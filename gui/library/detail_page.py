from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea
)
from PySide6.QtGui import QPixmap, QPainter, QPainterPath, QFont
from PySide6.QtCore import Qt, QPoint


THUMB_W = 140
THUMB_H = 210
RADIUS  = 8


class DetailPage(QWidget):

    def __init__(self, main_window):
        super().__init__()
        self.main_window    = main_window
        self.webtoon        = None
        self.progress_store = None

        self.setStyleSheet("background-color: #121212;")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Top bar ──────────────────────────────────────────────────────
        top_bar = QWidget()
        top_bar.setFixedHeight(52)
        top_bar.setStyleSheet("background-color: #181818; border-bottom: 1px solid #222;")
        tb_layout = QHBoxLayout(top_bar)
        tb_layout.setContentsMargins(16, 0, 16, 0)

        self.back_btn = QPushButton("← Back")
        self.back_btn.setCursor(Qt.PointingHandCursor)
        self.back_btn.setStyleSheet("""
            QPushButton { background: transparent; color: #aaa; border: none; font-size: 14px; }
            QPushButton:hover { color: #fff; }
        """)
        self.back_btn.clicked.connect(self._go_back)

        self.bar_title = QLabel()
        self.bar_title.setStyleSheet("color: #e0e0e0; font-size: 15px; font-weight: 600;")
        self.bar_title.setAlignment(Qt.AlignCenter)

        tb_layout.addWidget(self.back_btn)
        tb_layout.addWidget(self.bar_title, 1)
        tb_layout.addSpacing(70)
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
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet("color: #fff; font-size: 22px; font-weight: 700;")
        self.title_label.setFont(QFont("Segoe UI", 16, QFont.Bold))

        self.last_read_label = QLabel()
        self.last_read_label.setStyleSheet("color: #aaa; font-size: 13px;")

        self.chapter_count_label = QLabel()
        self.chapter_count_label.setStyleSheet("color: #888; font-size: 12px;")

        self.continue_btn = QPushButton("▶  Continue reading")
        self.continue_btn.setCursor(Qt.PointingHandCursor)
        self.continue_btn.setFixedHeight(36)
        self.continue_btn.setFixedWidth(200)
        self.continue_btn.setStyleSheet("""
            QPushButton { background: #2979ff; color: #fff; border: none; border-radius: 6px;
                          font-size: 13px; font-weight: 600; }
            QPushButton:hover { background: #448aff; }
        """)
        self.continue_btn.clicked.connect(self._continue_reading)
        self.continue_btn.hide()

        info_layout.addWidget(self.title_label)
        info_layout.addWidget(self.last_read_label)
        info_layout.addWidget(self.chapter_count_label)
        info_layout.addSpacing(12)
        info_layout.addWidget(self.continue_btn)

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
        sh_layout.addWidget(chapters_lbl)
        root.addWidget(section_header)

        # ── Chapter list ─────────────────────────────────────────────────
        self.chapter_scroll = QScrollArea()
        self.chapter_scroll.setWidgetResizable(True)
        self.chapter_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chapter_scroll.setStyleSheet("""
            QScrollArea { border: none; background: #121212; }
            QScrollBar:vertical { background: #1a1a1a; width: 6px; border-radius: 3px; }
            QScrollBar::handle:vertical { background: #333; border-radius: 3px; min-height: 20px; }
            QScrollBar::handle:vertical:hover { background: #555; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)

        self.chapter_list_widget = QWidget()
        self.chapter_list_widget.setStyleSheet("background: #121212;")
        self.chapter_list_layout = QVBoxLayout(self.chapter_list_widget)
        self.chapter_list_layout.setContentsMargins(32, 0, 32, 24)
        self.chapter_list_layout.setSpacing(0)
        self.chapter_list_layout.setAlignment(Qt.AlignTop)

        self.chapter_scroll.setWidget(self.chapter_list_widget)
        root.addWidget(self.chapter_scroll, 1)

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def load_webtoon(self, webtoon, progress_store):
        self.webtoon        = webtoon
        self.progress_store = progress_store

        self.bar_title.setText(webtoon.name)
        self.title_label.setText(webtoon.name)
        self.chapter_count_label.setText(f"{len(webtoon.chapters)} chapters")

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

        # Progress
        progress = progress_store.get(webtoon.name)
        if progress:
            self.last_read_label.setText(f"Last read: {progress['chapter']}")
            self.continue_btn.show()
        else:
            self.last_read_label.setText("Not started")
            self.continue_btn.hide()

        self._build_chapter_list(progress)

    def _build_chapter_list(self, progress):
        while self.chapter_list_layout.count():
            item = self.chapter_list_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        last_read_chapter = progress["chapter"] if progress else None

        for chapter in reversed(self.webtoon.chapters):
            row = self._make_chapter_row(chapter, is_last_read=(chapter == last_read_chapter))
            self.chapter_list_layout.addWidget(row)

    def _make_chapter_row(self, chapter: str, is_last_read: bool) -> QWidget:
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

        name_lbl = QLabel(chapter)
        name_lbl.setStyleSheet(
            "color: #2979ff; font-size: 14px; border: none;" if is_last_read
            else "color: #cccccc; font-size: 14px; border: none;"
        )
        layout.addWidget(name_lbl, 1)

        if is_last_read:
            badge = QLabel("last read")
            badge.setStyleSheet("""
                QLabel {
                    color: #2979ff; font-size: 10px; font-weight: 600;
                    background: #1a2a4a; border-radius: 4px;
                    padding: 2px 6px; border: none;
                }
            """)
            layout.addWidget(badge)

        # clicking any chapter goes through the prompt in the viewer
        row.mousePressEvent = lambda e, ch=chapter: self._open_chapter(ch)
        return row

    # ------------------------------------------------------------------ #
    #  Actions                                                             #
    # ------------------------------------------------------------------ #

    def _open_chapter(self, chapter: str):
        idx = self.webtoon.chapters.index(chapter)
        self.main_window.open_chapter_with_prompt(self.webtoon, idx)

    def _continue_reading(self):
        """Continue button: restore saved chapter + scroll, no prompt."""
        progress = self.progress_store.get(self.webtoon.name)
        if not progress:
            self.main_window.open_chapter(self.webtoon, 0, 0.0)
            return
        chapter = progress["chapter"]
        scroll_pct = progress.get("scroll", 0.0)
        if chapter in self.webtoon.chapters:
            idx = self.webtoon.chapters.index(chapter)
            self.main_window.open_chapter(self.webtoon, idx, scroll_pct)

    def _go_back(self):
        self.main_window.stack.setCurrentWidget(self.main_window.library)