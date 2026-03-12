from __future__ import annotations

import os
import re
import shutil

import qtawesome as qta

from app_logging import get_logger
from gui.common.styles import (
    DELETE_BUTTON_STYLE,
    EDIT_DIALOG_DELETE_BOX_STYLE,
    EDIT_DIALOG_DELETE_TEXT_STYLE,
    EDIT_DIALOG_FORM_FRAME_STYLE,
    EDIT_DIALOG_STYLE,
    EDIT_DIALOG_THUMB_PREVIEW_STYLE,
    EDIT_DIALOG_TITLE_STYLE,
    FORM_LABEL_STYLE,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.library.thumbnail_dialog import ThumbnailDialog
from gui.settings.settings_page import load_library_path, load_setting
from library_categories import load_custom_categories, save_custom_categories


CARD_W = 140
CARD_H = 210
CARD_RADIUS = 12
ROW_H = 40
logger = get_logger(__name__)


def _safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "", name).strip()


def _round_pixmap(src: QPixmap, w: int, h: int, radius: int) -> QPixmap:
    scaled = src.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    x = max(0, (scaled.width() - w) // 2)
    y = max(0, (scaled.height() - h) // 2)
    cropped = scaled.copy(x, y, w, h)

    out = QPixmap(w, h)
    out.fill(Qt.transparent)

    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, w, h, radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, cropped)
    painter.end()
    return out


class EditWebtoonDialog(QDialog):

    def __init__(self, webtoon, settings_store, progress_store, parent=None):
        super().__init__(parent)
        self.webtoon = webtoon
        self.settings_store = settings_store
        self.progress_store = progress_store
        self.deleted = False
        self._zoom_dirty = False
        self._initial_zoom_value = load_setting("viewer_zoom", 0.5)

        self.setWindowTitle("Edit Webtoon")
        self.setModal(True)
        self.resize(700, 0)
        self.setStyleSheet(EDIT_DIALOG_STYLE)

        self._build_ui()
        self._load_values()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(18)

        title = QLabel("Edit Webtoon")
        title.setStyleSheet(EDIT_DIALOG_TITLE_STYLE)
        root.addWidget(title)

        body = QHBoxLayout()
        body.setSpacing(20)

        preview_col = QVBoxLayout()
        preview_col.setSpacing(12)

        self.thumbnail_preview = QLabel("No thumbnail")
        self.thumbnail_preview.setAlignment(Qt.AlignCenter)
        self.thumbnail_preview.setFixedSize(CARD_W, CARD_H)
        self.thumbnail_preview.setStyleSheet(EDIT_DIALOG_THUMB_PREVIEW_STYLE)
        preview_col.addWidget(self.thumbnail_preview, alignment=Qt.AlignTop)

        thumb_btn_row = QHBoxLayout()
        thumb_btn_row.setSpacing(8)

        self.change_thumb_btn = QPushButton("Change Thumbnail")
        self.change_thumb_btn.setIcon(qta.icon("fa5s.image", color="#d8d8d8"))
        self.change_thumb_btn.clicked.connect(self._change_thumbnail)
        thumb_btn_row.addWidget(self.change_thumb_btn)

        self.reset_thumb_btn = QPushButton("Reset")
        self.reset_thumb_btn.setIcon(qta.icon("fa5s.undo", color="#d8d8d8"))
        self.reset_thumb_btn.clicked.connect(self._reset_thumbnail)
        thumb_btn_row.addWidget(self.reset_thumb_btn)

        preview_col.addLayout(thumb_btn_row)
        preview_col.addStretch()
        body.addLayout(preview_col)

        right = QVBoxLayout()
        right.setSpacing(14)

        form_frame = QFrame()
        form_frame.setStyleSheet(EDIT_DIALOG_FORM_FRAME_STYLE)
        form = QFormLayout(form_frame)
        form.setContentsMargins(16, 16, 16, 16)
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignLeft)
        form.setFormAlignment(Qt.AlignTop)

        self.name_input = QLineEdit()
        self.name_input.setFixedHeight(ROW_H)
        form.addRow(self._form_label("Name"), self._field_row(self.name_input))

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://example.com/series")
        self.url_input.setFixedHeight(ROW_H)
        form.addRow(self._form_label("Source URL"), self._field_row(self.url_input))

        self.zoom_input = QDoubleSpinBox()
        self.zoom_input.setDecimals(0)
        self.zoom_input.setRange(25, 100)
        self.zoom_input.setSingleStep(5)
        self.zoom_input.setSuffix("%")
        self.zoom_input.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.zoom_input.setFixedHeight(ROW_H)
        self.zoom_input.valueChanged.connect(self._mark_zoom_dirty)
        form.addRow(self._form_label("Zoom"), self._field_row(self.zoom_input))

        self.category_input = QComboBox()
        self.category_input.setEditable(True)
        self.category_input.setInsertPolicy(QComboBox.NoInsert)
        self.category_input.setFixedHeight(ROW_H)
        form.addRow(self._form_label("Category"), self._field_row(self.category_input))

        self.hide_filler_input = QCheckBox("Hide filler chapters for this webtoon")
        form.addRow(self._form_label("Filler"), self._field_row(self.hide_filler_input))

        self.completed_input = QCheckBox("Mark this webtoon as completed")
        form.addRow(self._form_label("Status"), self._field_row(self.completed_input))

        right.addWidget(form_frame)

        delete_box = QFrame()
        delete_box.setStyleSheet(EDIT_DIALOG_DELETE_BOX_STYLE)
        delete_layout = QHBoxLayout(delete_box)
        delete_layout.setContentsMargins(16, 14, 16, 14)
        delete_layout.setSpacing(12)

        delete_text = QLabel("Delete this webtoon from the library and remove its saved metadata.")
        delete_text.setWordWrap(True)
        delete_text.setStyleSheet(EDIT_DIALOG_DELETE_TEXT_STYLE)
        delete_layout.addWidget(delete_text, 1)

        self.delete_btn = QPushButton("Delete Webtoon")
        self.delete_btn.setIcon(qta.icon("fa5s.trash-alt", color="#ffffff"))
        self.delete_btn.setStyleSheet(DELETE_BUTTON_STYLE)
        self.delete_btn.clicked.connect(self._delete_webtoon)
        delete_layout.addWidget(self.delete_btn)

        right.addWidget(delete_box)
        right.addStretch()
        body.addLayout(right, 1)
        root.addLayout(body)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self._save)
        save_btn = buttons.button(QDialogButtonBox.Save)
        save_btn.setText("Save")
        save_btn.setIcon(qta.icon("fa5s.save", color="#ffffff"))
        cancel_btn = buttons.button(QDialogButtonBox.Cancel)
        cancel_btn.setText("Cancel")
        cancel_btn.setIcon(qta.icon("fa5s.times", color="#d8d8d8"))
        root.addWidget(buttons)

    def _load_values(self):
        self.name_input.setText(self.webtoon.name)
        self.url_input.setText(self.settings_store.get_source_url(self.webtoon.name) or "")

        zoom_override = self.settings_store.get_zoom_override(self.webtoon.name)
        base_zoom = float(zoom_override) if zoom_override is not None else float(load_setting("viewer_zoom", 0.5))
        self._initial_zoom_value = base_zoom * 100
        self.zoom_input.blockSignals(True)
        self.zoom_input.setValue(self._initial_zoom_value)
        self.zoom_input.blockSignals(False)

        self.hide_filler_input.setChecked(
            self.settings_store.get_hide_filler(self.webtoon.name)
        )
        self.completed_input.setChecked(
            self.settings_store.get_completed(self.webtoon.name)
        )
        self._update_thumbnail_preview()
        self._load_categories()

    def _load_categories(self):
        categories = load_custom_categories()
        current = self.settings_store.get_category(self.webtoon.name) or ""
        self.category_input.blockSignals(True)
        self.category_input.clear()
        self.category_input.addItem("")
        for category in categories:
            self.category_input.addItem(category)
        self.category_input.setCurrentText(current)
        self.category_input.blockSignals(False)

    def _form_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setFixedWidth(88)
        label.setFixedHeight(ROW_H)
        label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        label.setStyleSheet(FORM_LABEL_STYLE)
        return label

    def _field_row(self, widget):
        row = QWidget()
        row.setFixedHeight(ROW_H)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(widget, 1, Qt.AlignVCenter)
        return row

    def _mark_zoom_dirty(self, *_):
        self._zoom_dirty = True

    def _update_thumbnail_preview(self):
        pixmap = QPixmap(self.webtoon.thumbnail)
        if pixmap.isNull():
            self.thumbnail_preview.setPixmap(QPixmap())
            self.thumbnail_preview.clear()
            self.thumbnail_preview.setText("No thumbnail")
            return

        self.thumbnail_preview.setText("")
        self.thumbnail_preview.setPixmap(_round_pixmap(pixmap, CARD_W, CARD_H, CARD_RADIUS))

    def _change_thumbnail(self):
        logger.info("Opening thumbnail dialog for %s", self.webtoon.name)
        dlg = ThumbnailDialog(self.webtoon.name, self.settings_store, parent=self)
        if dlg.exec() == QDialog.Accepted and dlg.saved_path:
            self.webtoon.thumbnail = dlg.saved_path
            self._update_thumbnail_preview()

    def _reset_thumbnail(self):
        logger.info("Resetting custom thumbnail for %s", self.webtoon.name)
        self.settings_store.clear(self.webtoon.name)
        self.webtoon.thumbnail = os.path.join("data", "thumbnails", f"{self.webtoon.name}.jpg")
        self._update_thumbnail_preview()

    def _save(self):
        old_name = self.webtoon.name
        new_name = _safe_name(self.name_input.text())
        if not new_name:
            QMessageBox.warning(self, "Invalid name", "Name cannot be empty.")
            return

        library_path = load_library_path()
        old_path = self.webtoon.path
        new_path = os.path.join(library_path, new_name)

        if new_name != old_name and os.path.exists(new_path):
            QMessageBox.warning(self, "Name already exists", "A webtoon with that name already exists.")
            return

        try:
            if new_name != old_name:
                logger.info("Renaming webtoon from %s to %s", old_name, new_name)
                os.rename(old_path, new_path)
                self.settings_store.rename_webtoon(old_name, new_name)
                self.progress_store.rename_webtoon(old_name, new_name)
                self.webtoon.name = new_name
                self.webtoon.path = new_path
                auto_thumb = os.path.join("data", "thumbnails", f"{new_name}.jpg")
                custom_thumb = self.settings_store.get(new_name)
                self.webtoon.thumbnail = custom_thumb or auto_thumb

            source_url = self.url_input.text().strip()
            if source_url:
                self.settings_store.set_source_url(self.webtoon.name, source_url)
            else:
                self.settings_store.clear_source_url(self.webtoon.name)

            self.settings_store.set_hide_filler(
                self.webtoon.name,
                self.hide_filler_input.isChecked(),
            )
            self.settings_store.set_completed(
                self.webtoon.name,
                self.completed_input.isChecked(),
            )

            category = self.category_input.currentText().strip()
            if category:
                existing = load_custom_categories()
                if category not in existing:
                    existing.append(category)
                    save_custom_categories(existing)
                self.settings_store.set_category(self.webtoon.name, category)
                self.webtoon.category = category
            else:
                self.settings_store.clear_category(self.webtoon.name)
                self.webtoon.category = None

            if self._zoom_dirty:
                logger.info("Saving zoom override for %s", self.webtoon.name)
                self.settings_store.set_zoom_override(
                    self.webtoon.name,
                    float(self.zoom_input.value()) / 100.0,
                )

        except Exception as e:
            logger.error("Failed to save edit dialog changes for %s", old_name, exc_info=e)
            QMessageBox.critical(self, "Save failed", str(e))
            return

        logger.info("Edit dialog saved for %s", self.webtoon.name)
        self.accept()

    def _delete_webtoon(self):
        answer = QMessageBox.question(
            self,
            "Delete webtoon",
            f"Delete '{self.webtoon.name}' from the library?\n\nThis removes the folder, progress, thumbnail overrides, and saved settings.",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return

        try:
            logger.info("Deleting webtoon %s from edit dialog", self.webtoon.name)
            if os.path.isdir(self.webtoon.path):
                shutil.rmtree(self.webtoon.path)
            self.progress_store.clear(self.webtoon.name)
            self.settings_store.delete_webtoon(self.webtoon.name)
        except Exception as e:
            logger.error("Failed to delete webtoon %s", self.webtoon.name, exc_info=e)
            QMessageBox.critical(self, "Delete failed", str(e))
            return

        self.deleted = True
        self.accept()
