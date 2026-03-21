import json
import time

from scrapers.site_availability import (
    MODE_ALL_DISABLED,
    MODE_DISCOVERY_DISABLED,
    get_site_availability_mode,
)
from stores.app_settings_store import get_instance as get_app_settings_store


SITE_RELIABILITY_KEY = "scraper_site_reliability"
RELIABILITY_STALE_SECONDS = 24 * 60 * 60
FAILING_FRESH_SECONDS = 2 * 60 * 60
SLOW_THRESHOLD_MS = 3500

_BADGE_STYLES = {
    "healthy": {
        "label": "Healthy",
        "color": "#7bd67b",
        "background": "rgba(49, 87, 49, 0.45)",
        "border": "#4f8e4f",
    },
    "slow": {
        "label": "Slow",
        "color": "#ffd27d",
        "background": "rgba(100, 73, 20, 0.45)",
        "border": "#b7892f",
    },
    "failing": {
        "label": "Failing",
        "color": "#ff9b9b",
        "background": "rgba(104, 34, 34, 0.45)",
        "border": "#c95f5f",
    },
    "unknown": {
        "label": "Unknown",
        "color": "#d7b1aa",
        "background": "rgba(49, 31, 29, 0.45)",
        "border": "#6f5450",
    },
    "disabled": {
        "label": "Off",
        "color": "#b18b84",
        "background": "rgba(35, 24, 23, 0.45)",
        "border": "#5a423e",
    },
    "discovery_disabled": {
        "label": "Discover Off",
        "color": "#d7b1aa",
        "background": "rgba(49, 31, 29, 0.45)",
        "border": "#6f5450",
    },
}

_app_settings = get_app_settings_store()


def site_display_name(site_name: str) -> str:
    return str(site_name or "").replace("_", " ").title() or "Unknown"


def load_site_reliability() -> dict[str, dict]:
    raw = _app_settings.get(SITE_RELIABILITY_KEY, "{}")
    try:
        payload = json.loads(raw)
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        return {}

    normalized = {}
    for site_name, snapshot in payload.items():
        key = str(site_name or "").strip()
        if not key or not isinstance(snapshot, dict):
            continue
        normalized[key] = {
            "last_status": str(snapshot.get("last_status") or "").strip().lower(),
            "last_checked_at": int(snapshot.get("last_checked_at", 0) or 0),
            "last_success_at": int(snapshot.get("last_success_at", 0) or 0),
            "last_failure_at": int(snapshot.get("last_failure_at", 0) or 0),
            "last_duration_ms": int(snapshot.get("last_duration_ms", 0) or 0),
            "last_source": str(snapshot.get("last_source") or "").strip(),
            "last_error": str(snapshot.get("last_error") or "").strip(),
            "last_successes": int(snapshot.get("last_successes", 0) or 0),
            "last_failures": int(snapshot.get("last_failures", 0) or 0),
        }
    return normalized


def save_site_reliability(snapshots: dict[str, dict]) -> None:
    normalized = {}
    for site_name, snapshot in (snapshots or {}).items():
        key = str(site_name or "").strip()
        if not key or not isinstance(snapshot, dict):
            continue
        normalized[key] = {
            "last_status": str(snapshot.get("last_status") or "").strip().lower(),
            "last_checked_at": int(snapshot.get("last_checked_at", 0) or 0),
            "last_success_at": int(snapshot.get("last_success_at", 0) or 0),
            "last_failure_at": int(snapshot.get("last_failure_at", 0) or 0),
            "last_duration_ms": int(snapshot.get("last_duration_ms", 0) or 0),
            "last_source": str(snapshot.get("last_source") or "").strip(),
            "last_error": str(snapshot.get("last_error") or "").strip(),
            "last_successes": int(snapshot.get("last_successes", 0) or 0),
            "last_failures": int(snapshot.get("last_failures", 0) or 0),
        }
    _app_settings.set(SITE_RELIABILITY_KEY, json.dumps(normalized, separators=(",", ":"), sort_keys=True))


def record_site_check(site_name: str, *, source: str, succeeded: bool, duration_ms: int = 0, error: str = "") -> None:
    successes = 1 if succeeded else 0
    failures = 0 if succeeded else 1
    record_site_batch(
        site_name,
        source=source,
        successes=successes,
        failures=failures,
        duration_ms=duration_ms,
        error=error,
    )


def record_site_batch(
    site_name: str,
    *,
    source: str,
    successes: int = 0,
    failures: int = 0,
    duration_ms: int = 0,
    error: str = "",
) -> None:
    key = str(site_name or "").strip()
    if not key:
        return

    snapshots = load_site_reliability()
    snapshot = dict(snapshots.get(key, {}))
    checked_at = int(time.time())
    successes = max(0, int(successes or 0))
    failures = max(0, int(failures or 0))
    duration_ms = max(0, int(duration_ms or 0))

    if failures > 0 and successes <= 0:
        status = "failing"
        snapshot["last_failure_at"] = checked_at
    elif successes > 0:
        status = "slow" if duration_ms >= SLOW_THRESHOLD_MS else "healthy"
        snapshot["last_success_at"] = checked_at
    else:
        status = snapshot.get("last_status") or "unknown"

    snapshot.update({
        "last_status": status,
        "last_checked_at": checked_at,
        "last_duration_ms": duration_ms,
        "last_source": str(source or "").strip(),
        "last_error": str(error or "").strip() if failures > 0 else "",
        "last_successes": successes,
        "last_failures": failures,
    })
    snapshots[key] = snapshot
    save_site_reliability(snapshots)


def badge_for_site(site_name: str) -> dict[str, str]:
    key = str(site_name or "").strip()
    base = {
        "status": "unknown",
        "label": _BADGE_STYLES["unknown"]["label"],
        "color": _BADGE_STYLES["unknown"]["color"],
        "background": _BADGE_STYLES["unknown"]["background"],
        "border": _BADGE_STYLES["unknown"]["border"],
        "tooltip": f"{site_display_name(key)} has no recent reliability data.",
    }
    if not key:
        return base
    availability_mode = get_site_availability_mode(key)
    if availability_mode == MODE_ALL_DISABLED:
        return {
            "status": "disabled",
            "label": _BADGE_STYLES["disabled"]["label"],
            "color": _BADGE_STYLES["disabled"]["color"],
            "background": _BADGE_STYLES["disabled"]["background"],
            "border": _BADGE_STYLES["disabled"]["border"],
            "tooltip": f"{site_display_name(key)} is disabled for discovery and downloads.",
        }
    if availability_mode == MODE_DISCOVERY_DISABLED:
        return {
            "status": "discovery_disabled",
            "label": _BADGE_STYLES["discovery_disabled"]["label"],
            "color": _BADGE_STYLES["discovery_disabled"]["color"],
            "background": _BADGE_STYLES["discovery_disabled"]["background"],
            "border": _BADGE_STYLES["discovery_disabled"]["border"],
            "tooltip": f"{site_display_name(key)} is hidden from Discover but still available for downloads.",
        }

    snapshot = load_site_reliability().get(key)
    if not snapshot:
        return base

    checked_at = int(snapshot.get("last_checked_at", 0) or 0)
    age = max(0, int(time.time()) - checked_at) if checked_at else RELIABILITY_STALE_SECONDS + 1
    status = str(snapshot.get("last_status") or "unknown").strip().lower()
    if age > RELIABILITY_STALE_SECONDS:
        status = "unknown"
    elif status == "failing" and age > FAILING_FRESH_SECONDS:
        status = "unknown"
    if status not in _BADGE_STYLES:
        status = "unknown"

    style = _BADGE_STYLES[status]
    duration_ms = int(snapshot.get("last_duration_ms", 0) or 0)
    source = str(snapshot.get("last_source") or "").strip()
    error = str(snapshot.get("last_error") or "").strip()
    tooltip_parts = [f"{site_display_name(key)} status: {style['label']}"]
    if checked_at:
        tooltip_parts.append(f"Last checked: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(checked_at))}")
    if duration_ms > 0:
        tooltip_parts.append(f"Last response: {duration_ms} ms")
    if source:
        tooltip_parts.append(f"Source: {source}")
    if error and status == "failing":
        tooltip_parts.append(error)

    return {
        "status": status,
        "label": style["label"],
        "color": style["color"],
        "background": style["background"],
        "border": style["border"],
        "tooltip": "\n".join(tooltip_parts),
    }
