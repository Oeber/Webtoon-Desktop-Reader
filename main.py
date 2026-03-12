import sys
from pathlib import Path
import ctypes
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from app_logging import setup_logging, get_logger
from gui.main_window import MainWindow

setup_logging()
logger = get_logger(__name__)

def _asset_path(*parts: str) -> Path:
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base_path.joinpath(*parts)

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

app_icon_path = _asset_path("imgs", "logo.png")
if app_icon_path.exists():
    icon = QIcon(str(app_icon_path))
    app.setWindowIcon(icon)
    logger.info("Application icon loaded from %s", app_icon_path)
else:
    logger.warning("Application icon not found at %s", app_icon_path)

window = MainWindow()
if app_icon_path.exists():
    window.setWindowIcon(icon)
window.show()
logger.info("Main window shown")

sys.exit(app.exec())
