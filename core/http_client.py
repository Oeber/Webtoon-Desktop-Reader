from __future__ import annotations

from typing import Iterable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from core.app_logging import get_logger
from core.site_session import apply_site_cookies


logger = get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_RETRY_COUNT = 2
DEFAULT_STATUS_FORCELIST = (429, 500, 502, 503, 504)


def build_retry(
    *,
    retries: int = DEFAULT_RETRY_COUNT,
    backoff_factor: float = 0.6,
    allowed_methods: Iterable[str] = ("GET", "HEAD", "OPTIONS"),
) -> Retry:
    retry_count = max(0, int(retries))
    normalized_methods = frozenset(str(method or "").upper() for method in allowed_methods if str(method or "").strip())
    return Retry(
        total=retry_count,
        connect=retry_count,
        read=retry_count,
        status=retry_count,
        backoff_factor=max(0.0, float(backoff_factor)),
        status_forcelist=DEFAULT_STATUS_FORCELIST,
        allowed_methods=normalized_methods or None,
        raise_on_status=False,
    )


def create_session(
    *,
    pool_connections: int = 8,
    pool_maxsize: int = 8,
    retries: int = DEFAULT_RETRY_COUNT,
    backoff_factor: float = 0.6,
    allowed_methods: Iterable[str] = ("GET", "HEAD", "OPTIONS"),
    headers: dict[str, str] | None = None,
    site_name: str = "",
) -> requests.Session:
    session = requests.Session()
    adapter = HTTPAdapter(
        pool_connections=max(1, int(pool_connections)),
        pool_maxsize=max(1, int(pool_maxsize)),
        max_retries=build_retry(
            retries=retries,
            backoff_factor=backoff_factor,
            allowed_methods=allowed_methods,
        ),
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    if headers:
        session.headers.update({str(key): str(value) for key, value in headers.items()})
    if site_name:
        apply_site_cookies(session, site_name)
    return session


def request(
    method: str,
    url: str,
    *,
    session: requests.Session | None = None,
    headers: dict[str, str] | None = None,
    timeout: int | float = DEFAULT_TIMEOUT_SECONDS,
    stream: bool = False,
    log_label: str = "",
    **kwargs,
) -> requests.Response:
    owns_session = session is None
    active_session = session or create_session()
    method_name = str(method or "GET").upper()
    label = f"{log_label} " if log_label else ""
    logger.info("HTTP %s%s timeout=%s stream=%s", label, url, timeout, stream)
    try:
        response = active_session.request(
            method_name,
            url,
            headers=headers,
            timeout=timeout,
            stream=stream,
            **kwargs,
        )
        if owns_session and not stream:
            _ = response.content
            active_session.close()
        return response
    except requests.RequestException as exc:
        if owns_session:
            active_session.close()
        logger.warning("HTTP %sfailed url=%s error=%s", label, url, exc)
        raise


def get(
    url: str,
    *,
    session: requests.Session | None = None,
    headers: dict[str, str] | None = None,
    timeout: int | float = DEFAULT_TIMEOUT_SECONDS,
    stream: bool = False,
    log_label: str = "",
    **kwargs,
) -> requests.Response:
    return request(
        "GET",
        url,
        session=session,
        headers=headers,
        timeout=timeout,
        stream=stream,
        log_label=log_label,
        **kwargs,
    )
