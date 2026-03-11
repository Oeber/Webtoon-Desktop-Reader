from abc import ABC, abstractmethod
from .models import SeriesInfo, PageInfo


class ScraperError(Exception):
    pass


class BaseScraper(ABC):

    site_name: str = "unknown"

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