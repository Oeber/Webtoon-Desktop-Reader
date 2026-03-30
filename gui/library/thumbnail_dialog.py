"""
thumbnail_dialog.py

A modal dialog for setting a custom webtoon thumbnail.
Supports:
  - Drag & drop an image file onto the drop zone
  - Click the drop zone to browse for a local file
  - Paste / type a URL and download it

Usage:
    dlg = ThumbnailDialog(webtoon_name, settings_store, parent=self)
    if dlg.exec() == QDialog.Accepted:
        new_path = dlg.saved_path   # internal copy already saved in settings store
"""

from __future__ import annotations

import os
from pathlib import Path

from core.app_logging import get_logger
from gui.common.strings import t
from gui.common.styles import (
    TEXT_DIM,
    THUMBNAIL_DIALOG_STYLE,
    THUMBNAIL_DIALOG_TITLE_STYLE,
    THUMBNAIL_DIVIDER_LINE_STYLE,
    THUMBNAIL_DROPZONE_ICON_HOVER_STYLE,
    THUMBNAIL_DROPZONE_ICON_STYLE,
    THUMBNAIL_DROPZONE_SUBTITLE_STYLE,
    THUMBNAIL_DROPZONE_TITLE_STYLE,
    THUMBNAIL_PREVIEW_STYLE,
    THUMBNAIL_STATUS_IDLE_STYLE,
    status_label_color_style,
    thumbnail_action_button_style,
    thumbnail_dropzone_style,
)
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QFrame, QSizePolicy,
    QGraphicsOpacityEffect,
)
from PySide6.QtGui import (
    QPixmap, QPainter, QPainterPath,
    QDragEnterEvent, QDropEvent,
)
from PySide6.QtCore import (
    Qt, QThread, Signal, QPropertyAnimation,
    QEasingCurve,
)


# ── tiny worker for URL downloads ──────────────────────────────────────────

class _UrlWorker(QThread):
    done = Signal(bool, str)   # (success, path_or_error)

    def __init__(self, store, name: str, url: str):
        super().__init__()
        self._store = store
        self._name  = name
        self._url   = url

    def run(self):
        ok, res = self._store.set_from_url(self._name, self._url)
        self.done.emit(ok, res)


# ── helpers ─────────────────────────────────────────────────────────────────

SUCCESS  = "#22c55e"
ERROR    = "#ef4444"

RADIUS = 12
THUMB_W, THUMB_H = 160, 240
logger = get_logger(__name__)


def _round_pixmap(src: QPixmap, w: int, h: int, r: int) -> QPixmap:
    scaled = src.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    x = (scaled.width()  - w) // 2
    y = (scaled.height() - h) // 2
    cropped = scaled.copy(x, y, w, h)
    out = QPixmap(w, h)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, w, h, r, r)
    p.setClipPath(path)
    p.drawPixmap(0, 0, cropped)
    p.end()
    return out


# ── drop zone ────────────────────────────────────────────────────────────────

class _DropZone(QFrame):
    """Click-to-browse + drag-and-drop target."""
    file_dropped = Signal(str)


    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet(thumbnail_dropzone_style(False))

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(8)

        self._icon = QLabel("⬆")
        self._icon.setAlignment(Qt.AlignCenter)
        self._icon.setStyleSheet(THUMBNAIL_DROPZONE_ICON_STYLE)
        layout.addWidget(self._icon)

        self._main_lbl = QLabel(t("thumbnail.drop_here"))
        self._main_lbl.setAlignment(Qt.AlignCenter)
        self._main_lbl.setStyleSheet(THUMBNAIL_DROPZONE_TITLE_STYLE)
        layout.addWidget(self._main_lbl)

        sub = QLabel("or click to browse  ·  jpg · png · webp")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet(THUMBNAIL_DROPZONE_SUBTITLE_STYLE)
        layout.addWidget(sub)

    # drag
    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.setStyleSheet(thumbnail_dropzone_style(True))
            self._icon.setStyleSheet(THUMBNAIL_DROPZONE_ICON_HOVER_STYLE)

    def dragLeaveEvent(self, e):
        self.setStyleSheet(thumbnail_dropzone_style(False))
        self._icon.setStyleSheet(THUMBNAIL_DROPZONE_ICON_STYLE)

    def dropEvent(self, e: QDropEvent):
        self.setStyleSheet(thumbnail_dropzone_style(False))
        self._icon.setStyleSheet(THUMBNAIL_DROPZONE_ICON_STYLE)
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff")):
                self.file_dropped.emit(path)

    # click to browse
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            from PySide6.QtWidgets import QFileDialog
            path, _ = QFileDialog.getOpenFileName(
                self, t("thumbnail.select_image"), "",
                t("thumbnail.file_filter")
            )
            if path:
                self.file_dropped.emit(path)
        super().mousePressEvent(e)


# ── main dialog ──────────────────────────────────────────────────────────────

class ThumbnailDialog(QDialog):
    """
    Modal thumbnail picker.
    After exec() == Accepted, read `self.saved_path` for the stored path.
    """

    def __init__(self, webtoon_name: str, settings_store, parent=None):
        super().__init__(parent)
        self._name       = webtoon_name
        self._store      = settings_store
        self._worker     = None
        self.saved_path  = None      # set on successful save

        self.setWindowTitle(t("thumbnail.window"))
        self.setModal(True)
        self.setFixedWidth(480)
        self.setStyleSheet(THUMBNAIL_DIALOG_STYLE)

        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(20)

        title = QLabel(t("thumbnail.title"))
        title.setStyleSheet(THUMBNAIL_DIALOG_TITLE_STYLE)
        root.addWidget(title)

        # body: preview + right panel
        body = QHBoxLayout()
        body.setSpacing(20)

        # left — preview card
        self._preview_label = QLabel()
        self._preview_label.setFixedSize(THUMB_W, THUMB_H)
        self._preview_label.setAlignment(Qt.AlignCenter)
        self._preview_label.setStyleSheet(THUMBNAIL_PREVIEW_STYLE)
        self._preview_label.setText(t("thumbnail.preview"))

        # fade-in effect for preview
        self._preview_effect = QGraphicsOpacityEffect(self._preview_label)
        self._preview_label.setGraphicsEffect(self._preview_effect)
        self._preview_effect.setOpacity(1.0)

        body.addWidget(self._preview_label)

        # right panel
        right = QVBoxLayout()
        right.setSpacing(16)

        # drop zone
        self._drop_zone = _DropZone()
        self._drop_zone.file_dropped.connect(self._handle_local_file)
        right.addWidget(self._drop_zone)

        # divider
        div_row = QHBoxLayout()
        div_row.setSpacing(8)
        for _ in range(2):
            line = QFrame()
            line.setFrameShape(QFrame.HLine)
            line.setStyleSheet(THUMBNAIL_DIVIDER_LINE_STYLE)
            div_row.addWidget(line)
        or_lbl = QLabel(t("thumbnail.or_paste_url"))
        or_lbl.setStyleSheet(THUMBNAIL_STATUS_IDLE_STYLE)
        or_lbl.setAlignment(Qt.AlignCenter)
        div_row.addWidget(or_lbl)
        for _ in range(2):
            line = QFrame()
            line.setFrameShape(QFrame.HLine)
            line.setStyleSheet(THUMBNAIL_DIVIDER_LINE_STYLE)
            div_row.addWidget(line)
        right.addLayout(div_row)

        # URL row
        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText(t("thumbnail.url_placeholder"))
        self._url_input.returnPressed.connect(self._handle_url)
        url_row.addWidget(self._url_input)
        fetch_btn = QPushButton(t("thumbnail.fetch"))
        fetch_btn.setFixedWidth(64)
        fetch_btn.setCursor(Qt.PointingHandCursor)
        fetch_btn.setStyleSheet(thumbnail_action_button_style())
        fetch_btn.clicked.connect(self._handle_url)
        url_row.addWidget(fetch_btn)
        right.addLayout(url_row)

        # status label
        self._status = QLabel("")
        self._status.setStyleSheet(THUMBNAIL_STATUS_IDLE_STYLE)
        self._status.setWordWrap(True)
        right.addWidget(self._status)

        right.addStretch()
        body.addLayout(right)
        root.addLayout(body)

        # footer buttons
        footer = QHBoxLayout()
        footer.addStretch()
        cancel_btn = QPushButton(t("thumbnail.cancel"))
        cancel_btn.setFixedWidth(90)
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setStyleSheet(thumbnail_action_button_style())
        cancel_btn.clicked.connect(self.reject)
        footer.addWidget(cancel_btn)

        self._apply_btn = QPushButton(t("thumbnail.apply"))
        self._apply_btn.setFixedWidth(90)
        self._apply_btn.setCursor(Qt.PointingHandCursor)
        self._apply_btn.setEnabled(False)
        self._apply_btn.setStyleSheet(thumbnail_action_button_style(primary=True))
        self._apply_btn.clicked.connect(self._apply)
        footer.addWidget(self._apply_btn)
        root.addLayout(footer)

        # load current thumbnail into preview if one exists
        current = self._store.get(self._name)
        if current and Path(current).exists():
            self._show_preview(current)

    # ── handlers ─────────────────────────────────────────────────────────

    def _handle_local_file(self, path: str):
        logger.info("Saving local thumbnail for %s from %s", self._name, path)
        self._set_status("Saving…", TEXT_DIM)
        saved = self._store.set(self._name, path)
        self.saved_path = saved
        self._show_preview(saved)
        self._set_status("✓  Image saved", SUCCESS)
        self._apply_btn.setEnabled(True)

    def _handle_url(self):
        url = self._url_input.text().strip()
        if not url:
            return
        logger.info("Downloading thumbnail for %s from %s", self._name, url)
        self._set_status("Downloading…", TEXT_DIM)
        self._url_input.setEnabled(False)
        self._worker = _UrlWorker(self._store, self._name, url)
        self._worker.done.connect(self._on_url_done)
        self._worker.start()

    def _on_url_done(self, success: bool, result: str):
        self._url_input.setEnabled(True)
        if success:
            logger.info("Thumbnail download succeeded for %s", self._name)
            self.saved_path = result
            self._show_preview(result)
            self._set_status("✓  Image downloaded and saved", SUCCESS)
            self._apply_btn.setEnabled(True)
        else:
            logger.warning("Thumbnail download failed for %s: %s", self._name, result)
            self._set_status(f"✕  {result}", ERROR)

    def _apply(self):
        if self.saved_path:
            self.accept()

    # ── helpers ──────────────────────────────────────────────────────────

    def _show_preview(self, path: str):
        px = QPixmap(path)
        if px.isNull():
            return
        rounded = _round_pixmap(px, THUMB_W, THUMB_H, RADIUS)

        # quick fade-in
        anim = QPropertyAnimation(self._preview_effect, b"opacity", self)
        anim.setDuration(200)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        self._preview_effect.setOpacity(0.0)
        self._preview_label.setPixmap(rounded)
        self._preview_label.setText("")
        anim.start()
        self._anim = anim   # keep reference

    def _set_status(self, msg: str, color: str = TEXT_DIM):
        self._status.setText(msg)
        self._status.setStyleSheet(status_label_color_style(color))
