from core.app_paths import default_library_path
from stores.app_settings_store import get_instance as get_app_settings_store


DEFAULT_LIBRARY_PATH = str(default_library_path())

LIBRARY_USE_CATEGORIES_KEY = "library_use_categories"
LIBRARY_SHOW_NEW_SECTION_KEY = "library_show_new_section"
LIBRARY_SHOW_DOWNLOADS_SECTION_KEY = "library_show_downloads_section"
LIBRARY_SHOW_BOOKMARKED_SECTION_KEY = "library_show_bookmarked_section"
LIBRARY_SHOW_CONTINUE_SECTION_KEY = "library_show_continue_section"
LIBRARY_SHOW_UPDATES_SECTION_KEY = "library_show_updates_section"
LIBRARY_SHOW_COMPLETED_SECTION_KEY = "library_show_completed_section"

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
DISCOVERY_DEFAULT_PROVIDER_KEY = "discovery_default_provider"
LIBRARY_PATH_KEY = "library_path"

_app_settings = get_app_settings_store()


def load_library_path() -> str:
    return str(_app_settings.get(LIBRARY_PATH_KEY, DEFAULT_LIBRARY_PATH))


def save_library_path(path: str):
    _app_settings.set(LIBRARY_PATH_KEY, path)


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
