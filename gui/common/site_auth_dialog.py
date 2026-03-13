from urllib.parse import urlparse

import requests
from PySide6.QtCore import QTimer, QUrl
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout

from core.app_logging import get_logger
from core.site_session import (
    has_required_site_cookies,
    matching_session_cookie_names,
    save_site_cookies,
    save_site_user_agent,
    site_base_url,
    site_display_name,
    site_host,
    site_required_cookie_names,
)
from gui.common.styles import BUTTON_STYLE, PAGE_BG_STYLE, STATUS_LABEL_STYLE

logger = get_logger(__name__)


class SiteAuthDialog(QDialog):

    def __init__(self, site_name: str, url: str = "", parent=None):
        super().__init__(parent)
        self.site_name = str(site_name or "").strip()
        self.site_label = site_display_name(self.site_name)
        self.site_home_url = site_base_url(self.site_name)
        self.site_host = site_host(self.site_name)
        self._cookie_map: dict[tuple[str, str, str], dict] = {}
        self._cookie_store = None
        self._cleaned_up = False
        self._auto_return_pending = False
        self._validated_session_ready = False
        self._last_validation_error = ""
        self._auto_accept_pending = False

        self.setWindowTitle(f"Authorize {self.site_label}")
        self.resize(1180, 820)
        self.setStyleSheet(PAGE_BG_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        self.info_label = QLabel(
            f"Open {self.site_label} below in a fresh browser session, complete any challenge or login, then click Save Session."
        )
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet("color: #fff0ec; font-size: 13px; background: transparent;")
        layout.addWidget(self.info_label)

        self.status_label = QLabel("Waiting for page load...")
        self.status_label.setStyleSheet(STATUS_LABEL_STYLE)
        layout.addWidget(self.status_label)

        self.token_label = QLabel("")
        self.token_label.setWordWrap(True)
        self.token_label.setStyleSheet("color: #d8b7b0; font-size: 12px; background: transparent;")
        layout.addWidget(self.token_label)

        self.profile = QWebEngineProfile(self)
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.NoPersistentCookies)
        self.profile.setHttpCacheType(QWebEngineProfile.MemoryHttpCache)

        self.page = QWebEnginePage(self.profile, self)
        self.page.loadStarted.connect(self._on_load_started)
        self.page.loadFinished.connect(self._on_load_finished)
        self.page.urlChanged.connect(self._on_url_changed)
        self.page.titleChanged.connect(self._on_title_changed)
        self.view = QWebEngineView(self)
        self.view.setPage(self.page)
        layout.addWidget(self.view, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)

        self.reload_btn = QPushButton("Reload")
        self.reload_btn.setStyleSheet(BUTTON_STYLE)
        self.reload_btn.clicked.connect(self.view.reload)
        buttons.addWidget(self.reload_btn)

        self.home_btn = QPushButton("Open Home")
        self.home_btn.setStyleSheet(BUTTON_STYLE)
        self.home_btn.clicked.connect(self._open_home)
        buttons.addWidget(self.home_btn)

        self.check_btn = QPushButton("Check Session")
        self.check_btn.setStyleSheet(BUTTON_STYLE)
        self.check_btn.clicked.connect(self._check_session)
        buttons.addWidget(self.check_btn)

        buttons.addStretch()

        self.cancel_btn = QPushButton("Close")
        self.cancel_btn.setStyleSheet(BUTTON_STYLE)
        self.cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_btn)

        self.save_btn = QPushButton("Save Session")
        self.save_btn.setStyleSheet(BUTTON_STYLE)
        self.save_btn.clicked.connect(self._save_and_accept)
        self.save_btn.setEnabled(False)
        buttons.addWidget(self.save_btn)

        layout.addLayout(buttons)

        self._cookie_store = self.profile.cookieStore()
        self._cookie_store.cookieAdded.connect(self._on_cookie_added)
        self._cookie_store.cookieRemoved.connect(self._on_cookie_removed)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1200)
        self._poll_timer.timeout.connect(self._refresh_cookie_snapshot)
        self._poll_timer.start()

        self._refresh_cookie_snapshot()

        start_url = str(url or "").strip() or self.site_home_url
        self.view.load(QUrl(start_url))

    def _open_home(self):
        self._auto_return_pending = False
        if self.site_home_url:
            self.view.load(QUrl(self.site_home_url))

    def _on_load_started(self):
        self.status_label.setText("Loading page...")

    def _on_load_finished(self, ok: bool):
        self._refresh_cookie_snapshot()
        current_url = self.view.url().toString()
        if ok:
            self.status_label.setText(self._status_text(f"Loaded {current_url}", current_url=current_url))
            self._maybe_auto_return_home(current_url)
        else:
            self.status_label.setText(f"Failed to load {current_url}")

    def _on_url_changed(self, _url):
        self._refresh_cookie_snapshot()

    def _on_title_changed(self, _title: str):
        self._refresh_cookie_snapshot()

    def _on_cookie_added(self, cookie: QNetworkCookie):
        data = self._serialize_cookie(cookie)
        if data is None:
            return
        key = (data["name"], data["domain"], data["path"])
        self._cookie_map[key] = data
        self._validated_session_ready = False
        self._last_validation_error = ""
        self._update_status_labels()

    def _on_cookie_removed(self, cookie: QNetworkCookie):
        data = self._serialize_cookie(cookie)
        if data is None:
            return
        key = (data["name"], data["domain"], data["path"])
        self._cookie_map.pop(key, None)
        self._validated_session_ready = False
        self._last_validation_error = ""
        self._update_status_labels()

    def _serialize_cookie(self, cookie: QNetworkCookie) -> dict | None:
        name = bytes(cookie.name()).decode("utf-8", "ignore").strip()
        value = bytes(cookie.value()).decode("utf-8", "ignore")
        domain = str(cookie.domain() or "").strip()
        path = str(cookie.path() or "/").strip() or "/"
        if not name:
            return None
        if self.site_host and domain and self.site_host not in domain.lstrip(".").casefold():
            return None
        expires = None
        expiration = cookie.expirationDate()
        if expiration.isValid():
            expires = int(expiration.toSecsSinceEpoch())
        return {
            "name": name,
            "value": value,
            "domain": domain,
            "path": path,
            "secure": cookie.isSecure(),
            "expires": expires,
        }

    def _saved_cookie_count(self) -> int:
        return len(self._cookie_map)

    def _has_required_cookies(self) -> bool:
        return has_required_site_cookies(self.site_name, list(self._cookie_map.values()))

    def _has_reusable_session(self) -> bool:
        return self._has_required_cookies() or self._validated_session_ready

    def _refresh_cookie_snapshot(self):
        if self._cookie_store is None:
            return
        try:
            self._cookie_store.loadAllCookies()
        except Exception:
            logger.exception("Failed to refresh cookie snapshot for %s", self.site_name)
            return
        self._update_status_labels()

    def _browser_user_agent(self) -> str:
        return self.profile.httpUserAgent() if self.profile is not None else "Mozilla/5.0"

    def _captured_cookies(self) -> list[dict]:
        return list(self._cookie_map.values())

    def _validation_headers(self) -> dict[str, str]:
        return {
            "User-Agent": self._browser_user_agent(),
            "Referer": self.site_home_url,
        }

    def _looks_like_block_page(self, response: requests.Response) -> bool:
        if response.status_code == 403:
            return True
        text = str(getattr(response, "text", "") or "").casefold()
        return "cloudflare" in text and "just a moment" in text

    def _validate_session(self) -> bool:
        cookies = self._captured_cookies()
        if not cookies:
            self._validated_session_ready = False
            self._last_validation_error = "No captured cookies yet."
            return False
        session = requests.Session()
        try:
            for cookie in cookies:
                kwargs = {
                    "name": cookie["name"],
                    "value": cookie["value"],
                    "path": cookie.get("path") or "/",
                }
                domain = str(cookie.get("domain") or "").strip()
                if domain:
                    kwargs["domain"] = domain
                session.cookies.set(**kwargs)
            response = session.get(
                self.site_home_url,
                headers=self._validation_headers(),
                timeout=10,
            )
            if response.status_code == 200 and not self._looks_like_block_page(response):
                self._validated_session_ready = True
                self._last_validation_error = "Validated by live request."
                return True
            self._validated_session_ready = False
            self._last_validation_error = f"Validation returned HTTP {response.status_code}."
            if self._looks_like_block_page(response):
                self._last_validation_error = "Validation still hit the Cloudflare block page."
            return False
        except Exception as exc:
            self._validated_session_ready = False
            self._last_validation_error = str(exc)
            logger.warning("Session validation failed for %s: %s", self.site_name, exc)
            return False
        finally:
            try:
                session.close()
            except Exception:
                pass

    def _check_session(self):
        self._refresh_cookie_snapshot()
        self._validate_session()
        self._update_status_labels()

    def _schedule_auto_accept(self):
        if self._auto_accept_pending or self._cleaned_up:
            return
        self._auto_accept_pending = True
        self.status_label.setText(f"{self.site_label} session detected. Saving and closing...")
        QTimer.singleShot(250, self._auto_accept_if_ready)

    def _auto_accept_if_ready(self):
        self._auto_accept_pending = False
        if self._cleaned_up or not self._has_reusable_session():
            return
        self._save_and_accept(auto=True)

    def _update_status_labels(self):
        current_url = self.view.url().toString() if self.view is not None else ""
        self.status_label.setText(self._status_text(current_url=current_url))
        self.token_label.setText(self._token_status_text())
        ready = self._has_reusable_session()
        self.save_btn.setEnabled(ready)
        if ready:
            self._schedule_auto_accept()

    def _token_status_text(self) -> str:
        found = matching_session_cookie_names(self.site_name, self._captured_cookies())
        if self._validated_session_ready:
            if found:
                return f"Validated session. Detected cookies: {', '.join(found)}"
            return "Validated session. Cookie names do not match the expected list, but the live request succeeded."
        required = sorted(site_required_cookie_names(self.site_name), key=str.casefold)
        if found:
            return f"Detected session cookies: {', '.join(found)}"
        if required:
            tail = f" Last check: {self._last_validation_error}" if self._last_validation_error else ""
            return f"Waiting for required session cookie(s): {', '.join(required)}.{tail}"
        return "Waiting for usable site cookies."

    def _status_text(self, prefix: str | None = None, current_url: str | None = None) -> str:
        count = self._saved_cookie_count()
        base = prefix or f"Captured {count} cookies for {self.site_label}."
        current = str(current_url or self.view.url().toString() or "")
        if self._has_reusable_session():
            return base + f" Session is ready. You can save {count} cookie(s)."

        host_hint = ""
        if self.site_host and self.site_host not in current.casefold():
            host_hint = f" You may be on an interstitial page; the dialog will try to return to {self.site_label} automatically."
        return base + " Waiting for a reusable site session before saving." + host_hint

    def _maybe_auto_return_home(self, current_url: str):
        if self._has_reusable_session() or self._auto_return_pending:
            return
        host = urlparse(str(current_url or "")).netloc.casefold()
        if not host or (self.site_host and self.site_host in host):
            return
        self._auto_return_pending = True
        self.status_label.setText(self._status_text(current_url=current_url))
        QTimer.singleShot(1400, self._auto_return_home)

    def _auto_return_home(self):
        self._auto_return_pending = False
        if self._has_reusable_session():
            return
        self._open_home()

    def _save_and_accept(self, auto: bool = False):
        self._refresh_cookie_snapshot()
        if not self._has_reusable_session():
            self._validate_session()
        if not self._has_reusable_session():
            if auto:
                return
            required = sorted(site_required_cookie_names(self.site_name), key=str.casefold)
            token_hint = f"Expected cookie(s): {', '.join(required)}.\n\n" if required else ""
            detail = f"Last validation result: {self._last_validation_error}\n\n" if self._last_validation_error else ""
            QMessageBox.information(
                self,
                "Session Not Ready",
                token_hint + detail + f"The browser has not captured a reusable {self.site_label} session yet.\n\nIf {self.site_label} is already visible in the browser, press Check Session once and then try saving again.",
            )
            self._update_status_labels()
            return
        count = save_site_cookies(self.site_name, self._captured_cookies())
        user_agent = self._browser_user_agent()
        save_site_user_agent(self.site_name, user_agent)
        logger.info("Saved %d cookies for %s", count, self.site_name)
        self.status_label.setText(f"Saved {count} cookies for {self.site_label}.")
        self.accept()

    def done(self, result: int):
        self._cleanup_webengine()
        super().done(result)

    def _cleanup_webengine(self):
        if self._cleaned_up:
            return
        self._cleaned_up = True

        poll_timer = getattr(self, "_poll_timer", None)
        if poll_timer is not None:
            try:
                poll_timer.stop()
            except Exception:
                pass

        cookie_store = self._cookie_store
        self._cookie_store = None
        if cookie_store is not None:
            try:
                cookie_store.cookieAdded.disconnect(self._on_cookie_added)
            except Exception:
                pass
            try:
                cookie_store.cookieRemoved.disconnect(self._on_cookie_removed)
            except Exception:
                pass

        page = getattr(self, "page", None)
        view = getattr(self, "view", None)
        profile = getattr(self, "profile", None)

        if page is not None:
            try:
                page.loadStarted.disconnect(self._on_load_started)
            except Exception:
                pass
            try:
                page.loadFinished.disconnect(self._on_load_finished)
            except Exception:
                pass
            try:
                page.urlChanged.disconnect(self._on_url_changed)
            except Exception:
                pass
            try:
                page.titleChanged.disconnect(self._on_title_changed)
            except Exception:
                pass
            try:
                page.setParent(None)
            except Exception:
                pass

        if view is not None:
            try:
                view.setPage(None)
            except Exception:
                pass

        if page is not None:
            try:
                page.deleteLater()
            except Exception:
                pass
            self.page = None

        if view is not None:
            try:
                view.deleteLater()
            except Exception:
                pass
            self.view = None

        if profile is not None:
            try:
                profile.deleteLater()
            except Exception:
                pass
            self.profile = None
