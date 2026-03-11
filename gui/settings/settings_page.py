import json
import os

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QHBoxLayout,
    QPushButton, QLineEdit, QFileDialog
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
        self._save(DEFAULT_PATH)