from abc import ABC, abstractmethod

from .base import ScraperError
from .discovery_support import match_catalog_series_to_library
from .models import CatalogPage, CatalogSeries, normalize_catalog_text


class BaseDiscoveryProvider(ABC):

    site_name: str = "unknown"
    site_display_name: str = ""
    site_hosts: tuple[str, ...] = ()
    site_base_url: str = ""
    site_required_cookie_names: tuple[str, ...] = ()
    site_session_cookie_names: tuple[str, ...] = ()

    def get_display_name(self) -> str:
        return self.site_name.replace("_", " ").title()

    def get_site_session_config(self) -> dict:
        display_name = str(getattr(self, "site_display_name", "") or self.get_display_name()).strip()
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
