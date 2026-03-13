import json

import requests

from stores.app_settings_store import get_instance as get_app_settings


SITE_SESSION_KEY_PREFIX = "site_session_cookies:"
SITE_SESSION_UA_KEY_PREFIX = "site_session_user_agent:"
SITE_HOSTS = {
    "hiper_cool": "hiper.cool",
}
SITE_BASE_URLS = {
    "hiper_cool": "https://hiper.cool/",
}


def site_session_key(site_name: str) -> str:
    return f"{SITE_SESSION_KEY_PREFIX}{str(site_name or '').strip()}"


def site_user_agent_key(site_name: str) -> str:
    return f"{SITE_SESSION_UA_KEY_PREFIX}{str(site_name or '').strip()}"


def site_host(site_name: str) -> str:
    return str(SITE_HOSTS.get(str(site_name or "").strip(), "")).strip()


def site_base_url(site_name: str) -> str:
    return str(SITE_BASE_URLS.get(str(site_name or "").strip(), "")).strip()


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

    cookies = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "")
        if not name:
            continue
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": str(item.get("domain") or "").strip(),
                "path": str(item.get("path") or "/").strip() or "/",
                "secure": bool(item.get("secure", False)),
                "expires": item.get("expires"),
            }
        )
    return cookies


def save_site_cookies(site_name: str, cookies: list[dict]) -> int:
    normalized = []
    seen = set()
    expected_host = site_host(site_name)
    for item in cookies or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "")
        domain = str(item.get("domain") or "").strip()
        path = str(item.get("path") or "/").strip() or "/"
        if not name:
            continue
        if expected_host and domain and expected_host not in domain.lstrip(".").casefold():
            continue
        key = (name, domain, path)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path,
                "secure": bool(item.get("secure", False)),
                "expires": item.get("expires"),
            }
        )

    get_app_settings().set(site_session_key(site_name), json.dumps(normalized))
    return len(normalized)


def clear_site_cookies(site_name: str):
    get_app_settings().set(site_session_key(site_name), "[]")


def has_site_cookies(site_name: str) -> bool:
    return bool(load_site_cookies(site_name))


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
