from abc import ABC, abstractmethod
from typing import Any
import re
from urllib.parse import parse_qs, urlparse

from .models import ChapterContent, PageInfo, ScraperConfigField, SeriesInfo


class ScraperError(Exception):
    pass


class ScraperDisabledError(ScraperError):
    pass


class BaseScraper(ABC):

    site_name: str = "unknown"
    site_display_name: str = ""
    content_type: str = "webtoon"
    site_hosts: tuple[str, ...] = ()
    site_base_url: str = ""
    site_required_cookie_names: tuple[str, ...] = ()
    site_session_cookie_names: tuple[str, ...] = ()
    source_config_fields: tuple[ScraperConfigField, ...] = ()

    def __init__(self):
        self.source_config: dict[str, Any] = self.default_source_config()

    @classmethod
    @abstractmethod
    def can_handle(cls, url: str) -> bool:
        """
        Return True if this scraper can handle the provided URL.
        """
        pass

    @abstractmethod
    def get_series_info(self, url: str) -> SeriesInfo:
        """
        Extract metadata and chapter list from a series page.
        """
        pass

    @abstractmethod
    def get_chapter_pages(self, chapter_url: str) -> list[PageInfo]:
        """
        Extract image URLs for a chapter.
        """
        pass

    @abstractmethod
    def get_request_headers(self, url):
        """
        Gets headers for scraping
        """
        pass

    def get_chapter_content(self, chapter_url: str) -> ChapterContent:
        """
        Optional text/html chapter extraction for sources such as webnovels.
        """
        raise NotImplementedError

    def parse_chapter_content_html(self, chapter_url: str, html: str) -> ChapterContent:
        """
        Optional parser for browser-fetched chapter HTML.
        """
        raise NotImplementedError

    def validate_session(self, cookies: list[dict], user_agent: str, url: str | None = None) -> tuple[bool, str]:
        """
        Optional site-specific session validation hook for protected sites.
        Return (ok, detail).
        """
        return False, "No custom validator available."

    def is_chapter_url(self, url: str) -> bool:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if "episode_no" in qs:
            return True
        return re.search(r"(chapter|episode)[-/ ]?\d+", parsed.path, re.IGNORECASE) is not None

    def series_url_from_chapter_url(self, url: str) -> str:
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        match = re.search(r"(.+)/(?:chapter|episode)[^/]*$", path, re.IGNORECASE)
        if match:
            path = match.group(1)
        return f"{parsed.scheme}://{parsed.netloc}{path}"

    def extract_chapter_number(self, url: str) -> int | None:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if "episode_no" in qs:
            try:
                return int(qs["episode_no"][0])
            except Exception:
                return None

        match = re.search(r"(?:chapter|episode)[-/ ]?(\d+)", url, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None

    def get_site_session_config(self) -> dict:
        display_name = str(
            getattr(self, "site_display_name", "") or self.site_name.replace("_", " ").title()
        ).strip()
        hosts = tuple(
            str(host).strip().casefold()
            for host in getattr(self, "site_hosts", ()) or ()
            if str(host).strip()
        )
        base_url = str(getattr(self, "site_base_url", "") or "").strip()
        required_cookie_names = tuple(
            str(name).strip()
            for name in getattr(self, "site_required_cookie_names", ()) or ()
            if str(name).strip()
        )
        session_cookie_names = tuple(
            str(name).strip()
            for name in getattr(self, "site_session_cookie_names", ()) or ()
            if str(name).strip()
        )
        return {
            "display_name": display_name,
            "hosts": hosts,
            "base_url": base_url,
            "required_cookie_names": required_cookie_names,
            "session_cookie_names": session_cookie_names,
        }
    
    def download_asset(self, url: str, dest_path: str) -> bool:
        return False

    @classmethod
    def get_source_config_fields(cls) -> tuple[ScraperConfigField, ...]:
        return tuple(getattr(cls, "source_config_fields", ()) or ())

    @classmethod
    def default_source_config(cls) -> dict[str, Any]:
        defaults: dict[str, Any] = {}
        for field in cls.get_source_config_fields():
            if field.control == "multi_select":
                values = field.default if isinstance(field.default, list) else []
                defaults[field.key] = [str(value) for value in values if str(value).strip()]
                continue
            defaults[field.key] = field.default
        return defaults

    @classmethod
    def normalize_source_config(cls, config: dict | None) -> dict[str, Any]:
        incoming = config if isinstance(config, dict) else {}
        normalized = cls.default_source_config()
        for field in cls.get_source_config_fields():
            value = incoming.get(field.key, normalized.get(field.key))
            allowed = {
                str(option.value): str(option.value)
                for option in field.options
            }
            if field.control == "boolean":
                normalized[field.key] = bool(value)
                continue
            if field.control == "integer":
                try:
                    coerced = int(value)
                except (TypeError, ValueError):
                    coerced = field.default
                if coerced is None:
                    normalized[field.key] = None
                    continue
                if field.min_value is not None:
                    coerced = max(int(field.min_value), coerced)
                if field.max_value is not None:
                    coerced = min(int(field.max_value), coerced)
                normalized[field.key] = coerced
                continue
            if field.control == "multi_select":
                values = value if isinstance(value, list) else field.default if isinstance(field.default, list) else []
                seen = set()
                chosen = []
                for item in values:
                    text = str(item or "").strip()
                    if not text or text in seen:
                        continue
                    if allowed and text not in allowed:
                        continue
                    seen.add(text)
                    chosen.append(text)
                normalized[field.key] = chosen
                continue
            if field.control == "select":
                text = str(value or "").strip()
                if allowed and text not in allowed:
                    text = str(field.default or "").strip()
                normalized[field.key] = text
                continue
            text = str(value or "").strip()
            normalized[field.key] = text
        return normalized

    def apply_source_config(self, config: dict | None) -> dict[str, Any]:
        self.source_config = self.normalize_source_config(config)
        return dict(self.source_config)

    def get_source_config_value(self, key: str, default: Any = None) -> Any:
        return self.source_config.get(key, default)
