import json

from stores.app_settings_store import get_instance as get_app_settings_store


DISABLED_SITES_KEY = "disabled_scraper_sites"

_app_settings = get_app_settings_store()


def load_disabled_sites() -> set[str]:
    raw = _app_settings.get(DISABLED_SITES_KEY, "[]")
    try:
        loaded = json.loads(raw)
    except Exception:
        loaded = []

    normalized = set()
    for site_name in loaded or []:
        text = str(site_name or "").strip()
        if text:
            normalized.add(text)
    return normalized


def save_disabled_sites(site_names) -> None:
    normalized = sorted(
        {
            str(site_name or "").strip()
            for site_name in (site_names or [])
            if str(site_name or "").strip()
        }
    )
    _app_settings.set(DISABLED_SITES_KEY, json.dumps(normalized))


def is_site_enabled(site_name: str) -> bool:
    normalized = str(site_name or "").strip()
    if not normalized:
        return True
    return normalized not in load_disabled_sites()


def set_site_enabled(site_name: str, enabled: bool) -> None:
    normalized = str(site_name or "").strip()
    if not normalized:
        return
    disabled = load_disabled_sites()
    if enabled:
        disabled.discard(normalized)
    else:
        disabled.add(normalized)
    save_disabled_sites(disabled)
