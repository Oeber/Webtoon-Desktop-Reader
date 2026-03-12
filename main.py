import sys
import ctypes
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from app_logging import setup_logging, get_logger
from app_paths import resource_path
from gui.main_window import MainWindow

setup_logging()
logger = get_logger(__name__)

def _set_windows_app_id():
    if sys.platform != "win32":
        return
    app_id = "reader.desktop.app"
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        logger.info("Windows AppUserModelID set to %s", app_id)
    except Exception:
        logger.exception("Failed to set Windows AppUserModelID")

_set_windows_app_id()

app = QApplication(sys.argv)
logger.info("QApplication created")
app.setApplicationName("Webtoon Desktop Reader")

app_icon_path = resource_path("imgs", "logo.png")
if app_icon_path.exists():
    icon = QIcon(str(app_icon_path))
    app.setWindowIcon(icon)
    logger.info("Application icon loaded from %s", app_icon_path)
else:
    logger.warning("Application icon not found at %s", app_icon_path)

window = MainWindow()
if app_icon_path.exists():
    window.setWindowIcon(icon)
app.aboutToQuit.connect(window.shutdown_background_tasks)
window.show()
logger.info("Main window shown")

sys.exit(app.exec())
