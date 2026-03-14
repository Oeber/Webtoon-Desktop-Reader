from abc import ABC, abstractmethod
import re
from urllib.parse import parse_qs, urlparse

from .models import PageInfo, SeriesInfo


class ScraperError(Exception):
    pass


class ScraperDisabledError(ScraperError):
    pass


class BaseScraper(ABC):

    site_name: str = "unknown"
    site_display_name: str = ""
    site_hosts: tuple[str, ...] = ()
    site_base_url: str = ""
    site_required_cookie_names: tuple[str, ...] = ()
    site_session_cookie_names: tuple[str, ...] = ()

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
