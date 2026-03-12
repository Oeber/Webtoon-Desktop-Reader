import logging
import sys
from datetime import datetime
from pathlib import Path

from core.app_paths import data_path


LOG_DIR = data_path("logs")
CURRENT_LOG = LOG_DIR / "current.log"
MAX_ARCHIVES = 5

_configured = False


def setup_logging() -> Path:
    global _configured
    if _configured:
        return CURRENT_LOG

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _rotate_current_log()

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(CURRENT_LOG, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    sys.excepthook = _handle_uncaught_exception
    _configured = True

    logging.getLogger(__name__).info("Logging initialized at %s", CURRENT_LOG)
    return CURRENT_LOG


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def current_log_path() -> Path:
    return CURRENT_LOG


def archived_log_paths() -> list[Path]:
    if not LOG_DIR.exists():
        return []
    return sorted(
        (path for path in LOG_DIR.glob("*.log") if path.name != CURRENT_LOG.name),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def _rotate_current_log():
    if not CURRENT_LOG.exists():
        return

    if CURRENT_LOG.stat().st_size > 0:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        archive_path = LOG_DIR / f"session-{stamp}.log"
        CURRENT_LOG.replace(archive_path)
    else:
        CURRENT_LOG.unlink(missing_ok=True)

    archives = archived_log_paths()
    for stale in archives[MAX_ARCHIVES:]:
        stale.unlink(missing_ok=True)


def _handle_uncaught_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logging.getLogger("app.crash").critical(
        "Unhandled exception",
        exc_info=(exc_type, exc_value, exc_traceback),
    )
