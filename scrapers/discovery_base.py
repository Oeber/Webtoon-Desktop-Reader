from abc import ABC, abstractmethod

from .base import ScraperError
from .models import CatalogPage


class BaseDiscoveryProvider(ABC):

    site_name: str = "unknown"

    @abstractmethod
    def get_catalog_page(self, page: int = 1) -> CatalogPage:
        """
        Return one page of discoverable series for this site.
        """
        raise ScraperError(f"{self.site_name} does not implement catalog browsing")
