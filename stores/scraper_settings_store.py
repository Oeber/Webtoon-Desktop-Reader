from stores.app_settings_store import get_instance as get_app_settings_store


_app_settings = get_app_settings_store()


def scraper_default_config_key(site_name: str) -> str:
    return f"scraper_default_config::{str(site_name or '').strip()}"


def load_scraper_default_config(site_name: str) -> dict:
    value = _app_settings.get(scraper_default_config_key(site_name), {})
    return value if isinstance(value, dict) else {}


def save_scraper_default_config(site_name: str, config: dict | None):
    _app_settings.set(
        scraper_default_config_key(site_name),
        config if isinstance(config, dict) else {},
    )


def reset_scraper_default_config(site_name: str):
    _app_settings.delete(scraper_default_config_key(site_name))
