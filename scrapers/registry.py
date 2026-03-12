import importlib
import importlib.util
import inspect
import pkgutil
import sys
from pathlib import Path

from app_logging import get_logger
from app_paths import external_scrapers_path
from .base import BaseScraper

logger = get_logger(__name__)


def _iter_scraper_module_names(package):
    configured = [name for name in getattr(package, "__all__", []) if not name.startswith("_")]
    discovered = [m.name for m in pkgutil.iter_modules(package.__path__) if not m.name.startswith("_")]
    ordered = []
    for name in configured + discovered:
        if name not in ordered:
            ordered.append(name)
    return ordered


def _iter_builtin_scraper_classes():
    package_name = "scrapers.sites"
    package = importlib.import_module(package_name)

    for module_name in _iter_scraper_module_names(package):
        module = importlib.import_module(f"{package_name}.{module_name}")

        for _, obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(obj, BaseScraper):
                continue
            if obj is BaseScraper:
                continue
            yield obj


def _iter_external_scraper_classes():
    external_dir = external_scrapers_path()
    if not external_dir.is_dir():
        return

    for path in sorted(external_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue

        module_name = f"external_scrapers.{path.stem}"
        try:
            module = _load_external_module(module_name, path)
        except Exception as e:
            logger.warning("Failed to load external scraper module %s", path, exc_info=e)
            continue

        for _, obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(obj, BaseScraper):
                continue
            if obj is BaseScraper:
                continue
            yield obj


def _load_external_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not create import spec for {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _iter_scraper_classes():
    seen = set()
    for scraper_cls in _iter_external_scraper_classes():
        key = f"{scraper_cls.__module__}.{scraper_cls.__name__}"
        if key in seen:
            continue
        seen.add(key)
        yield scraper_cls

    for scraper_cls in _iter_builtin_scraper_classes():
        key = f"{scraper_cls.__module__}.{scraper_cls.__name__}"
        if key in seen:
            continue
        seen.add(key)
        yield scraper_cls


def get_scraper(url: str):
    for scraper_cls in _iter_scraper_classes():
        if scraper_cls.can_handle(url):
            logger.info("Matched scraper %s for %s", scraper_cls.__name__, url)
            return scraper_cls()

    logger.warning("No scraper available for %s", url)
    raise ValueError(f"No scraper available for URL: {url}")


def get_all_scrapers():
    return [scraper_cls() for scraper_cls in _iter_scraper_classes()]
