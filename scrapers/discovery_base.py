from abc import ABC, abstractmethod

from .base import ScraperError
from .discovery_support import match_catalog_series_to_library
from .models import CatalogPage, CatalogSeries, normalize_catalog_text


class BaseDiscoveryProvider(ABC):

    site_name: str = "unknown"

    def get_display_name(self) -> str:
        return self.site_name.replace("_", " ").title()

    def entry_key(self, entry: CatalogSeries) -> str:
        return entry.identity_key()

    def matches_search(self, entry: CatalogSeries, query: str) -> bool:
        return entry.matches_query(query)

    def match_entry_to_library(
        self,
        entry: CatalogSeries,
        source_matches: dict[tuple[str, str], dict],
        title_matches: dict[str, dict],
    ) -> dict | None:
        return match_catalog_series_to_library(entry, source_matches, title_matches)

    def downloaded_entries(self, entries_by_site: dict[str, list[CatalogSeries]]) -> list[CatalogSeries]:
        entries = list(entries_by_site.get(self.site_name, []))
        entries.sort(key=lambda entry: normalize_catalog_text(entry.title))
        return entries

    @abstractmethod
    def get_catalog_page(self, page: int = 1, search_query: str = "") -> CatalogPage:
        """
        Return one page of discoverable series for this site.
        """
        raise ScraperError(f"{self.site_name} does not implement catalog browsing")
