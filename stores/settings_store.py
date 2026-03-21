from core.app_paths import default_library_path
from stores.app_settings_store import get_instance as get_app_settings_store


DEFAULT_LIBRARY_PATH = str(default_library_path())

LIBRARY_USE_CATEGORIES_KEY = "library_use_categories"
LIBRARY_SHOW_NEW_SECTION_KEY = "library_show_new_section"
LIBRARY_SHOW_DOWNLOADS_SECTION_KEY = "library_show_downloads_section"

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
LIBRARY_PATH_KEY = "library_path"

_app_settings = get_app_settings_store()


def load_library_path() -> str:
    return str(_app_settings.get(LIBRARY_PATH_KEY, DEFAULT_LIBRARY_PATH))


def save_library_path(path: str):
    _app_settings.set(LIBRARY_PATH_KEY, path)


def load_setting(key: str, default):
    return _app_settings.get(key, default)


def save_setting(key: str, value):
    _app_settings.set(key, value)


def save_settings(values: dict):
    _app_settings.set_many(values)
