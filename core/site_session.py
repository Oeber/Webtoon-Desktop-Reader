import json
from functools import lru_cache
from urllib.parse import urlparse

import requests

from stores.app_settings_store import get_instance as get_app_settings


SITE_SESSION_KEY_PREFIX = "site_session_cookies:"
SITE_SESSION_UA_KEY_PREFIX = "site_session_user_agent:"


def _normalize_site_name(site_name: str) -> str:
    return str(site_name or "").strip()


def _normalize_host_values(values) -> tuple[str, ...]:
    hosts = []
    seen = set()
    for value in values or ():
        text = str(value or "").strip().casefold().lstrip(".")
        if not text or text in seen:
            continue
        seen.add(text)
        hosts.append(text)
    return tuple(hosts)


def _normalize_name_values(values) -> tuple[str, ...]:
    names = []
    seen = set()
    for value in values or ():
        text = str(value or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(text)
    return tuple(names)


def _merge_site_session_config(base: dict, extra: dict | None) -> dict:
    if not extra:
        return base
    merged = dict(base)
    if extra.get("display_name") and not merged["display_name"]:
        merged["display_name"] = str(extra["display_name"]).strip()
    if extra.get("base_url") and not merged["base_url"]:
        merged["base_url"] = str(extra["base_url"]).strip()
    merged["hosts"] = _normalize_host_values((*merged["hosts"], *(extra.get("hosts") or ())))
    merged["required_cookie_names"] = _normalize_name_values(
        (*merged["required_cookie_names"], *(extra.get("required_cookie_names") or ()))
    )
    merged["session_cookie_names"] = _normalize_name_values(
        (*merged["session_cookie_names"], *(extra.get("session_cookie_names") or ()))
    )
    return merged


def _default_site_session_config(site_name: str) -> dict:
    normalized = _normalize_site_name(site_name)
    return {
        "site_name": normalized,
        "display_name": "",
        "hosts": (),
        "base_url": "",
        "required_cookie_names": (),
        "session_cookie_names": (),
    }


def _extract_site_session_config(source) -> dict | None:
    getter = getattr(source, "get_site_session_config", None)
    if not callable(getter):
        return None
    try:
        config = getter()
    except Exception:
        return None
    if not isinstance(config, dict):
        return None
    return {
        "display_name": str(config.get("display_name") or "").strip(),
        "hosts": _normalize_host_values(config.get("hosts") or ()),
        "base_url": str(config.get("base_url") or "").strip(),
        "required_cookie_names": _normalize_name_values(config.get("required_cookie_names") or ()),
        "session_cookie_names": _normalize_name_values(config.get("session_cookie_names") or ()),
    }


@lru_cache(maxsize=None)
def _site_session_config(site_name: str) -> dict:
    normalized = _normalize_site_name(site_name)
    config = _default_site_session_config(normalized)
    if not normalized:
        return config

    try:
        from scrapers.registry import get_all_scrapers_including_disabled

        for scraper in get_all_scrapers_including_disabled():
            if _normalize_site_name(getattr(scraper, "site_name", "")) != normalized:
                continue
            config = _merge_site_session_config(config, _extract_site_session_config(scraper))
    except Exception:
        pass

    try:
        from scrapers.discovery_registry import get_all_discovery_providers_including_disabled

        for provider in get_all_discovery_providers_including_disabled():
            if _normalize_site_name(getattr(provider, "site_name", "")) != normalized:
                continue
            config = _merge_site_session_config(config, _extract_site_session_config(provider))
    except Exception:
        pass

    if not config["hosts"] and config["base_url"]:
        host = urlparse(config["base_url"]).netloc.strip().casefold().lstrip(".")
        if host:
            config["hosts"] = (host,)

    if config["required_cookie_names"]:
        config["session_cookie_names"] = _normalize_name_values(
            (*config["required_cookie_names"], *config["session_cookie_names"])
        )

    return config


def site_session_key(site_name: str) -> str:
    return f"{SITE_SESSION_KEY_PREFIX}{_normalize_site_name(site_name)}"


def site_user_agent_key(site_name: str) -> str:
    return f"{SITE_SESSION_UA_KEY_PREFIX}{_normalize_site_name(site_name)}"


def site_host(site_name: str) -> str:
    hosts = _site_session_config(site_name)["hosts"]
    return hosts[0] if hosts else ""


def site_base_url(site_name: str) -> str:
    return _site_session_config(site_name)["base_url"]


def site_display_name(site_name: str) -> str:
    return _site_session_config(site_name)["display_name"] or "Site"


def site_required_cookie_names(site_name: str) -> set[str]:
    return set(_site_session_config(site_name)["required_cookie_names"])


def site_session_cookie_names(site_name: str) -> set[str]:
    required = site_required_cookie_names(site_name)
    session_names = set(_site_session_config(site_name)["session_cookie_names"])
    return required | session_names


def site_name_for_host(host: str) -> str:
    normalized_host = str(host or "").strip().casefold().lstrip(".")
    if not normalized_host:
        return ""

    try:
        from scrapers.registry import get_all_scrapers_including_disabled

        for scraper in get_all_scrapers_including_disabled():
            site_name = _normalize_site_name(getattr(scraper, "site_name", ""))
            if not site_name:
                continue
            hosts = _site_session_config(site_name)["hosts"]
            if any(normalized_host == candidate or normalized_host.endswith(f".{candidate}") for candidate in hosts):
                return site_name
    except Exception:
        pass

    try:
        from scrapers.discovery_registry import get_all_discovery_providers_including_disabled

        for provider in get_all_discovery_providers_including_disabled():
            site_name = _normalize_site_name(getattr(provider, "site_name", ""))
            if not site_name:
                continue
            hosts = _site_session_config(site_name)["hosts"]
            if any(normalized_host == candidate or normalized_host.endswith(f".{candidate}") for candidate in hosts):
                return site_name
    except Exception:
        pass

    return ""


def site_name_for_url(url: str) -> str:
    return site_name_for_host(urlparse(str(url or "")).netloc)


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
