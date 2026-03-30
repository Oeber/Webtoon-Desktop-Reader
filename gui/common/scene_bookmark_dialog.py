from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout

from gui.common.styles import BUTTON_STYLE, PAGE_BG_STYLE, STATUS_LABEL_STYLE, TEXT_MUTED_TRANSPARENT_STYLE
from gui.common.strings import t


THUMB_SIZE = QSize(96, 96)


class SceneBookmarksDialog(QDialog):
    def __init__(self, webtoon, chapter: str, bookmark_store, open_callback, parent=None, *, mode_label: str = "Scene"):
        super().__init__(parent)
        self.webtoon = webtoon
        self.chapter = chapter
        self.bookmark_store = bookmark_store
        self.open_callback = open_callback
        self.mode_label = str(mode_label or "Scene")

        self.setWindowTitle(t("scene.dialog.window", mode_label=self.mode_label, chapter=chapter))
        self.setModal(True)
        self.resize(640, 480)
        self.setStyleSheet(PAGE_BG_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel(t("scene.dialog.title", mode_label=self.mode_label, chapter=chapter))
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #f3ece8;")
        layout.addWidget(title)

        subtitle = QLabel(t("scene.dialog.subtitle", mode_label=self.mode_label.lower()))
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(TEXT_MUTED_TRANSPARENT_STYLE)
        layout.addWidget(subtitle)

        self.list_widget = QListWidget(self)
        self.list_widget.setIconSize(THUMB_SIZE)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self._open_selected())
        layout.addWidget(self.list_widget, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)

        self.open_btn = QPushButton(t("scene.dialog.open", mode_label=self.mode_label))
        self.open_btn.setStyleSheet(BUTTON_STYLE)
        self.open_btn.clicked.connect(self._open_selected)
        actions.addWidget(self.open_btn)

        self.delete_btn = QPushButton(t("scene.dialog.delete"))
        self.delete_btn.setStyleSheet(BUTTON_STYLE)
        self.delete_btn.clicked.connect(self._delete_selected)
        actions.addWidget(self.delete_btn)

        actions.addStretch()

        close_btn = QPushButton(t("scene.dialog.close"))
        close_btn.setStyleSheet(BUTTON_STYLE)
        close_btn.clicked.connect(self.accept)
        actions.addWidget(close_btn)

        layout.addLayout(actions)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(STATUS_LABEL_STYLE)
        layout.addWidget(self.status_label)

        self.refresh_bookmarks()

    def refresh_bookmarks(self):
        self.list_widget.clear()
        bookmarks = self.bookmark_store.list_for_chapter(self.webtoon.name, self.chapter)
        for bookmark in bookmarks:
            item = QListWidgetItem(self._item_text(bookmark, self.mode_label))
            item.setData(Qt.UserRole, bookmark)
            item.setToolTip(self._item_tooltip(bookmark, self.mode_label))
            item.setSizeHint(QSize(0, 104))
            icon = self._item_icon(bookmark)
            if icon is not None:
                item.setIcon(icon)
            self.list_widget.addItem(item)
        has_rows = self.list_widget.count() > 0
        self.open_btn.setEnabled(has_rows)
        self.delete_btn.setEnabled(has_rows)
        self.status_label.setText(t("scene.dialog.empty_chapter", mode_label=self.mode_label.lower()) if not has_rows else "")
        if has_rows:
            self.list_widget.setCurrentRow(0)

    def _current_bookmark(self) -> dict | None:
        item = self.list_widget.currentItem()
        if item is None:
            return None
        data = item.data(Qt.UserRole)
        return data if isinstance(data, dict) else None

    def _open_selected(self):
        bookmark = self._current_bookmark()
        if bookmark is None:
            return
        self.open_callback(float(bookmark.get("packed") or 0.0))
        self.accept()

    def _delete_selected(self):
        bookmark = self._current_bookmark()
        if bookmark is None:
            return
        self.bookmark_store.delete(int(bookmark["id"]))
        self.refresh_bookmarks()

    @staticmethod
    def _item_text(bookmark: dict, mode_label: str = "Scene") -> str:
        title = str(bookmark.get("note") or "").strip()
        if not title:
            title = SceneBookmarksDialog._default_title(bookmark, mode_label)
        updated_text = SceneBookmarksDialog._format_timestamp(int(bookmark.get("updated_at") or 0))
        return f"{title}\n{updated_text}"

    @staticmethod
    def _default_title(bookmark: dict, mode_label: str = "Scene") -> str:
        image_index = int(bookmark.get("image_index") or 0)
        packed = max(0.0, float(bookmark.get("packed") or 0.0))
        if image_index <= 0:
            return t("scene.dialog.default_progress", mode_label=mode_label, percent=int(packed * 100))
        image_index = max(1, image_index)
        offset = packed - int(packed)
        if offset < 0.2:
            region = t("scene.dialog.region_top")
        elif offset > 0.8:
            region = t("scene.dialog.region_bottom")
        else:
            region = t("scene.dialog.region_mid")
        return t("scene.dialog.default_image", image_index=image_index, region=region)

    @staticmethod
    def _item_tooltip(bookmark: dict, mode_label: str = "Scene") -> str:
        packed = float(bookmark.get("packed") or 0.0)
        image_index = int(bookmark.get("image_index") or 0)
        if image_index <= 0:
            return t("scene.dialog.tooltip_progress", mode_label=mode_label, percent=int(max(0.0, min(1.0, packed)) * 100))
        image_index = max(1, image_index)
        return t("scene.dialog.tooltip_image", image_index=image_index, packed=packed)

    @staticmethod
    def _item_icon(bookmark: dict) -> QIcon | None:
        thumbnail_path = str(bookmark.get("thumbnail_path") or "").strip()
        if not thumbnail_path:
            return None
        pixmap = QPixmap(thumbnail_path)
        if pixmap.isNull():
            return None
        scaled = pixmap.scaled(THUMB_SIZE, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        return QIcon(scaled)

    @staticmethod
    def _format_timestamp(timestamp_ms: int) -> str:
        if timestamp_ms <= 0:
            return t("scene.dialog.saved_recently")
        return datetime.fromtimestamp(timestamp_ms / 1000.0).strftime("%Y-%m-%d %H:%M")


class AllSceneBookmarksDialog(QDialog):
    def __init__(self, webtoon, bookmark_store, open_callback, parent=None, *, mode_label: str = "Scene"):
        super().__init__(parent)
        self.webtoon = webtoon
        self.bookmark_store = bookmark_store
        self.open_callback = open_callback
        self.mode_label = str(mode_label or "Scene")

        self.setWindowTitle(t("scene.dialog.window", mode_label=self.mode_label, chapter=self.webtoon.name))
        self.setModal(True)
        self.resize(720, 520)
        self.setStyleSheet(PAGE_BG_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel(t("scene.dialog.all_title", mode_label=self.mode_label))
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #f3ece8;")
        layout.addWidget(title)

        subtitle = QLabel(t("scene.dialog.all_subtitle", mode_label=self.mode_label.lower()))
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(TEXT_MUTED_TRANSPARENT_STYLE)
        layout.addWidget(subtitle)

        self.list_widget = QListWidget(self)
        self.list_widget.setIconSize(THUMB_SIZE)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self._open_selected())
        layout.addWidget(self.list_widget, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)

        self.open_btn = QPushButton(t("scene.dialog.open", mode_label=self.mode_label))
        self.open_btn.setStyleSheet(BUTTON_STYLE)
        self.open_btn.clicked.connect(self._open_selected)
        actions.addWidget(self.open_btn)

        self.delete_btn = QPushButton(t("scene.dialog.delete"))
        self.delete_btn.setStyleSheet(BUTTON_STYLE)
        self.delete_btn.clicked.connect(self._delete_selected)
        actions.addWidget(self.delete_btn)

        actions.addStretch()

        close_btn = QPushButton(t("scene.dialog.close"))
        close_btn.setStyleSheet(BUTTON_STYLE)
        close_btn.clicked.connect(self.accept)
        actions.addWidget(close_btn)

        layout.addLayout(actions)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(STATUS_LABEL_STYLE)
        layout.addWidget(self.status_label)

        self.refresh_bookmarks()

    def refresh_bookmarks(self):
        self.list_widget.clear()
        bookmarks = self.bookmark_store.list_for_webtoon(self.webtoon.name)
        for bookmark in bookmarks:
            chapter = str(bookmark.get("chapter") or "")
            item = QListWidgetItem(f"{chapter}\n{SceneBookmarksDialog._item_text(bookmark, self.mode_label)}")
            item.setData(Qt.UserRole, bookmark)
            item.setToolTip(f"{chapter}\n{SceneBookmarksDialog._item_tooltip(bookmark, self.mode_label)}")
            item.setSizeHint(QSize(0, 116))
            icon = SceneBookmarksDialog._item_icon(bookmark)
            if icon is not None:
                item.setIcon(icon)
            self.list_widget.addItem(item)
        has_rows = self.list_widget.count() > 0
        self.open_btn.setEnabled(has_rows)
        self.delete_btn.setEnabled(has_rows)
        self.status_label.setText(t("scene.dialog.empty_series", mode_label=self.mode_label.lower()) if not has_rows else "")
        if has_rows:
            self.list_widget.setCurrentRow(0)

    def _current_bookmark(self) -> dict | None:
        item = self.list_widget.currentItem()
        if item is None:
            return None
        data = item.data(Qt.UserRole)
        return data if isinstance(data, dict) else None

    def _open_selected(self):
        bookmark = self._current_bookmark()
        if bookmark is None:
            return
        self.open_callback(str(bookmark.get("chapter") or ""), float(bookmark.get("packed") or 0.0))
        self.accept()

    def _delete_selected(self):
        bookmark = self._current_bookmark()
        if bookmark is None:
            return
        self.bookmark_store.delete(int(bookmark["id"]))
        self.refresh_bookmarks()
