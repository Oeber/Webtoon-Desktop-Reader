from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.app_paths import resource_path


DEFAULT_LOCALE = "en"
_current_locale = DEFAULT_LOCALE
_cache: dict[str, dict[str, str]] = {}
_i18n_dir = resource_path("gui", "i18n")


def _locale_path(locale: str) -> Path:
    return _i18n_dir / f"{locale}.json"


def _load_locale(locale: str) -> dict[str, str]:
    normalized = str(locale or "").strip() or DEFAULT_LOCALE
    cached = _cache.get(normalized)
    if cached is not None:
        return cached

    path = _locale_path(normalized)
    if not path.exists() and normalized != DEFAULT_LOCALE:
        path = _locale_path(DEFAULT_LOCALE)
        normalized = DEFAULT_LOCALE

    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        data = {}

    if not isinstance(data, dict):
        data = {}

    cleaned = {str(key): str(value) for key, value in data.items()}
    _cache[normalized] = cleaned
    return cleaned


def set_locale(locale: str) -> None:
    global _current_locale
    normalized = str(locale or "").strip() or DEFAULT_LOCALE
    if not _locale_path(normalized).exists():
        normalized = DEFAULT_LOCALE
    _current_locale = normalized


def get_locale() -> str:
    return _current_locale


def available_locales() -> tuple[str, ...]:
    if not _i18n_dir.exists():
        return (DEFAULT_LOCALE,)
    locales = sorted(path.stem for path in _i18n_dir.glob("*.json") if path.is_file())
    return tuple(locales) or (DEFAULT_LOCALE,)


def t(key: str, /, **kwargs: Any) -> str:
    current = _load_locale(_current_locale)
    default = _load_locale(DEFAULT_LOCALE)
    template = current.get(key, default.get(key, key))
    return template.format(**kwargs) if kwargs else template
