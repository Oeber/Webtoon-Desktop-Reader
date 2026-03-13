import importlib
import inspect
import pkgutil

from core.app_logging import get_logger

from .discovery_base import BaseDiscoveryProvider

logger = get_logger(__name__)


def _iter_provider_module_names(package):
    configured = [name for name in getattr(package, "__all__", []) if not name.startswith("_")]
    discovered = [m.name for m in pkgutil.iter_modules(package.__path__) if not m.name.startswith("_")]
    ordered = []
    for name in configured + discovered:
        if name not in ordered:
            ordered.append(name)
    return ordered


def _iter_provider_classes():
    package_name = "scrapers.discovery_sites"
    package = importlib.import_module(package_name)

    for module_name in _iter_provider_module_names(package):
        module = importlib.import_module(f"{package_name}.{module_name}")

        for _, obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(obj, BaseDiscoveryProvider):
                continue
            if obj is BaseDiscoveryProvider:
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
