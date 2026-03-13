from urllib.parse import urlparse

from core.app_logging import get_logger
from scrapers.demo_metadata import DEMO_BASE, rewrite_series_info

from .omega_scans import OmegaScansScraper

logger = get_logger(__name__)


class CoolWebtoonsScraper(OmegaScansScraper):
    site_name = "cool_webtoons"
    DEMO_NETLOC = urlparse(DEMO_BASE).netloc

    @classmethod
    def can_handle(cls, url: str) -> bool:
        return cls.DEMO_NETLOC in str(url or "")

    def get_series_info(self, url: str, session=None):
        real_url = self._to_real_url(url)
        logger.info("Cool Webtoons: fetching OmegaScans series info via %s", real_url)
        series = super().get_series_info(real_url, session=session)
        return rewrite_series_info(series, site_name=self.site_name)

    def get_chapter_pages(self, chapter_url: str, session=None):
        real_url = self._to_real_url(chapter_url)
        logger.info("Cool Webtoons: fetching OmegaScans chapter pages via %s", real_url)
        return super().get_chapter_pages(real_url, session=session)

    def get_request_headers(self, url: str) -> dict:
        return super().get_request_headers(self._to_real_url(url))

    def series_url_from_chapter_url(self, url: str) -> str:
        return self._to_demo_url(super().series_url_from_chapter_url(self._to_real_url(url)))

    def _to_real_url(self, url: str) -> str:
        text = str(url or "").strip()
        if self.DEMO_NETLOC not in text:
            return text
        parsed = urlparse(text)
        return f"{self.BASE}{parsed.path}".rstrip("/")

    def _to_demo_url(self, url: str) -> str:
        text = str(url or "").strip()
        if "omegascans.org" not in text:
            return text
        parsed = urlparse(text)
        return f"{DEMO_BASE}{parsed.path}".rstrip("/")
