from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout
from PySide6.QtGui import QPixmap

class WebtoonCard(QWidget):

    def __init__(self, webtoon):

        super().__init__()

        layout = QVBoxLayout()

        image = QLabel()
        pixmap = QPixmap(webtoon.thumbnail)
        image.setPixmap(pixmap.scaled(200, 300))

        title = QLabel(webtoon.name)

        layout.addWidget(image)
        layout.addWidget(title)

        self.setLayout(layout)