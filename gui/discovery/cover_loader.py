import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

import requests
from PySide6.QtCore import QObject, Signal


class DiscoveryCoverLoader(QObject):
    loaded = Signal(object, object, str)
    _MAX_WORKERS = 8
    _MAX_CACHE_ENTRIES = 256

    def __init__(self, parent=None):
        super().__init__(parent)
        self._executor = ThreadPoolExecutor(
            max_workers=self._MAX_WORKERS,
            thread_name_prefix="discovery-cover",
        )
        self._lock = threading.Lock()
        self._session_local = threading.local()
        self._inflight: dict[tuple[str, tuple[tuple[str, str], ...]], list[object]] = {}
        self._cache: OrderedDict[tuple[str, tuple[tuple[str, str], ...]], tuple[bytes | None, str]] = OrderedDict()

    def load(self, widget, url: str, headers: dict[str, str] | None):
        request_headers = dict(headers or {})
        key = (url, tuple(sorted(request_headers.items())))

        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                data, error = cached
                self.loaded.emit(widget, data, error)
                return

            waiting_widgets = self._inflight.get(key)
            if waiting_widgets is not None:
                waiting_widgets.append(widget)
                return

            self._inflight[key] = [widget]

        self._executor.submit(self._fetch_cover, key, request_headers)

    def _get_session(self) -> requests.Session:
        session = getattr(self._session_local, "session", None)
        if session is not None:
            return session

        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=self._MAX_WORKERS,
            pool_maxsize=self._MAX_WORKERS,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        self._session_local.session = session
        return session

    def _fetch_cover(self, key: tuple[str, tuple[tuple[str, str], ...]], headers: dict[str, str]) -> None:
        url = key[0]
        data = None
        error = ""
        try:
            session = self._get_session()
            response = session.get(url, headers=headers, timeout=20)
            response.raise_for_status()
            data = response.content
        except Exception as e:
            error = str(e)

        with self._lock:
            waiting_widgets = self._inflight.pop(key, [])
            self._cache[key] = (data, error)
            self._cache.move_to_end(key)
            while len(self._cache) > self._MAX_CACHE_ENTRIES:
                self._cache.popitem(last=False)

        for widget in waiting_widgets:
            self.loaded.emit(widget, data, error)
