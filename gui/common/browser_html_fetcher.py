import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile

from core.app_logging import get_logger
from core.app_paths import app_root
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


class BrowserHtmlFetcher(QObject):
    _start_fetch_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._start_fetch_requested.connect(self._start_in_process_fetch, Qt.QueuedConnection)

    def fetch_html(self, url: str, site_name: str, timeout_ms: int = 30000) -> str:
        try:
            return self._fetch_html_subprocess(url, site_name, timeout_ms)
        except Exception as exc:
            logger.warning("Browser-backed subprocess fetch failed for %s: %s", url, exc)
            return self._fetch_html_in_process(url, site_name, timeout_ms)

    def _fetch_html_subprocess(self, url: str, site_name: str, timeout_ms: int) -> str:
        output_path = Path(tempfile.mkstemp(prefix="browser-fetch-", suffix=".json")[1])
        try:
            command = self._helper_command(site_name=site_name, url=url, timeout_ms=timeout_ms, output_path=output_path)
            logger.info("Browser-backed subprocess fetch starting for %s", url)
            env = os.environ.copy()
            env.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
            flags = str(env.get("QTWEBENGINE_CHROMIUM_FLAGS") or "").strip()
            if "--no-sandbox" not in flags:
                flags = f"{flags} --no-sandbox".strip()
            env["QTWEBENGINE_CHROMIUM_FLAGS"] = flags
            kwargs = {
                "cwd": str(app_root()),
                "timeout": max(5, int(timeout_ms / 1000) + 5),
                "check": False,
                "env": env,
                "capture_output": True,
                "text": True,
            }
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            completed = subprocess.run(command, **kwargs)
            if completed.returncode != 0:
                stderr = str(completed.stderr or "").strip()
                stdout = str(completed.stdout or "").strip()
                detail = stderr or stdout or "no output"
                raise RuntimeError(
                    f"Browser fetch helper exited with code {completed.returncode} for {url}: {detail}"
                )
            if not output_path.exists():
                raise RuntimeError(f"Browser fetch helper did not produce output for {url}")
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            if not bool(payload.get("ok")):
                raise RuntimeError(str(payload.get("error") or f"Browser fetch failed for {url}"))
            return str(payload.get("html") or "")
        finally:
            try:
                output_path.unlink(missing_ok=True)
            except Exception:
                pass

    def _fetch_html_in_process(self, url: str, site_name: str, timeout_ms: int) -> str:
        logger.info("Browser-backed in-process fetch fallback starting for %s", url)
        request = {
            "url": str(url),
            "site_name": str(site_name),
            "timeout_ms": max(5000, int(timeout_ms)),
            "done": threading.Event(),
            "result": {},
        }
        self._start_fetch_requested.emit(request)
        wait_seconds = max(5.0, request["timeout_ms"] / 1000.0 + 5.0)
        if not request["done"].wait(wait_seconds):
            raise RuntimeError(f"In-process browser fetch timed out for {url}")
        result = request["result"]
        if not bool(result.get("ok")):
            raise RuntimeError(str(result.get("error") or f"In-process browser fetch failed for {url}"))
        return str(result.get("html") or "")

    def _start_in_process_fetch(self, request: object):
        req = request if isinstance(request, dict) else {}
        profile = QWebEngineProfile(self)
        profile.setPersistentCookiesPolicy(QWebEngineProfile.NoPersistentCookies)
        profile.setHttpCacheType(QWebEngineProfile.MemoryHttpCache)
        user_agent = load_site_user_agent(req.get("site_name"), profile.httpUserAgent() or "Mozilla/5.0")
        if user_agent:
            profile.setHttpUserAgent(user_agent)

        page = _QuietBrowserFetchPage(profile)
        timer = QTimer(self)
        timer.setSingleShot(True)

        req["profile"] = profile
        req["page"] = page
        req["timer"] = timer

        def finish(payload: dict):
            if req.get("finished"):
                return
            req["finished"] = True
            req["result"] = dict(payload)
            try:
                timer.stop()
            except Exception:
                pass
            try:
                page.deleteLater()
            except Exception:
                pass
            try:
                profile.deleteLater()
            except Exception:
                pass
            done = req.get("done")
            if done is not None:
                done.set()

        def on_timeout():
            finish({"ok": False, "error": f"In-process browser fetch timed out for {req['url']}"})

        def on_load_finished(ok: bool):
            if not ok:
                finish({"ok": False, "error": f"In-process browser fetch failed to load {req['url']}"})
                return
            QTimer.singleShot(900, capture_html)

        def capture_html():
            if req.get("finished"):
                return
            page.toHtml(on_html_ready)

        def on_html_ready(html: str):
            finish({"ok": True, "html": str(html or "")})

        timer.timeout.connect(on_timeout)
        page.loadFinished.connect(on_load_finished)

        store = profile.cookieStore()
        origin = QUrl(site_base_url(req.get("site_name")) or req.get("url") or "")
        for cookie_data in load_site_cookies(req.get("site_name")):
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

        timer.start(int(req["timeout_ms"]))
        page.load(QUrl(req["url"]))

    def _helper_command(self, *, site_name: str, url: str, timeout_ms: int, output_path: Path) -> list[str]:
        if getattr(sys, "frozen", False):
            return [
                sys.executable,
                "--browser-fetch-helper",
                "--site-name", str(site_name),
                "--url", str(url),
                "--timeout-ms", str(int(timeout_ms)),
                "--output", str(output_path),
            ]
        return [
            sys.executable,
            str(app_root() / "main.py"),
            "--browser-fetch-helper",
            "--site-name", str(site_name),
            "--url", str(url),
            "--timeout-ms", str(int(timeout_ms)),
            "--output", str(output_path),
        ]
