import sys
import os
import ctypes
import argparse
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from core.app_logging import setup_logging, get_logger
from core.app_paths import resource_path
from gui.common.strings import set_locale

logger = None

def _set_windows_app_id():
    if sys.platform != "win32":
        return
    app_id = "reader.desktop.app"
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        logger.info("Windows AppUserModelID set to %s", app_id)
    except Exception:
        logger.exception("Failed to set Windows AppUserModelID")

def _helper_args(argv: list[str]) -> argparse.Namespace | None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--browser-fetch-helper', action='store_true')
    parser.add_argument('--site-name')
    parser.add_argument('--url')
    parser.add_argument('--timeout-ms', type=int, default=30000)
    parser.add_argument('--output')
    known, _unknown = parser.parse_known_args(argv[1:])
    if not known.browser_fetch_helper:
        return None
    return known


def main(argv: list[str] | None = None) -> int:
    global logger
    raw_argv = argv or sys.argv
    setup_logging()
    logger = get_logger(__name__)

    from stores.settings_store import APP_LOCALE_KEY, load_setting

    set_locale(str(load_setting(APP_LOCALE_KEY, "en") or "en").strip() or "en")

    helper = _helper_args(list(raw_argv))
    if helper is not None:
        os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
        flags = str(os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS") or "").strip()
        if "--no-sandbox" not in flags:
            os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = f"{flags} --no-sandbox".strip()
        from core.browser_fetch_runtime import run_browser_fetch_helper
        _set_windows_app_id()
        app = QApplication(list(raw_argv))
        return run_browser_fetch_helper(
            helper.site_name or '',
            helper.url or '',
            int(helper.timeout_ms or 30000),
            helper.output or '',
        )

    from core.app_update import APP_NAME, APP_VERSION
    from core.library_layout import ensure_library_content_layout
    from core.profiler import create_session_profiler
    from gui.main_window import MainWindow
    from stores.settings_store import load_library_path
    from stores.db import prewarm_connection, prewarm_connection_async

    profiler, app_argv = create_session_profiler(raw_argv, logger)
    profiler.start()

    _set_windows_app_id()

    app = QApplication(app_argv)
    logger.info("QApplication created")
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setFont(QFont("Segoe UI", 10))

    app_icon_path = resource_path("imgs", "logo.png")
    icon = None
    if app_icon_path.exists():
        icon = QIcon(str(app_icon_path))
        app.setWindowIcon(icon)
        logger.info("Application icon loaded from %s", app_icon_path)
    else:
        logger.warning("Application icon not found at %s", app_icon_path)

    window = MainWindow()
    ensure_library_content_layout(load_library_path(), window.settings_store)
    if icon is not None:
        window.setWindowIcon(icon)
    app.aboutToQuit.connect(profiler.stop)
    app.aboutToQuit.connect(window.shutdown_background_tasks)
    window.show()
    prewarm_connection_async()
    QTimer.singleShot(0, prewarm_connection)
    logger.info("Main window shown")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
