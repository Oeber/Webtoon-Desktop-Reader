from PySide6.QtWidgets import QMainWindow, QStackedWidget
from gui.library.library_page import LibraryPage
from gui.viewer.viewer_page import ViewerPage
from gui.settings.settings_page import SettingsPage

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()

        self.stack = QStackedWidget()


        self.resize(1400, 900)
        self.library = LibraryPage(self)
        self.viewer = ViewerPage(self)
        self.settings = SettingsPage(self)

        self.stack.addWidget(self.library)
        self.stack.addWidget(self.viewer)
        self.stack.addWidget(self.settings)

        self.setCentralWidget(self.stack)

    def open_viewer(self, webtoon):
        self.viewer.load_webtoon(webtoon)
        self.stack.setCurrentWidget(self.viewer)