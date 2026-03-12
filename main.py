import sys
from PySide6.QtWidgets import QApplication
from app_logging import setup_logging, get_logger
from gui.main_window import MainWindow

setup_logging()
logger = get_logger(__name__)

app = QApplication(sys.argv)
logger.info("QApplication created")

window = MainWindow()
window.show()
logger.info("Main window shown")

sys.exit(app.exec())
