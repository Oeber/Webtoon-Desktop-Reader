import json

from stores.app_settings_store import get_instance as get_app_settings_store


CUSTOM_CATEGORIES_KEY = "library_custom_categories"
SECTION_ORDER_KEY = "library_section_order"
_app_settings = get_app_settings_store()


def load_custom_categories() -> list[str]:
    raw = _app_settings.get(CUSTOM_CATEGORIES_KEY, "[]")
    try:
        values = json.loads(raw) if isinstance(raw, str) else list(raw)
    except Exception:
        return []
    seen = set()
    ordered = []
    for value in values:
        normalized = str(value).strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(normalized)
    return ordered


def save_custom_categories(categories: list[str]):
    seen = set()
    normalized = []
    for value in categories:
        name = str(value).strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(name)
    _app_settings.set(CUSTOM_CATEGORIES_KEY, json.dumps(normalized))


def load_section_order() -> list[str]:
    raw = _app_settings.get(SECTION_ORDER_KEY, "[]")
    try:
        values = json.loads(raw) if isinstance(raw, str) else list(raw)
    except Exception:
        return []

    seen = set()
    ordered = []
    for value in values:
        name = str(value).strip()
        if not name:
            continue
        if name in seen:
            continue
        seen.add(name)
        ordered.append(name)
    return ordered


def save_section_order(section_keys: list[str]):
    seen = set()
    normalized = []
    for value in section_keys:
        name = str(value).strip()
        if not name:
            continue
        if name in seen:
            continue
        seen.add(name)
        normalized.append(name)
    _app_settings.set(SECTION_ORDER_KEY, json.dumps(normalized))
