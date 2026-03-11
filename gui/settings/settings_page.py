import json
import os

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QHBoxLayout,
    QPushButton, QLineEdit, QFileDialog, QCheckBox, QSlider
)
from PySide6.QtCore import Qt

CONFIG_FILE = "config.json"
DEFAULT_PATH = "webtoons"


def load_library_path() -> str:
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f).get("library_path", DEFAULT_PATH)
    except FileNotFoundError:
        return DEFAULT_PATH


def save_library_path(path: str):
    try:
        with open(CONFIG_FILE, "r") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    data["library_path"] = path
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=4)

def load_setting(key: str, default):
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f).get(key, default)
    except FileNotFoundError:
        return default


def save_setting(key: str, value):
    try:
        with open(CONFIG_FILE, "r") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}

    data[key] = value

    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=4)


LABEL_STYLE = "color: #aaaaaa; font-size: 12px;"
SECTION_STYLE = "color: #ffffff; font-size: 13px; font-weight: bold;"

INPUT_STYLE = """
    QLineEdit {
        background: #1a1a1a;
        border: 1px solid #333;
        border-radius: 6px;
        padding: 6px 10px;
        color: #eeeeee;
        font-size: 13px;
    }
    QLineEdit:focus { border: 1px solid #555; }
"""

BTN_STYLE = """
    QPushButton {
        background-color: #2a2a2a;
        color: #cccccc;
        border: 1px solid #333;
        border-radius: 6px;
        padding: 6px 16px;
        font-size: 13px;
    }
    QPushButton:hover { background-color: #333; }
    QPushButton:pressed { background-color: #3a3a3a; }
"""


class SettingsPage(QWidget):

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background-color: #121212;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(24)
        layout.setAlignment(Qt.AlignTop)

        title = QLabel("Settings")
        title.setStyleSheet("color: #ffffff; font-size: 20px; font-weight: bold; background: transparent;")
        layout.addWidget(title)

        # --- Library folder ---
        folder_label = QLabel("Webtoon Library Folder")
        folder_label.setStyleSheet(SECTION_STYLE + " background: transparent;")
        layout.addWidget(folder_label)

        row = QHBoxLayout()
        row.setSpacing(8)

        self.path_input = QLineEdit()
        self.path_input.setText(load_library_path())
        self.path_input.setStyleSheet(INPUT_STYLE)
        self.path_input.editingFinished.connect(self._on_path_edited)

        browse_btn = QPushButton("Browse")
        browse_btn.setStyleSheet(BTN_STYLE)
        browse_btn.setFixedWidth(90)
        browse_btn.clicked.connect(self._browse)

        row.addWidget(self.path_input)
        row.addWidget(browse_btn)
        layout.addLayout(row)

        # --- Reader settings ---
        reader_label = QLabel("Reader")
        reader_label.setStyleSheet(SECTION_STYLE + " background: transparent;")
        layout.addWidget(reader_label)

        self.auto_skip_checkbox = QCheckBox("Enable auto panel skip")
        self.auto_skip_checkbox.setChecked(load_setting("viewer_auto_skip", True))
        self.auto_skip_checkbox.setStyleSheet("""
            QCheckBox {
                color: #dddddd;
                font-size: 13px;
                spacing: 8px;
                background: transparent;
            }
        """)
        self.auto_skip_checkbox.toggled.connect(self._on_auto_skip_changed)
        layout.addWidget(self.auto_skip_checkbox)

        zoom_row = QHBoxLayout()
        zoom_row.setSpacing(10)

        zoom_text = QLabel("Default zoom")
        zoom_text.setStyleSheet(LABEL_STYLE + " background: transparent;")
        zoom_text.setFixedWidth(90)

        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setMinimum(25)
        self.zoom_slider.setMaximum(100)
        self.zoom_slider.setValue(int(load_setting("viewer_zoom", 0.5) * 100))
        self.zoom_slider.valueChanged.connect(self._on_zoom_changed)

        self.zoom_value_label = QLabel(f"{self.zoom_slider.value()}%")
        self.zoom_value_label.setStyleSheet("color: #cccccc; font-size: 12px; background: transparent;")
        self.zoom_value_label.setFixedWidth(40)

        zoom_row.addWidget(zoom_text)
        zoom_row.addWidget(self.zoom_slider)
        zoom_row.addWidget(self.zoom_value_label)
        layout.addLayout(zoom_row)

        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.setStyleSheet(BTN_STYLE)
        reset_btn.setFixedWidth(140)
        reset_btn.clicked.connect(self._reset)
        layout.addWidget(reset_btn)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #666; font-size: 12px; background: transparent;")
        layout.addWidget(self.status_label)

        layout.addStretch()
    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Library Folder")
        if folder:
            self.path_input.setText(folder)
            self._save(folder)

    def _on_path_edited(self):
        self._save(self.path_input.text().strip())

    def _save(self, path: str):
        if not os.path.isdir(path):
            self.status_label.setText("⚠ Folder not found.")
            return
        save_library_path(path)
        self.status_label.setText("✔ Saved.")
        self.main_window.library.load_library()

    def _reset(self):
        self.path_input.setText(DEFAULT_PATH)
        save_setting("viewer_auto_skip", True)
        save_setting("viewer_zoom", 0.5)

        self.auto_skip_checkbox.blockSignals(True)
        self.auto_skip_checkbox.setChecked(True)
        self.auto_skip_checkbox.blockSignals(False)

        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(50)
        self.zoom_slider.blockSignals(False)
        self.zoom_value_label.setText("50%")

        viewer = getattr(self.main_window, "viewer", None)
        if viewer is not None:
            viewer.auto_skip_enabled = True
            if hasattr(viewer, "nav_toggle"):
                viewer.nav_toggle.blockSignals(True)
                viewer.nav_toggle.setChecked(True)
                viewer.nav_toggle.setText("Auto Skip")
                viewer.nav_toggle.blockSignals(False)

            viewer._zoom = 0.5
            if hasattr(viewer, "_zoom_slider"):
                viewer._zoom_slider.blockSignals(True)
                viewer._zoom_slider.setValue(50)
                viewer._zoom_slider.blockSignals(False)
            if hasattr(viewer, "_zoom_label"):
                viewer._zoom_label.setText("50%")
            if hasattr(viewer, "preview"):
                viewer.preview.set_zoom(0.5)
            if getattr(viewer, "image_labels", None):
                viewer.rescale_images()

        self._save(DEFAULT_PATH)

    def _on_auto_skip_changed(self, checked: bool):
        save_setting("viewer_auto_skip", checked)
        self.status_label.setText("✔ Reader settings saved.")

        viewer = getattr(self.main_window, "viewer", None)
        if viewer is not None:
            viewer.auto_skip_enabled = checked
            if hasattr(viewer, "nav_toggle"):
                viewer.nav_toggle.blockSignals(True)
                viewer.nav_toggle.setChecked(checked)
                viewer.nav_toggle.setText("Auto Skip" if checked else "Standard")
                viewer.nav_toggle.blockSignals(False)

    def _on_zoom_changed(self, value: int):
        zoom = value / 100.0
        self.zoom_value_label.setText(f"{value}%")
        save_setting("viewer_zoom", zoom)
        self.status_label.setText("✔ Reader settings saved.")

        viewer = getattr(self.main_window, "viewer", None)
        if viewer is not None:
            viewer._zoom = zoom
            if hasattr(viewer, "_zoom_slider"):
                viewer._zoom_slider.blockSignals(True)
                viewer._zoom_slider.setValue(value)
                viewer._zoom_slider.blockSignals(False)
            if hasattr(viewer, "_zoom_label"):
                viewer._zoom_label.setText(f"{value}%")
            if hasattr(viewer, "preview"):
                viewer.preview.set_zoom(zoom)
            if getattr(viewer, "image_labels", None):
                viewer.rescale_images()            