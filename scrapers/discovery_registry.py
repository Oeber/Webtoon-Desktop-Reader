import importlib
import importlib.util
import inspect
import pkgutil
import sys
from pathlib import Path

from core.app_logging import get_logger
from core.app_paths import app_root

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


def _iter_builtin_provider_classes():
    package_name = "scrapers.discovery_sites"
    try:
        package = importlib.import_module(package_name)
    except ModuleNotFoundError:
        return

    for module_name in _iter_provider_module_names(package):
        module = importlib.import_module(f"{package_name}.{module_name}")

        for _, obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(obj, BaseDiscoveryProvider):
                continue
            if obj is BaseDiscoveryProvider:
                continue
            yield obj


def _external_discovery_scrapers_path() -> Path:
    return app_root().joinpath("scrapers", "discovery_sites")


def _iter_external_provider_classes():
    external_dir = _external_discovery_scrapers_path()
    if not external_dir.is_dir():
        return

    for path in sorted(external_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue

        module_name = f"scrapers.discovery_sites.{path.stem}"
        try:
            module = _load_external_module(module_name, path)
        except Exception as e:
            logger.warning("Failed to load external discovery module %s", path, exc_info=e)
            continue

        for _, obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(obj, BaseDiscoveryProvider):
                continue
            if obj is BaseDiscoveryProvider:
                continue
            yield obj


def _load_external_module(module_name: str, path: Path):
    package_name = module_name.rpartition(".")[0]
    package = sys.modules.get(package_name)
    if package is not None:
        package_paths = getattr(package, "__path__", None)
        if package_paths is not None:
            parent_str = str(path.parent)
            if parent_str not in package_paths:
                package_paths.append(parent_str)

    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not create import spec for {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _iter_provider_classes(include_disabled: bool = False):
    seen = set()
    for provider_cls in _iter_external_provider_classes():
        key = f"{provider_cls.__module__}.{provider_cls.__name__}"
        if key in seen:
            continue
        seen.add(key)
        if not include_disabled and not is_site_enabled(getattr(provider_cls, "site_name", "")):
            continue
        yield provider_cls

    for provider_cls in _iter_builtin_provider_classes():
        key = f"{provider_cls.__module__}.{provider_cls.__name__}"
        if key in seen:
            continue
        seen.add(key)
        if not include_disabled and not is_site_enabled(getattr(provider_cls, "site_name", "")):
            continue
        yield provider_cls


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
