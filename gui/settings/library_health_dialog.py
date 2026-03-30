from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from gui.common.styles import (
    BUTTON_STYLE,
    LOG_VIEW_STYLE,
    PAGE_BG_STYLE,
    STATUS_LABEL_STYLE,
    SURFACE_PANEL_STYLE,
    TEXT_MUTED_TRANSPARENT_STYLE,
)
from library.health_tools import (
    analyze_library_health,
    cleanup_orphaned_metadata,
    delete_empty_chapter_folders,
    delete_invalid_series_folders,
    remove_orphaned_thumbnail_files,
)
from stores.settings_store import load_library_path
from gui.common.strings import t


class LibraryHealthDialog(QDialog):
    def __init__(self, main_window, parent=None):
        super().__init__(parent or main_window)
        self.main_window = main_window
        self._report = None

        self.setWindowTitle(t("library_health.window"))
        self.setModal(True)
        self.resize(760, 620)
        self.setStyleSheet(PAGE_BG_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        panel = QLabel()
        panel.setStyleSheet(SURFACE_PANEL_STYLE)
        panel.setFixedHeight(0)
        panel.hide()

        title = QLabel(t("library_health.title"))
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #f3ece8;")
        layout.addWidget(title)

        help_text = QLabel(
            t("library_health.help")
        )
        help_text.setWordWrap(True)
        help_text.setStyleSheet(TEXT_MUTED_TRANSPARENT_STYLE)
        layout.addWidget(help_text)

        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet(TEXT_MUTED_TRANSPARENT_STYLE)
        layout.addWidget(self.summary_label)

        self.details_view = QTextEdit(self)
        self.details_view.setReadOnly(True)
        self.details_view.setStyleSheet(LOG_VIEW_STYLE)
        layout.addWidget(self.details_view, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)

        self.refresh_btn = QPushButton(t("library_health.refresh"))
        self.refresh_btn.setStyleSheet(BUTTON_STYLE)
        self.refresh_btn.clicked.connect(self.refresh_report)
        actions.addWidget(self.refresh_btn)

        self.cleanup_metadata_btn = QPushButton(t("library_health.clear_metadata"))
        self.cleanup_metadata_btn.setStyleSheet(BUTTON_STYLE)
        self.cleanup_metadata_btn.clicked.connect(self._cleanup_orphaned_metadata)
        actions.addWidget(self.cleanup_metadata_btn)

        self.delete_invalid_btn = QPushButton(t("library_health.delete_empty"))
        self.delete_invalid_btn.setStyleSheet(BUTTON_STYLE)
        self.delete_invalid_btn.clicked.connect(self._delete_invalid_folders)
        actions.addWidget(self.delete_invalid_btn)

        self.remove_thumbs_btn = QPushButton(t("library_health.remove_thumbs"))
        self.remove_thumbs_btn.setStyleSheet(BUTTON_STYLE)
        self.remove_thumbs_btn.clicked.connect(self._remove_orphaned_thumbnails)
        actions.addWidget(self.remove_thumbs_btn)

        actions.addStretch()

        close_btn = QPushButton(t("library_health.close"))
        close_btn.setStyleSheet(BUTTON_STYLE)
        close_btn.clicked.connect(self.accept)
        actions.addWidget(close_btn)

        layout.addLayout(actions)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(STATUS_LABEL_STYLE)
        layout.addWidget(self.status_label)

        self.refresh_report()

    def refresh_report(self):
        self._report = analyze_library_health(
            load_library_path(),
            self.main_window.library.settings_store,
        )
        self.summary_label.setText(" | ".join(self._report.summary_lines()))
        self.details_view.setPlainText(self._report.details_text())
        self.cleanup_metadata_btn.setEnabled(bool(self._report.orphaned_settings or self._report.orphaned_progress))
        self.delete_invalid_btn.setEnabled(bool(self._report.invalid_series_folders or self._report.empty_chapter_folders))
        self.remove_thumbs_btn.setEnabled(bool(self._report.orphaned_thumbnail_files))
        self.status_label.setText(t("library_health.scan_complete"))

    def _cleanup_orphaned_metadata(self):
        if self._report is None:
            return
        if not (self._report.orphaned_settings or self._report.orphaned_progress):
            self.status_label.setText(t("library_health.no_metadata"))
            return
        answer = QMessageBox.question(
            self,
            t("library_health.clear_metadata_title"),
            t("library_health.clear_metadata_text"),
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        removed = cleanup_orphaned_metadata(
            self._report,
            self.main_window.library.settings_store,
            self.main_window.library.progress_store,
        )
        self.main_window.library.load_library()
        self.refresh_report()
        self.status_label.setText(t("library_health.metadata_removed", removed=removed))

    def _delete_invalid_folders(self):
        if self._report is None:
            return
        if not (self._report.invalid_series_folders or self._report.empty_chapter_folders):
            self.status_label.setText(t("library_health.no_empty"))
            return
        answer = QMessageBox.question(
            self,
            t("library_health.delete_empty_title"),
            t("library_health.delete_empty_text"),
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        removed = delete_invalid_series_folders(self._report) + delete_empty_chapter_folders(self._report)
        self.main_window.library.load_library()
        self.refresh_report()
        self.status_label.setText(t("library_health.empty_deleted", removed=removed))

    def _remove_orphaned_thumbnails(self):
        if self._report is None:
            return
        if not self._report.orphaned_thumbnail_files:
            self.status_label.setText(t("library_health.no_thumbs"))
            return
        answer = QMessageBox.question(
            self,
            t("library_health.remove_thumbs_title"),
            t("library_health.remove_thumbs_text"),
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        removed = remove_orphaned_thumbnail_files(self._report)
        self.refresh_report()
        self.status_label.setText(t("library_health.thumbs_removed", removed=removed))
