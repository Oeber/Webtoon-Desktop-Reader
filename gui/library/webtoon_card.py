from PySide6.QtCore import QPoint, Qt, QSize
from PySide6.QtGui import QAction, QFont, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QDialog, QLabel, QMenu, QPushButton, QVBoxLayout, QWidget
import qtawesome as qta
import time

from gui.downloader.download_widgets import SpinnerCircle
from gui.library.edit_webtoon_dialog import EditWebtoonDialog


CARD_WIDTH = 180
CARD_HEIGHT = 270
CARD_RADIUS = 12


class WebtoonCard(QWidget):

    def __init__(self, webtoon, settings_store, progress_store, on_open, on_changed, on_update=None):
        super().__init__()

        self.webtoon = webtoon
        self.progress_store = progress_store
        self.settings_store = settings_store
        self.on_open = on_open
        self.on_changed = on_changed
        self.on_update = on_update

        self._latest_connected = False
        self._lastread_connected = False
        self._update_available = False
        self._ignore_open_until = 0.0
        self._update_menu_label = "Update"
        self._update_button_label = ""
        self._update_menu = None
        self._update_action = None

        self.setFixedWidth(CARD_WIDTH + 16)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("background: transparent;")

        root = QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(8, 8, 8, 8)

        self.image_container = QWidget()
        self.image_container.setFixedSize(CARD_WIDTH, CARD_HEIGHT)
        self.image_container.setStyleSheet("background: transparent;")

        self.image_label = QLabel(self.image_container)
        self.image_label.setFixedSize(CARD_WIDTH, CARD_HEIGHT)
        self.image_label.setAlignment(Qt.AlignCenter)
        self._apply_border_style(hovered=False)

        self.dots_btn = QPushButton("...")
        self.dots_btn.setParent(self.image_container)
        self.dots_btn.setFixedSize(28, 28)
        self.dots_btn.move(CARD_WIDTH - 34, 6)
        self.dots_btn.setCursor(Qt.PointingHandCursor)
        self.dots_btn.setStyleSheet("""
            QPushButton {
                background: rgba(0,0,0,0.65);
                color: #fff;
                border: none;
                border-radius: 14px;
                font-size: 14px;
                padding-bottom: 2px;
            }
            QPushButton:hover { background: rgba(80,80,80,0.90); }
        """)
        self.dots_btn.hide()
        self.dots_btn.clicked.connect(self._show_context_menu_at_btn)

        self.update_btn = QPushButton()
        self.update_btn.setParent(self.image_container)
        self.update_btn.setFixedSize(28, 28)
        self.update_btn.move(6, 6)
        self.update_btn.setCursor(Qt.PointingHandCursor)
        self.update_btn.setStyleSheet("""
            QPushButton {
                background: rgba(0,0,0,0.65);
                color: #fff;
                border: none;
                border-radius: 14px;
                font-size: 10px;
                font-weight: 700;
                padding: 0;
            }
            QPushButton:hover { background: rgba(80,80,80,0.90); }
            QPushButton:disabled {
                background: rgba(0,0,0,0.45);
                color: #777;
            }
        """)
        self.update_btn.hide()
        self.update_btn.clicked.connect(self._trigger_update)
        self._set_update_button_idle()

        self.progress_overlay = QWidget(self.image_container)
        self.progress_overlay.setFixedSize(84, 84)
        self.progress_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.progress_overlay.setStyleSheet("""
            QWidget {
                background: rgba(0, 0, 0, 0.55);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 42px;
            }
        """)
        overlay_layout = QVBoxLayout(self.progress_overlay)
        overlay_layout.setContentsMargins(0, 0, 0, 0)
        self.progress_spinner = SpinnerCircle(self.progress_overlay)
        overlay_layout.addWidget(self.progress_spinner, alignment=Qt.AlignCenter)
        self.progress_overlay.hide()
        self._center_progress_overlay()

        self.title_label = QLabel(webtoon.name)
        self.title_label.setFixedWidth(CARD_WIDTH)
        self.title_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.title_label.setWordWrap(True)
        self.title_label.setMaximumHeight(40)
        self.title_label.setStyleSheet("""
            QLabel {
                color: #e0e0e0;
                font-size: 12px;
                background: transparent;
                border: none;
                padding: 0;
            }
        """)
        font = QFont("Segoe UI", 10)
        font.setWeight(QFont.Medium)
        self.title_label.setFont(font)

        self.latest_btn = self._make_badge_btn(accent=False)
        self.lastread_btn = self._make_badge_btn(accent=True)

        root.addWidget(self.image_container)
        root.addWidget(self.title_label)
        root.addWidget(self.latest_btn)
        root.addWidget(self.lastread_btn)

        self._load_thumbnail(webtoon.thumbnail)
        self._refresh_badges()

    def _make_badge_btn(self, accent=False) -> QPushButton:
        btn = QPushButton()
        btn.setFixedWidth(CARD_WIDTH)
        btn.setFixedHeight(20)
        btn.setCursor(Qt.PointingHandCursor)
        color = "#2979ff" if accent else "#888"
        bg_hover = "#1a2a4a" if accent else "#2a2a2a"
        btn.setStyleSheet(f"""
            QPushButton {{
                color: {color};
                font-size: 10px;
                font-weight: 600;
                background: transparent;
                border: none;
                text-align: left;
                padding: 0 2px;
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

        if chapters:
            latest = chapters[-1]
            self.latest_btn.setText(f"Play  {latest}")
            self.latest_btn.show()
            if self._latest_connected:
                self.latest_btn.clicked.disconnect()
            self.latest_btn.clicked.connect(
                lambda checked=False, ch=latest: self._open_chapter_direct(ch)
            )
            self._latest_connected = True
        else:
            self.latest_btn.hide()

        if progress:
            last_ch = progress["chapter"]
            self.lastread_btn.setText(f"Last  {last_ch}")
            self.lastread_btn.show()
            if self._lastread_connected:
                self.lastread_btn.clicked.disconnect()
            self.lastread_btn.clicked.connect(
                lambda checked=False, ch=last_ch: self._open_chapter_direct(ch)
            )
            self._lastread_connected = True
        else:
            self.lastread_btn.hide()

    def _open_chapter_direct(self, chapter: str):
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

    def _load_thumbnail(self, path: str):
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.image_label.clear()
            return

        pixmap = pixmap.scaled(
            CARD_WIDTH,
            CARD_HEIGHT,
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation,
        )
        x = (pixmap.width() - CARD_WIDTH) // 2
        y = (pixmap.height() - CARD_HEIGHT) // 2
        pixmap = pixmap.copy(x, y, CARD_WIDTH, CARD_HEIGHT)

        rounded = QPixmap(CARD_WIDTH, CARD_HEIGHT)
        rounded.fill(Qt.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.Antialiasing)
        path_obj = QPainterPath()
        path_obj.addRoundedRect(0, 0, CARD_WIDTH, CARD_HEIGHT, CARD_RADIUS, CARD_RADIUS)
        painter.setClipPath(path_obj)
        painter.drawPixmap(0, 0, pixmap)
        painter.end()

        self.image_label.setPixmap(rounded)

    def refresh_webtoon(self, webtoon):
        self.webtoon = webtoon
        self.title_label.setText(webtoon.name)
        self._load_thumbnail(webtoon.thumbnail)
        self._refresh_badges()

    def _build_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #1e1e1e;
                color: #e0e0e0;
                border: 1px solid #333;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 20px;
                border-radius: 4px;
            }
            QMenu::item:selected { background: #2e2e2e; }
        """)

        edit_action = QAction("Edit", self)
        edit_action.triggered.connect(self._open_edit_dialog)
        menu.addAction(edit_action)

        if self._update_available:
            update_action = QAction(self._update_menu_label, self)
            update_action.triggered.connect(self._trigger_update)
            update_action.setEnabled(self.update_btn.isEnabled())
            menu.addAction(update_action)
            self._update_action = update_action
        else:
            self._update_action = None

        self._update_menu = menu
        menu.aboutToHide.connect(self._clear_menu_refs)
        return menu

    def _show_context_menu_at_btn(self):
        self._build_menu().exec(
            self.dots_btn.mapToGlobal(QPoint(0, self.dots_btn.height()))
        )

    def contextMenuEvent(self, event):
        self._build_menu().exec(event.globalPos())

    def _open_edit_dialog(self):
        dlg = EditWebtoonDialog(
            self.webtoon,
            settings_store=self.settings_store,
            progress_store=self.progress_store,
            parent=self,
        )
        if dlg.exec() == QDialog.Accepted and callable(self.on_changed):
            self.on_changed()

    def enterEvent(self, event):
        self._apply_border_style(hovered=True)
        self.dots_btn.show()
        if self._update_available:
            self.update_btn.show()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._apply_border_style(hovered=False)
        self.dots_btn.hide()
        if self._update_available and not self.progress_overlay.isVisible():
            self.update_btn.hide()
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

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if time.monotonic() < self._ignore_open_until:
                event.accept()
                return

            target = self.childAt(event.position().toPoint())
            while target is not None and target is not self:
                if target in (self.dots_btn, self.latest_btn, self.lastread_btn, self.update_btn):
                    event.accept()
                    return
                target = target.parentWidget()

            if self.progress_overlay.isVisible():
                event.accept()
                return

            self.on_open(self.webtoon)
            event.accept()
            return
        super().mousePressEvent(event)

    def set_update_available(self, available: bool):
        self._update_available = available
        self.update_btn.setVisible(available and self.underMouse())
        if not available:
            self.progress_overlay.hide()

    def set_update_enabled(self, enabled: bool, tooltip: str = "", cooldown_text: str | None = None):
        self.update_btn.setEnabled(enabled)
        self.update_btn.setToolTip(tooltip)
        if cooldown_text:
            self._update_button_label = cooldown_text
            self._update_menu_label = f"Update ({cooldown_text})"
        else:
            self._update_button_label = ""
            self._update_menu_label = "Update"
        self._set_update_button_idle()
        if self._update_action is not None:
            self._update_action.setText(self._update_menu_label)
            self._update_action.setEnabled(enabled)

    def set_update_progress(self, current: int, total: int):
        total = max(1, total)
        current = max(0, min(current, total))
        percent = int((current / total) * 100)
        self.progress_overlay.show()
        self.progress_spinner.set_progress(percent)

    def set_update_status(self, status: str):
        if status == "Downloading":
            self._ignore_card_open(0.75)
            self.progress_overlay.show()
            self.progress_spinner.set_spinning()
            self.update_btn.hide()
            return

        if status == "Completed":
            self._ignore_card_open(1.5)

        self.progress_overlay.hide()
        if self._update_available and self.underMouse():
            self.update_btn.show()

    def _center_progress_overlay(self):
        x = (CARD_WIDTH - self.progress_overlay.width()) // 2
        y = (CARD_HEIGHT - self.progress_overlay.height()) // 2
        self.progress_overlay.move(x, y)

    def _trigger_update(self):
        self._ignore_card_open(1.0)
        if callable(self.on_update):
            self.on_update(self.webtoon.name)

    def _ignore_card_open(self, seconds: float):
        self._ignore_open_until = max(self._ignore_open_until, time.monotonic() + seconds)

    def _set_update_button_idle(self):
        if self._update_button_label:
            self.update_btn.setText(self._update_button_label)
            self.update_btn.setIcon(QIcon())
        else:
            self.update_btn.setText("")
            self.update_btn.setIcon(qta.icon("fa5s.sync", color="#ffffff"))
            self.update_btn.setIconSize(QSize(12, 12))

    def _clear_menu_refs(self):
        self._update_menu = None
        self._update_action = None
