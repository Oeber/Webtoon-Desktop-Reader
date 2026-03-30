from pathlib import Path

from core.app_paths import default_library_path
from stores.app_settings_store import get_instance as get_app_settings_store


DEFAULT_LIBRARY_PATH = str(default_library_path())
DEFAULT_LIBRARY_FOLDER_NAMES = {
    "webtoon": "webtoon",
    "manga": "manga",
    "webnovel": "webnovel",
}
DEFAULT_LIBRARY_CONTENT_PATHS = {
    content_type: str(Path(DEFAULT_LIBRARY_PATH) / folder_name)
    for content_type, folder_name in DEFAULT_LIBRARY_FOLDER_NAMES.items()
}

LIBRARY_USE_CATEGORIES_KEY = "library_use_categories"
LIBRARY_SHOW_NEW_SECTION_KEY = "library_show_new_section"
LIBRARY_SHOW_DOWNLOADS_SECTION_KEY = "library_show_downloads_section"
LIBRARY_SHOW_BOOKMARKED_SECTION_KEY = "library_show_bookmarked_section"
LIBRARY_SHOW_CONTINUE_SECTION_KEY = "library_show_continue_section"
LIBRARY_SHOW_UPDATES_SECTION_KEY = "library_show_updates_section"
LIBRARY_SHOW_COMPLETED_SECTION_KEY = "library_show_completed_section"
LIBRARY_WEBTOON_PATH_KEY = "library_webtoon_path"
LIBRARY_MANGA_PATH_KEY = "library_manga_path"
LIBRARY_WEBNOVEL_PATH_KEY = "library_webnovel_path"
LIBRARY_RESERVED_FOLDER_NAMES_KEY = "library_reserved_folder_names"

APP_UPDATE_CHECK_ON_STARTUP_KEY = "app_update_check_on_startup"
APP_UPDATE_LAST_CHECK_AT_KEY = "app_update_last_check_at"
APP_UPDATE_LAST_VERSION_KEY = "app_update_last_version"
APP_UPDATE_LAST_URL_KEY = "app_update_last_url"
APP_UPDATE_LAST_ASSET_URL_KEY = "app_update_last_asset_url"
APP_UPDATE_LAST_STATUS_KEY = "app_update_last_status"
APP_UPDATE_LAST_ERROR_KEY = "app_update_last_error"
APP_UPDATE_LAST_NOTIFIED_VERSION_KEY = "app_update_last_notified_version"

LIBRARY_UPDATE_CHECK_ON_STARTUP_KEY = "library_update_check_on_startup"
LIBRARY_UPDATE_INTERVAL_MINUTES_KEY = "library_update_interval_minutes"
LIBRARY_UPDATE_LAST_CHECK_AT_KEY = "library_update_last_check_at"
LIBRARY_UPDATE_LAST_RESULT_KEY = "library_update_last_result"
LIBRARY_UPDATE_LAST_NOTIFIED_SIGNATURE_KEY = "library_update_last_notified_signature"
LIBRARY_UPDATE_INTERVAL_OPTIONS = [
    (0, "Off"),
    (15, "Every 15 minutes"),
    (30, "Every 30 minutes"),
    (60, "Every hour"),
    (120, "Every 2 hours"),
    (240, "Every 4 hours"),
]

VIEWER_AUTO_SKIP_KEY = "viewer_auto_skip"
VIEWER_ZOOM_KEY = "viewer_zoom"
VIEWER_FOCUS_MODE_KEY = "viewer_focus_mode"
VIEWER_CHROME_VISIBLE_KEY = "viewer_chrome_visible"
VIEWER_MINIMAP_VISIBLE_KEY = "viewer_minimap_visible"
VIEWER_SCENE_ANCHORS_VISIBLE_KEY = "viewer_scene_anchors_visible"
VIEWER_TEXT_PROGRESS_VISIBLE_KEY = "viewer_text_progress_visible"
VIEWER_TEXT_SIZE_KEY = "viewer_text_size"
VIEWER_TEXT_PAGE_COLOR_KEY = "viewer_text_page_color"
VIEWER_TEXT_COLOR_KEY = "viewer_text_color"
VIEWER_MANGA_LAYOUT_KEY = "viewer_manga_layout"
VIEWER_MANGA_SPREAD_PARITY_KEY = "viewer_manga_spread_parity"
VIEWER_MANGA_FIT_MODE_KEY = "viewer_manga_fit_mode"
VIEWER_NAV_DIRECTION_KEY = "viewer_nav_direction"
DISCOVERY_DEFAULT_PROVIDER_KEY = "discovery_default_provider"
LIBRARY_PATH_KEY = "library_path"

_app_settings = get_app_settings_store()

_LIBRARY_PATH_KEYS = {
    "webtoon": LIBRARY_WEBTOON_PATH_KEY,
    "manga": LIBRARY_MANGA_PATH_KEY,
    "webnovel": LIBRARY_WEBNOVEL_PATH_KEY,
}


def load_library_path() -> str:
    return str(_app_settings.get(LIBRARY_PATH_KEY, DEFAULT_LIBRARY_PATH))


def save_library_path(path: str):
    _app_settings.set(LIBRARY_PATH_KEY, path)


def default_library_content_path(content_type: str, library_path: str | None = None) -> str:
    normalized = str(content_type or "").strip().casefold()
    folder_name = DEFAULT_LIBRARY_FOLDER_NAMES.get(normalized, DEFAULT_LIBRARY_FOLDER_NAMES["webtoon"])
    base_path = str(library_path or load_library_path() or DEFAULT_LIBRARY_PATH).strip() or DEFAULT_LIBRARY_PATH
    return str(Path(base_path) / folder_name)


def load_library_content_path(content_type: str, library_path: str | None = None) -> str:
    normalized = str(content_type or "").strip().casefold()
    key = _LIBRARY_PATH_KEYS.get(normalized)
    default_path = default_library_content_path(normalized, library_path)
    if not key:
        return default_path

    raw = str(_app_settings.get(key, "") or "").strip()
    if not raw:
        return default_path
    if Path(raw).is_absolute():
        return raw
    return str(Path(str(library_path or load_library_path() or DEFAULT_LIBRARY_PATH)) / raw)


def load_library_content_paths(library_path: str | None = None) -> dict[str, str]:
    return {
        content_type: load_library_content_path(content_type, library_path)
        for content_type in DEFAULT_LIBRARY_FOLDER_NAMES
    }


def save_library_content_paths(content_paths: dict[str, str]):
    values = {}
    for content_type in DEFAULT_LIBRARY_FOLDER_NAMES:
        key = _LIBRARY_PATH_KEYS[content_type]
        default_path = DEFAULT_LIBRARY_CONTENT_PATHS[content_type]
        values[key] = str((content_paths or {}).get(content_type) or default_path).strip() or default_path
    _app_settings.set_many(values)


def load_library_reserved_folder_names() -> list[str]:
    value = _app_settings.get(LIBRARY_RESERVED_FOLDER_NAMES_KEY, [])
    if not isinstance(value, list):
        return []
    seen = set()
    names = []
    for entry in value:
        normalized = str(entry or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        names.append(normalized)
    return names


def save_library_reserved_folder_names(folder_names: list[str]):
    seen = set()
    names = []
    for entry in folder_names or []:
        normalized = str(entry or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        names.append(normalized)
    _app_settings.set(LIBRARY_RESERVED_FOLDER_NAMES_KEY, names)


def load_default_discovery_provider() -> str:
    return str(_app_settings.get(DISCOVERY_DEFAULT_PROVIDER_KEY, "") or "").strip()


def save_default_discovery_provider(site_name: str):
    _app_settings.set(DISCOVERY_DEFAULT_PROVIDER_KEY, str(site_name or "").strip())


def load_setting(key: str, default):
    return _app_settings.get(key, default)


def save_setting(key: str, value):
    _app_settings.set(key, value)


def save_settings(values: dict):
    _app_settings.set_many(values)

