from core.app_logging import get_logger
from scrapers.demo_metadata import rewrite_catalog_entry

from .omega_scans import OmegaScansDiscoveryProvider

logger = get_logger(__name__)


class CoolWebtoonsDiscoveryProvider(OmegaScansDiscoveryProvider):
    site_name = "cool_webtoons"

    def get_display_name(self) -> str:
        return "Cool Webtoons"

    def get_catalog_page(self, page: int = 1, search_query: str = ""):
        result = super().get_catalog_page(page=page, search_query=search_query)
        result.site = self.site_name
        result.entries = [rewrite_catalog_entry(entry, site_name=self.site_name) for entry in result.entries]
        logger.info(
            "Cool Webtoons discovery: rewrote %d OmegaScans entries for demo output",
            len(result.entries),
        )
        return result
