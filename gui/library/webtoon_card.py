from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout,
    QPushButton, QMenu, QFileDialog
)
from PySide6.QtGui import QPixmap, QFont, QPainter, QPainterPath, QAction
from PySide6.QtCore import Qt, QPoint


CARD_WIDTH  = 180
CARD_HEIGHT = 270
CARD_RADIUS = 8


class WebtoonCard(QWidget):

    def __init__(self, webtoon, thumb_store, progress_store, on_open):
        super().__init__()

        self.webtoon        = webtoon
        self.thumb_store    = thumb_store
        self.progress_store = progress_store
        self.on_open        = on_open

        # Track whether badge buttons have a signal connected yet
        self._latest_connected  = False
        self._lastread_connected = False

        self.setFixedWidth(CARD_WIDTH + 16)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("background: transparent;")

        root = QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(8, 8, 8, 8)

        # ── Image container ──────────────────────────────────────────────
        self.image_container = QWidget()
        self.image_container.setFixedSize(CARD_WIDTH, CARD_HEIGHT)
        self.image_container.setStyleSheet("background: transparent;")

        self.image_label = QLabel(self.image_container)
        self.image_label.setFixedSize(CARD_WIDTH, CARD_HEIGHT)
        self.image_label.setAlignment(Qt.AlignCenter)
        self._apply_border_style(hovered=False)

        # ⋯ button
        self.dots_btn = QPushButton("⋯", self.image_container)
        self.dots_btn.setFixedSize(28, 28)
        self.dots_btn.move(CARD_WIDTH - 34, 6)
        self.dots_btn.setCursor(Qt.PointingHandCursor)
        self.dots_btn.setStyleSheet("""
            QPushButton {
                background: rgba(0,0,0,0.65); color: #fff;
                border: none; border-radius: 14px;
                font-size: 14px; padding-bottom: 4px;
            }
            QPushButton:hover { background: rgba(80,80,80,0.90); }
        """)
        self.dots_btn.hide()
        self.dots_btn.clicked.connect(self._show_context_menu_at_btn)

        # ── Title ────────────────────────────────────────────────────────
        self.title_label = QLabel(webtoon.name)
        self.title_label.setFixedWidth(CARD_WIDTH)
        self.title_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.title_label.setWordWrap(True)
        self.title_label.setMaximumHeight(40)
        self.title_label.setStyleSheet("""
            QLabel {
                color: #e0e0e0; font-size: 12px;
                background: transparent; border: none; padding: 0;
            }
        """)
        font = QFont("Segoe UI", 10)
        font.setWeight(QFont.Medium)
        self.title_label.setFont(font)

        # ── Badge buttons ────────────────────────────────────────────────
        self.latest_btn   = self._make_badge_btn(accent=False)
        self.lastread_btn = self._make_badge_btn(accent=True)

        root.addWidget(self.image_container)
        root.addWidget(self.title_label)
        root.addWidget(self.latest_btn)
        root.addWidget(self.lastread_btn)

        self._load_thumbnail(webtoon.thumbnail)
        self._refresh_badges()

    # ------------------------------------------------------------------ #
    #  Badge buttons                                                       #
    # ------------------------------------------------------------------ #

    def _make_badge_btn(self, accent=False) -> QPushButton:
        btn = QPushButton()
        btn.setFixedWidth(CARD_WIDTH)
        btn.setFixedHeight(20)
        btn.setCursor(Qt.PointingHandCursor)
        color    = "#2979ff" if accent else "#888"
        bg_hover = "#1a2a4a" if accent else "#2a2a2a"
        btn.setStyleSheet(f"""
            QPushButton {{
                color: {color}; font-size: 10px; font-weight: 600;
                background: transparent; border: none;
                text-align: left; padding: 0 2px;
            }}
            QPushButton:hover {{
                background: {bg_hover};
                border-radius: 4px;
            }}
        """)
        btn.hide()
        return btn

    def _refresh_badges(self):
        chapters = self.webtoon.chapters
        progress = self.progress_store.get(self.webtoon.name)

        # ── Latest chapter badge ─────────────────────────────────────────
        if chapters:
            latest = chapters[-1]
            self.latest_btn.setText(f"▶  {latest}")
            self.latest_btn.show()
            if self._latest_connected:
                self.latest_btn.clicked.disconnect()
            self.latest_btn.clicked.connect(lambda checked=False, ch=latest: self._open_chapter_direct(ch))
            self._latest_connected = True
        else:
            self.latest_btn.hide()

        # ── Last-read badge ──────────────────────────────────────────────
        if progress:
            last_ch = progress["chapter"]
            self.lastread_btn.setText(f"◉  {last_ch}")
            self.lastread_btn.show()
            if self._lastread_connected:
                self.lastread_btn.clicked.disconnect()
            self.lastread_btn.clicked.connect(lambda checked=False, ch=last_ch: self._open_chapter_direct(ch))
            self._lastread_connected = True
        else:
            self.lastread_btn.hide()

    def _open_chapter_direct(self, chapter: str):
        """Open a chapter via main_window so the continue/restart prompt is shown."""
        chapters = self.webtoon.chapters
        if chapter not in chapters:
            return
        idx = chapters.index(chapter)
        mw = self._find_main_window()
        if mw:
            mw.open_chapter_with_prompt(self.webtoon, idx)

    def _find_main_window(self):
        w = self.parent()
        while w:
            if hasattr(w, "open_chapter_with_prompt"):
                return w
            w = w.parent()
        return None

    # ------------------------------------------------------------------ #
    #  Thumbnail                                                           #
    # ------------------------------------------------------------------ #

    def _load_thumbnail(self, path: str):
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.image_label.clear()
            return

        pixmap = pixmap.scaled(CARD_WIDTH, CARD_HEIGHT,
                               Qt.KeepAspectRatioByExpanding,
                               Qt.SmoothTransformation)
        x = (pixmap.width()  - CARD_WIDTH)  // 2
        y = (pixmap.height() - CARD_HEIGHT) // 2
        pixmap = pixmap.copy(x, y, CARD_WIDTH, CARD_HEIGHT)

        rounded = QPixmap(CARD_WIDTH, CARD_HEIGHT)
        rounded.fill(Qt.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.Antialiasing)
        p = QPainterPath()
        p.addRoundedRect(0, 0, CARD_WIDTH, CARD_HEIGHT, CARD_RADIUS, CARD_RADIUS)
        painter.setClipPath(p)
        painter.drawPixmap(0, 0, pixmap)
        painter.end()

        self.image_label.setPixmap(rounded)

    # ------------------------------------------------------------------ #
    #  Context menu (thumbnail override)                                  #
    # ------------------------------------------------------------------ #

    def _build_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #1e1e1e; color: #e0e0e0;
                border: 1px solid #333; border-radius: 6px; padding: 4px;
            }
            QMenu::item { padding: 6px 20px; border-radius: 4px; }
            QMenu::item:selected { background: #2e2e2e; }
        """)
        set_action = QAction("🖼  Set custom thumbnail", self)
        set_action.triggered.connect(self._pick_custom_thumbnail)
        menu.addAction(set_action)

        if self.thumb_store.get(self.webtoon.name):
            reset_action = QAction("↺  Reset to auto thumbnail", self)
            reset_action.triggered.connect(self._reset_thumbnail)
            menu.addAction(reset_action)

        return menu

    def _show_context_menu_at_btn(self):
        self._build_menu().exec(
            self.dots_btn.mapToGlobal(QPoint(0, self.dots_btn.height()))
        )

    def contextMenuEvent(self, event):
        self._build_menu().exec(event.globalPos())

    def _pick_custom_thumbnail(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select thumbnail image", "",
            "Images (*.jpg *.jpeg *.png *.webp)"
        )
        if not file_path:
            return
        self.thumb_store.set(self.webtoon.name, file_path)
        self.webtoon.thumbnail = file_path
        self._load_thumbnail(file_path)

    def _reset_thumbnail(self):
        self.thumb_store.clear(self.webtoon.name)
        auto_path = f"data/thumbnails/{self.webtoon.name}.jpg"
        self.webtoon.thumbnail = auto_path
        self._load_thumbnail(auto_path)

    # ------------------------------------------------------------------ #
    #  Hover                                                               #
    # ------------------------------------------------------------------ #

    def enterEvent(self, event):
        self._apply_border_style(hovered=True)
        self.dots_btn.show()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._apply_border_style(hovered=False)
        self.dots_btn.hide()
        super().leaveEvent(event)

    def _apply_border_style(self, hovered: bool):
        color = "#666" if hovered else "#2a2a2a"
        self.image_label.setStyleSheet(f"""
            QLabel {{
                background-color: #1e1e1e;
                border-radius: {CARD_RADIUS}px;
                border: 1px solid {color};
            }}
        """)

    # ------------------------------------------------------------------ #
    #  Click — open detail page                                           #
    # ------------------------------------------------------------------ #

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            child = self.childAt(event.position().toPoint())
            if child in (self.dots_btn, self.latest_btn, self.lastread_btn):
                return
            self.on_open(self.webtoon)
        super().mousePressEvent(event)




