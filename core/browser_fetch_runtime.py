import json
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWidgets import QApplication

from core.app_logging import get_logger
from core.site_session import load_site_cookies, load_site_user_agent, site_base_url

logger = get_logger(__name__)


class _QuietBrowserFetchPage(QWebEnginePage):
    _IGNORED_CONSOLE_PATTERNS = (
        "[FuzzySearch] Fuse.js library not loaded.",
        "[FuzzySearch] Fuse.js library is not loaded.",
        "Cannot read properties of null (reading 'addEventListener')",
        "Identifier 'closeOtherToggles' has already been declared",
    )

    def javaScriptConsoleMessage(self, level, message: str, line_number: int, source_id: str):
        text = " ".join(str(message or "").split())
        if any(pattern in text for pattern in self._IGNORED_CONSOLE_PATTERNS):
            return
        source = str(source_id or "").strip() or "<inline>"
        level_name = getattr(level, "name", None) or str(level)
        logger.debug("Browser fetch JS console [%s] %s (%s:%s)", level_name, text, source, line_number)


class BrowserFetchRunner:
    def __init__(self, site_name: str, url: str, timeout_ms: int, output_path: str):
        self.site_name = str(site_name or "").strip()
        self.url = str(url or "").strip()
        self.timeout_ms = max(5000, int(timeout_ms))
        self.output_path = Path(output_path)
        self.profile = None
        self.page = None
        self.timer = None
        self.app = QApplication.instance()
        self.finished = False

    def run(self) -> int:
        if self.app is None:
            raise RuntimeError("QApplication is required for browser fetch helper")
        self.profile = QWebEngineProfile()
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.NoPersistentCookies)
        self.profile.setHttpCacheType(QWebEngineProfile.MemoryHttpCache)
        user_agent = load_site_user_agent(self.site_name, self.profile.httpUserAgent() or "Mozilla/5.0")
        if user_agent:
            self.profile.setHttpUserAgent(user_agent)

        self.page = _QuietBrowserFetchPage(self.profile)
        self.page.loadFinished.connect(self._on_load_finished)

        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._on_timeout)

        store = self.profile.cookieStore()
        origin = QUrl(site_base_url(self.site_name) or self.url)
        for cookie_data in load_site_cookies(self.site_name):
            name = str(cookie_data.get("name") or "").strip()
            if not name:
                continue
            cookie = QNetworkCookie(name.encode("utf-8"), str(cookie_data.get("value") or "").encode("utf-8"))
            domain = str(cookie_data.get("domain") or "").strip()
            if domain:
                cookie.setDomain(domain)
            cookie.setPath(str(cookie_data.get("path") or "/").strip() or "/")
            cookie.setSecure(bool(cookie_data.get("secure", False)))
            store.setCookie(cookie, origin)

        logger.info("Browser helper loading %s", self.url)
        self.timer.start(self.timeout_ms)
        self.page.load(QUrl(self.url))
        self.app.exec()
        return 0 if self.finished else 1

    def _write_payload(self, payload: dict):
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')

    def _finish(self, payload: dict):
        if self.finished:
            return
        self.finished = True
        self._write_payload(payload)
        if self.timer is not None:
            self.timer.stop()
        if self.page is not None:
            try:
                self.page.deleteLater()
            except Exception:
                pass
        if self.profile is not None:
            try:
                self.profile.deleteLater()
            except Exception:
                pass
        if self.app is not None:
            self.app.quit()

    def _on_timeout(self):
        self._finish({"ok": False, "error": f"Browser fetch timed out for {self.url}"})

    def _on_load_finished(self, ok: bool):
        if not ok:
            self._finish({"ok": False, "error": f"Browser fetch failed to load {self.url}"})
            return
        QTimer.singleShot(900, self._capture_html)

    def _capture_html(self):
        if self.page is None or self.finished:
            return
        self.page.toHtml(self._on_html_ready)

    def _on_html_ready(self, html: str):
        self._finish({"ok": True, "html": str(html or "")})


def run_browser_fetch_helper(site_name: str, url: str, timeout_ms: int, output_path: str) -> int:
    runner = BrowserFetchRunner(site_name, url, timeout_ms, output_path)
    return runner.run()
