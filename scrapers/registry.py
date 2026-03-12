import importlib
import inspect
import pkgutil

from app_logging import get_logger
from .base import BaseScraper

logger = get_logger(__name__)


def _iter_scraper_classes():
    package_name = "scrapers.sites"
    package = importlib.import_module(package_name)

    for module_info in pkgutil.iter_modules(package.__path__):
        if module_info.name.startswith("_"):
            continue

        module = importlib.import_module(f"{package_name}.{module_info.name}")

        for _, obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(obj, BaseScraper):
                continue

            if obj is BaseScraper:
                continue

            yield obj


def get_scraper(url: str):
    for scraper_cls in _iter_scraper_classes():
        if scraper_cls.can_handle(url):
            logger.info("Matched scraper %s for %s", scraper_cls.__name__, url)
            return scraper_cls()

    logger.warning("No scraper available for %s", url)
    raise ValueError(f"No scraper available for URL: {url}")


def get_all_scrapers():
    return [scraper_cls() for scraper_cls in _iter_scraper_classes()]
