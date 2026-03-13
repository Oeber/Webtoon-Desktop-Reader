import importlib
import inspect
import pkgutil

from core.app_logging import get_logger

from .discovery_base import BaseDiscoveryProvider
from .site_availability import is_site_enabled

logger = get_logger(__name__)


def _iter_provider_module_names(package):
    configured = [name for name in getattr(package, "__all__", []) if not name.startswith("_")]
    discovered = [m.name for m in pkgutil.iter_modules(package.__path__) if not m.name.startswith("_")]
    ordered = []
    for name in configured + discovered:
        if name not in ordered:
            ordered.append(name)
    return ordered


def _iter_provider_classes(include_disabled: bool = False):
    package_name = "scrapers.discovery_sites"
    package = importlib.import_module(package_name)

    for module_name in _iter_provider_module_names(package):
        module = importlib.import_module(f"{package_name}.{module_name}")

        for _, obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(obj, BaseDiscoveryProvider):
                continue
            if obj is BaseDiscoveryProvider:
                continue
            if not include_disabled and not is_site_enabled(getattr(obj, "site_name", "")):
                continue
            yield obj


def get_all_discovery_providers():
    providers = []
    seen = set()
    for provider_cls in _iter_provider_classes():
        key = f"{provider_cls.__module__}.{provider_cls.__name__}"
        if key in seen:
            continue
        seen.add(key)
        try:
            providers.append(provider_cls())
        except Exception as e:
            logger.warning("Failed to initialize discovery provider %s", provider_cls.__name__, exc_info=e)
    return providers


def get_all_discovery_providers_including_disabled():
    providers = []
    seen = set()
    for provider_cls in _iter_provider_classes(include_disabled=True):
        key = f"{provider_cls.__module__}.{provider_cls.__name__}"
        if key in seen:
            continue
        seen.add(key)
        try:
            providers.append(provider_cls())
        except Exception as e:
            logger.warning("Failed to initialize discovery provider %s", provider_cls.__name__, exc_info=e)
    return providers
