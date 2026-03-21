import json

from stores.app_settings_store import get_instance as get_app_settings_store


SITE_AVAILABILITY_KEY = "scraper_site_availability"
DISABLED_SITES_KEY = "disabled_scraper_sites"
MODE_ENABLED = "enabled"
MODE_DISCOVERY_DISABLED = "discovery_disabled"
MODE_ALL_DISABLED = "all_disabled"
_VALID_MODES = {
    MODE_ENABLED,
    MODE_DISCOVERY_DISABLED,
    MODE_ALL_DISABLED,
}

_app_settings = get_app_settings_store()


def _normalize_site_name(site_name) -> str:
    return str(site_name or "").strip()


def _normalize_mode(mode: str) -> str:
    normalized = str(mode or "").strip().lower()
    if normalized not in _VALID_MODES:
        return MODE_ENABLED
    return normalized


def _load_legacy_disabled_sites() -> set[str]:
    raw = _app_settings.get(DISABLED_SITES_KEY, "[]")
    try:
        loaded = json.loads(raw)
    except Exception:
        loaded = []

    normalized = set()
    for site_name in loaded or []:
        text = _normalize_site_name(site_name)
        if text:
            normalized.add(text)
    return normalized


def load_site_availability() -> dict[str, str]:
    raw = _app_settings.get(SITE_AVAILABILITY_KEY, "{}")
    try:
        loaded = json.loads(raw)
    except Exception:
        loaded = {}

    normalized: dict[str, str] = {}
    if isinstance(loaded, dict):
        for site_name, mode in loaded.items():
            key = _normalize_site_name(site_name)
            if not key:
                continue
            normalized_mode = _normalize_mode(mode)
            if normalized_mode != MODE_ENABLED:
                normalized[key] = normalized_mode

    for site_name in _load_legacy_disabled_sites():
        normalized.setdefault(site_name, MODE_ALL_DISABLED)
    return normalized


def save_site_availability(site_modes: dict[str, str] | None) -> None:
    normalized: dict[str, str] = {}
    for site_name, mode in (site_modes or {}).items():
        key = _normalize_site_name(site_name)
        if not key:
            continue
        normalized_mode = _normalize_mode(mode)
        if normalized_mode != MODE_ENABLED:
            normalized[key] = normalized_mode
    _app_settings.set(
        SITE_AVAILABILITY_KEY,
        json.dumps(normalized, separators=(",", ":"), sort_keys=True),
    )
    legacy_disabled = sorted(
        site_name
        for site_name, mode in normalized.items()
        if mode == MODE_ALL_DISABLED
    )
    _app_settings.set(DISABLED_SITES_KEY, json.dumps(legacy_disabled))


def get_site_availability_mode(site_name: str) -> str:
    normalized = _normalize_site_name(site_name)
    if not normalized:
        return MODE_ENABLED
    return load_site_availability().get(normalized, MODE_ENABLED)


def set_site_availability_mode(site_name: str, mode: str) -> None:
    normalized = _normalize_site_name(site_name)
    if not normalized:
        return
    availability = load_site_availability()
    normalized_mode = _normalize_mode(mode)
    if normalized_mode == MODE_ENABLED:
        availability.pop(normalized, None)
    else:
        availability[normalized] = normalized_mode
    save_site_availability(availability)


def is_discovery_enabled(site_name: str) -> bool:
    return get_site_availability_mode(site_name) == MODE_ENABLED


def is_download_enabled(site_name: str) -> bool:
    return get_site_availability_mode(site_name) != MODE_ALL_DISABLED


def is_site_enabled(site_name: str) -> bool:
    return is_download_enabled(site_name)


def load_disabled_sites() -> set[str]:
    return {
        site_name
        for site_name, mode in load_site_availability().items()
        if mode == MODE_ALL_DISABLED
    }


def save_disabled_sites(site_names) -> None:
    save_site_availability(
        {
            _normalize_site_name(site_name): MODE_ALL_DISABLED
            for site_name in (site_names or [])
            if _normalize_site_name(site_name)
        }
    )


def set_site_enabled(site_name: str, enabled: bool) -> None:
    set_site_availability_mode(site_name, MODE_ENABLED if enabled else MODE_ALL_DISABLED)
