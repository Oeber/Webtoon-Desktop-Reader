import json

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QFileDialog,
    QLineEdit
)


CONFIG_FILE = "config.json"


class SettingsPage(QWidget):

    def __init__(self, main_window):
        super().__init__()

        self.main_window = main_window

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Webtoon Library Folder"))

        self.path_input = QLineEdit()

        browse_button = QPushButton("Browse")
        browse_button.clicked.connect(self.browse_folder)

        save_button = QPushButton("Save Settings")
        save_button.clicked.connect(self.save_settings)

        layout.addWidget(self.path_input)
        layout.addWidget(browse_button)
        layout.addWidget(save_button)

        self.load_settings()

    def browse_folder(self):

        folder = QFileDialog.getExistingDirectory(self, "Select Library Folder")

        if folder:
            self.path_input.setText(folder)

    def load_settings(self):

        try:

            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)

                self.path_input.setText(data.get("library_path", ""))

        except FileNotFoundError:
            pass

    def save_settings(self):

        data = {
            "library_path": self.path_input.text()
        }

        with open(CONFIG_FILE, "w") as f:
            json.dump(data, f, indent=4)