import json

from app_settings_store import get_instance as get_app_settings_store


CUSTOM_CATEGORIES_KEY = "library_custom_categories"
_app_settings = get_app_settings_store()


def load_custom_categories() -> list[str]:
    raw = _app_settings.get(CUSTOM_CATEGORIES_KEY, "[]")
    try:
        values = json.loads(raw) if isinstance(raw, str) else list(raw)
    except Exception:
        return []
    return sorted({str(value).strip() for value in values if str(value).strip()}, key=str.lower)


def save_custom_categories(categories: list[str]):
    normalized = sorted({str(value).strip() for value in categories if str(value).strip()}, key=str.lower)
    _app_settings.set(CUSTOM_CATEGORIES_KEY, json.dumps(normalized))
