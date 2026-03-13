import json

import requests

from stores.app_settings_store import get_instance as get_app_settings


SITE_SESSION_KEY_PREFIX = "site_session_cookies:"
SITE_SESSION_UA_KEY_PREFIX = "site_session_user_agent:"
SITE_DISPLAY_NAMES = {
    "hiper_cool": "HiperCool",
}
SITE_HOSTS = {
    "hiper_cool": "hiper.cool",
}
SITE_BASE_URLS = {
    "hiper_cool": "https://hiper.cool/",
}
SITE_REQUIRED_COOKIE_NAMES = {
    "hiper_cool": {"cf_clearance"},
}
SITE_SESSION_COOKIE_NAMES = {
    "hiper_cool": {
        "cf_clearance",
        "PHPSESSID",
        "wordpress_logged_in",
        "wordpress_sec",
        "wp-settings-1",
        "wp-settings-time-1",
    },
}


def site_session_key(site_name: str) -> str:
    return f"{SITE_SESSION_KEY_PREFIX}{str(site_name or '').strip()}"


def site_user_agent_key(site_name: str) -> str:
    return f"{SITE_SESSION_UA_KEY_PREFIX}{str(site_name or '').strip()}"


def site_host(site_name: str) -> str:
    return str(SITE_HOSTS.get(str(site_name or "").strip(), "")).strip()


def site_base_url(site_name: str) -> str:
    return str(SITE_BASE_URLS.get(str(site_name or "").strip(), "")).strip()


def site_display_name(site_name: str) -> str:
    key = str(site_name or "").strip()
    if not key:
        return "Site"
    return str(SITE_DISPLAY_NAMES.get(key, key.replace("_", " ").title())).strip() or "Site"


def site_required_cookie_names(site_name: str) -> set[str]:
    return set(SITE_REQUIRED_COOKIE_NAMES.get(str(site_name or "").strip(), set()))


def site_session_cookie_names(site_name: str) -> set[str]:
    required = site_required_cookie_names(site_name)
    session_names = set(SITE_SESSION_COOKIE_NAMES.get(str(site_name or "").strip(), set()))
    return required | session_names


def _filter_site_cookies(site_name: str, cookies: list[dict]) -> list[dict]:
    normalized = []
    expected_host = site_host(site_name)
    for item in cookies or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "")
        domain = str(item.get("domain") or "").strip()
        if not name or value == "":
            continue
        if expected_host and domain and expected_host not in domain.lstrip(".").casefold():
            continue
        normalized.append(
            {
                "name": name,
                "value": value,
                "domain": domain,
                "path": str(item.get("path") or "/").strip() or "/",
                "secure": bool(item.get("secure", False)),
                "expires": item.get("expires"),
            }
        )
    return normalized


def load_site_cookies(site_name: str) -> list[dict]:
    raw = get_app_settings().get(site_session_key(site_name), "")
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    return _filter_site_cookies(site_name, payload)


def save_site_cookies(site_name: str, cookies: list[dict]) -> int:
    normalized = []
    seen = set()
    for item in _filter_site_cookies(site_name, cookies):
        key = (item["name"], item["domain"], item["path"])
        if key in seen:
            continue
        seen.add(key)
        normalized.append(item)

    get_app_settings().set(site_session_key(site_name), json.dumps(normalized))
    return len(normalized)


def clear_site_cookies(site_name: str):
    get_app_settings().set(site_session_key(site_name), "[]")


def has_site_cookies(site_name: str) -> bool:
    return bool(load_site_cookies(site_name))


def has_required_site_cookies(site_name: str, cookies: list[dict] | None = None) -> bool:
    required = {name.casefold() for name in site_required_cookie_names(site_name)}
    current = cookies if cookies is not None else load_site_cookies(site_name)
    if not required:
        return bool(current)
    names = {
        str(cookie.get("name") or "").strip().casefold()
        for cookie in current
        if isinstance(cookie, dict)
    }
    return required.issubset(names)


def matching_session_cookie_names(site_name: str, cookies: list[dict] | None = None) -> list[str]:
    interesting = {name.casefold() for name in site_session_cookie_names(site_name)}
    current = cookies if cookies is not None else load_site_cookies(site_name)
    matches = []
    for cookie in current:
        if not isinstance(cookie, dict):
            continue
        name = str(cookie.get("name") or "").strip()
        if not name:
            continue
        if interesting and name.casefold() not in interesting:
            continue
        matches.append(name)
    return sorted(set(matches), key=str.casefold)


def save_site_user_agent(site_name: str, user_agent: str):
    get_app_settings().set(site_user_agent_key(site_name), str(user_agent or "").strip())


def load_site_user_agent(site_name: str, default: str = "") -> str:
    return str(get_app_settings().get(site_user_agent_key(site_name), default) or default).strip()


def site_cookie_header(site_name: str) -> str:
    parts = []
    for cookie in load_site_cookies(site_name):
        name = str(cookie.get("name") or "").strip()
        value = str(cookie.get("value") or "")
        if not name:
            continue
        parts.append(f"{name}={value}")
    return "; ".join(parts)


def apply_site_cookies(session: requests.Session, site_name: str) -> requests.Session:
    for cookie in load_site_cookies(site_name):
        kwargs = {
            "name": cookie["name"],
            "value": cookie["value"],
            "path": cookie.get("path") or "/",
        }
        domain = str(cookie.get("domain") or "").strip()
        if domain:
            kwargs["domain"] = domain
        expires = cookie.get("expires")
        if expires not in (None, ""):
            try:
                kwargs["expires"] = int(expires)
            except (TypeError, ValueError):
                pass
        session.cookies.set(**kwargs)
    return session
