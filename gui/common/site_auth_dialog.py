from PySide6.QtCore import QUrl, Qt
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout

from core.app_logging import get_logger
from core.app_paths import data_path
from core.site_session import save_site_cookies, save_site_user_agent, site_base_url, site_host
from gui.common.styles import BUTTON_STYLE, PAGE_BG_STYLE, STATUS_LABEL_STYLE

logger = get_logger(__name__)


class SiteAuthDialog(QDialog):

    def __init__(self, site_name: str, url: str = "", parent=None):
        super().__init__(parent)
        self.site_name = str(site_name or "").strip()
        self.site_host = site_host(self.site_name)
        self._cookie_map: dict[tuple[str, str, str], dict] = {}
        self._cookie_store = None
        self._cleaned_up = False

        self.setWindowTitle(f"Authorize {self._display_name()}")
        self.resize(1180, 820)
        self.setStyleSheet(PAGE_BG_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        self.info_label = QLabel(
            f"Open {self._display_name()} below, complete any challenge or login, then click Save Session."
        )
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet("color: #fff0ec; font-size: 13px; background: transparent;")
        layout.addWidget(self.info_label)

        self.status_label = QLabel("Waiting for page load...")
        self.status_label.setStyleSheet(STATUS_LABEL_STYLE)
        layout.addWidget(self.status_label)

        self.profile = QWebEngineProfile(f"auth-{self.site_name}")
        storage_root = data_path("webengine", self.site_name)
        cache_root = data_path("webengine", f"{self.site_name}-cache")
        storage_root.mkdir(parents=True, exist_ok=True)
        cache_root.mkdir(parents=True, exist_ok=True)
        self.profile.setPersistentStoragePath(str(storage_root))
        self.profile.setCachePath(str(cache_root))
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)

        self.page = QWebEnginePage(self.profile, self)
        self.page.loadStarted.connect(self._on_load_started)
        self.page.loadFinished.connect(self._on_load_finished)
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
        self._cookie_store.loadAllCookies()

        start_url = str(url or "").strip() or site_base_url(self.site_name)
        if not start_url:
            start_url = "https://hiper.cool/"
        self.view.load(QUrl(start_url))

    def _display_name(self) -> str:
        return self.site_name.replace("_", " ").title() or "Site"

    def _open_home(self):
        self.view.load(QUrl(site_base_url(self.site_name) or "https://hiper.cool/"))

    def _on_load_started(self):
        self.status_label.setText("Loading page...")

    def _on_load_finished(self, ok: bool):
        current_url = self.view.url().toString()
        if ok:
            self.status_label.setText(self._status_text(f"Loaded {current_url}", current_url=current_url))
        else:
            self.status_label.setText(f"Failed to load {current_url}")

    def _on_cookie_added(self, cookie: QNetworkCookie):
        data = self._serialize_cookie(cookie)
        if data is None:
            return
        key = (data["name"], data["domain"], data["path"])
        self._cookie_map[key] = data
        self.status_label.setText(self._status_text())
        self.save_btn.setEnabled(self._saved_cookie_count() > 0)

    def _on_cookie_removed(self, cookie: QNetworkCookie):
        data = self._serialize_cookie(cookie)
        if data is None:
            return
        key = (data["name"], data["domain"], data["path"])
        self._cookie_map.pop(key, None)
        self.status_label.setText(self._status_text())
        self.save_btn.setEnabled(self._saved_cookie_count() > 0)

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

    def _status_text(self, prefix: str | None = None, current_url: str | None = None) -> str:
        count = self._saved_cookie_count()
        base = prefix or f"Captured {count} cookies for {self._display_name()}."
        if count <= 0:
            host_hint = ""
            current = str(current_url or self.view.url().toString() or "")
            if self.site_host and self.site_host not in current.casefold():
                host_hint = " You may be on an interstitial page; click Open Home or open a title on HiperCool, then wait for cookies."
            return base + " No usable cookies yet; complete the site challenge before saving." + host_hint
        return base + f" Ready to save {count} cookie(s)."

    def _save_and_accept(self):
        count = self._saved_cookie_count()
        if count <= 0:
            QMessageBox.information(
                self,
                "No Session Captured",
                "No usable HiperCool cookies were captured yet.\n\nWait until the challenge or login fully completes, then save again.",
            )
            return
        count = save_site_cookies(self.site_name, list(self._cookie_map.values()))
        user_agent = self.profile.httpUserAgent() if self.profile is not None else ""
        save_site_user_agent(self.site_name, user_agent)
        logger.info("Saved %d cookies for %s", count, self.site_name)
        self.status_label.setText(f"Saved {count} cookies for {self._display_name()}.")
        self.accept()

    def done(self, result: int):
        self._cleanup_webengine()
        super().done(result)

    def _cleanup_webengine(self):
        if self._cleaned_up:
            return
        self._cleaned_up = True

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
