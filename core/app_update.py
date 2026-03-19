from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from core.app_logging import get_logger
from core.app_paths import app_root, data_path, resource_path


APP_NAME = "Webtoon Desktop Reader"
DEFAULT_APP_VERSION = "0.9.5"
GITHUB_OWNER = "Oeber"
GITHUB_REPO = "Webtoon-Desktop-Reader"
GITHUB_REPO_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"
GITHUB_RELEASES_URL = f"{GITHUB_REPO_URL}/releases"
GITHUB_LATEST_RELEASE_API_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
REQUEST_TIMEOUT_SECONDS = 15
UPDATE_DOWNLOAD_CHUNK_SIZE = 1024 * 256

_VERSION_RE = re.compile(r"\d+")
logger = get_logger(__name__)


@dataclass(slots=True)
class ReleaseAsset:
    name: str
    download_url: str
    content_type: str
    size: int


@dataclass(slots=True)
class ReleaseInfo:
    version: str
    tag_name: str
    title: str
    html_url: str
    published_at: str
    body: str
    prerelease: bool
    draft: bool
    asset: ReleaseAsset | None = None


@dataclass(slots=True)
class UpdateCheckResult:
    current_version: str
    checked_at: int
    latest_release: ReleaseInfo | None = None
    error_message: str = ""

    @property
    def is_update_available(self) -> bool:
        if self.latest_release is None:
            return False
        return compare_versions(self.latest_release.version, self.current_version) > 0


def _extract_version(raw) -> str:
    text = str(raw or "")
    parts = _VERSION_RE.findall(text)
    if not parts:
        return ""
    return ".".join(parts)


def _load_app_version() -> str:
    disk_path = data_path("app_version.txt")
    try:
        raw = disk_path.read_text(encoding="utf-8").strip()
        cleaned = _extract_version(raw)
        if cleaned:
            return cleaned
    except OSError:
        pass

    bundle_path = resource_path("data", "app_version.txt")
    try:
        raw = bundle_path.read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_APP_VERSION

    cleaned = _extract_version(raw)
    version = cleaned or DEFAULT_APP_VERSION

    try:
        disk_path.parent.mkdir(parents=True, exist_ok=True)
        disk_path.write_text(version, encoding="utf-8")
    except OSError:
        pass

    return version


APP_VERSION = _load_app_version()


def _updater_binary_name() -> str:
    return "Webtoon Desktop Reader Updater.exe" if sys.platform == "win32" else "Webtoon Desktop Reader Updater"


def _updater_binary_path() -> Path:
    return app_root().joinpath(_updater_binary_name())


def _temp_updater_binary_path() -> Path:
    launch_dir = Path(tempfile.mkdtemp(prefix="webtoon-reader-updater-launch-"))
    return launch_dir.joinpath(_updater_binary_name())


def is_self_update_supported() -> bool:
    if not bool(getattr(sys, "frozen", False)):
        return False
    return _updater_binary_path().exists()


def can_self_update(release: ReleaseInfo | None) -> bool:
    if not is_self_update_supported() or release is None or release.asset is None:
        return False
    return release.asset.name.casefold().endswith(".zip")


def fetch_latest_release(timeout: int = REQUEST_TIMEOUT_SECONDS) -> UpdateCheckResult:
    checked_at = int(time.time())
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{APP_NAME}/{APP_VERSION}",
    }
    logger.info("Checking latest GitHub release current_version=%s url=%s", APP_VERSION, GITHUB_LATEST_RELEASE_API_URL)

    try:
        response = requests.get(
            GITHUB_LATEST_RELEASE_API_URL,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        logger.warning("GitHub release check failed: %s", exc)
        return UpdateCheckResult(
            current_version=APP_VERSION,
            checked_at=checked_at,
            error_message=str(exc),
        )
    except ValueError:
        logger.warning("GitHub release check returned invalid JSON")
        return UpdateCheckResult(
            current_version=APP_VERSION,
            checked_at=checked_at,
            error_message="GitHub returned an invalid release response.",
        )

    release = _parse_release(payload)
    if release is None:
        logger.warning("GitHub release payload could not be parsed")
        return UpdateCheckResult(
            current_version=APP_VERSION,
            checked_at=checked_at,
            error_message="Could not read the latest release details from GitHub.",
        )

    logger.info(
        "Latest GitHub release parsed version=%s tag=%s asset=%s update_available=%s",
        release.version,
        release.tag_name,
        release.asset.name if release.asset else "",
        compare_versions(release.version, APP_VERSION) > 0,
    )

    return UpdateCheckResult(
        current_version=APP_VERSION,
        checked_at=checked_at,
        latest_release=release,
    )


def compare_versions(left: str, right: str) -> int:
    left_parts = _version_parts(left)
    right_parts = _version_parts(right)
    width = max(len(left_parts), len(right_parts))
    left_parts += [0] * (width - len(left_parts))
    right_parts += [0] * (width - len(right_parts))
    if left_parts < right_parts:
        return -1
    if left_parts > right_parts:
        return 1
    return 0


def display_version(raw: str) -> str:
    cleaned = str(raw or "").strip()
    if not cleaned:
        return APP_VERSION
    return cleaned if cleaned.lower().startswith("v") else f"v{cleaned}"


def format_check_time(timestamp: int | None) -> str:
    if not timestamp:
        return "Never"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(timestamp)))


def last_update_error_path() -> Path:
    return app_root().joinpath("data", "last_update_error.txt")


def last_update_trace_path() -> Path:
    return app_root().joinpath("data", "last_update_trace.txt")


def last_update_launch_path() -> Path:
    return app_root().joinpath("data", "last_update_launch.txt")


def _write_update_launch_marker(launcher_path: Path, zip_path: Path, install_dir: Path, exe_path: Path, launcher_source: str) -> None:
    marker_path = last_update_launch_path()
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(
        "\n".join(
            [
                f"time={format_check_time(int(time.time()))}",
                f"pid={os.getpid()}",
                f"launcher_source={launcher_source}",
                f"launcher={launcher_path}",
                f"zip={zip_path}",
                f"install_dir={install_dir}",
                f"exe={exe_path}",
            ]
        ),
        encoding="utf-8",
    )


def load_last_update_error() -> str:
    path = last_update_error_path()
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def load_last_update_trace() -> str:
    path = last_update_trace_path()
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def download_release_asset(
    asset: ReleaseAsset,
    progress_callback=None,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
) -> Path:
    target_dir = Path(tempfile.mkdtemp(prefix="webtoon-reader-update-"))
    target_path = target_dir / asset.name
    headers = {
        "Accept": "application/octet-stream",
        "User-Agent": f"{APP_NAME}/{APP_VERSION}",
    }
    logger.info(
        "Starting release asset download asset=%s size=%s url=%s target=%s",
        asset.name,
        asset.size,
        asset.download_url,
        target_path,
    )

    written = 0
    total = int(asset.size or 0)

    try:
        local_source = Path(asset.download_url)
        if local_source.exists():
            total = int(local_source.stat().st_size)
            with local_source.open("rb") as source, target_path.open("wb") as handle:
                while True:
                    chunk = source.read(UPDATE_DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    handle.write(chunk)
                    written += len(chunk)
                    if callable(progress_callback):
                        progress_callback(written, total)
        else:
            with requests.get(asset.download_url, headers=headers, timeout=timeout, stream=True) as response:
                response.raise_for_status()
                total = int(response.headers.get("Content-Length") or asset.size or 0)
                with target_path.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=UPDATE_DOWNLOAD_CHUNK_SIZE):
                        if not chunk:
                            continue
                        handle.write(chunk)
                        written += len(chunk)
                        if callable(progress_callback):
                            progress_callback(written, total)
        logger.info(
            "Finished release asset download asset=%s written=%s total=%s target=%s",
            asset.name,
            written,
            total,
            target_path,
        )
    except Exception:
        logger.exception("Release asset download failed asset=%s target=%s", asset.name, target_path)
        try:
            target_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to remove partial update file %s", target_path, exc_info=True)
        try:
            target_dir.rmdir()
        except OSError:
            logger.warning("Failed to remove update temp directory %s", target_dir, exc_info=True)
        raise

    return target_path


def launch_windows_update_installer(zip_path: str | Path) -> tuple[bool, str]:
    if not is_self_update_supported():
        logger.warning("Self-update launch requested but packaged self-update helper is not available")
        return False, "Automatic app updates are only supported for packaged builds that include the updater helper."

    zip_path = Path(zip_path).resolve()
    if not zip_path.exists():
        logger.warning("Self-update launch requested but downloaded update package was missing: %s", zip_path)
        return False, "Downloaded update package was not found."

    install_dir = app_root().resolve()
    exe_path = Path(sys.executable).resolve()
    updater_source = _updater_binary_path().resolve()
    updater_copy = _temp_updater_binary_path().resolve()
    error_path = last_update_error_path()
    trace_path = last_update_trace_path()
    launch_marker_path = last_update_launch_path()
    logger.info(
        "Preparing packaged updater helper zip=%s install_dir=%s exe=%s helper=%s temp_helper=%s",
        zip_path,
        install_dir,
        exe_path,
        updater_source,
        updater_copy,
    )

    try:
        error_path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Failed to clear previous update error file %s", error_path, exc_info=True)

    try:
        trace_path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Failed to clear previous update trace file %s", trace_path, exc_info=True)

    try:
        launch_marker_path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Failed to clear previous update launch marker %s", launch_marker_path, exc_info=True)

    try:
        updater_copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(updater_source, updater_copy)
    except OSError as exc:
        logger.exception("Failed to stage updater helper %s -> %s", updater_source, updater_copy)
        return False, str(exc)

    _write_update_launch_marker(updater_copy, zip_path, install_dir, exe_path, str(updater_source))

    flags = 0
    flags |= getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    try:
        subprocess.Popen(
            [
                str(updater_copy),
                "--zip-path",
                str(zip_path),
                "--install-dir",
                str(install_dir),
                "--exe-path",
                str(exe_path),
                "--parent-pid",
                str(os.getpid()),
            ],
            creationflags=flags,
            close_fds=True,
        )
    except Exception as exc:
        logger.exception("Failed to launch updater helper staged at %s", updater_copy)
        return False, str(exc)

    logger.info("Launched updater helper source=%s staged=%s marker=%s", updater_source, updater_copy, launch_marker_path)
    return True, ""


def _parse_release(payload: dict) -> ReleaseInfo | None:
    if not isinstance(payload, dict):
        return None

    tag_name = str(payload.get("tag_name") or "").strip()
    version = _extract_version(tag_name) or _extract_version(payload.get("name")) or "0.0.0"
    assets = payload.get("assets")
    asset = _pick_asset(assets if isinstance(assets, list) else [])

    return ReleaseInfo(
        version=version,
        tag_name=tag_name or display_version(version),
        title=str(payload.get("name") or tag_name or display_version(version)),
        html_url=str(payload.get("html_url") or GITHUB_RELEASES_URL),
        published_at=str(payload.get("published_at") or ""),
        body=str(payload.get("body") or ""),
        prerelease=bool(payload.get("prerelease")),
        draft=bool(payload.get("draft")),
        asset=asset,
    )


def _pick_asset(assets: list[dict]) -> ReleaseAsset | None:
    parsed_assets = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        parsed_assets.append(
            ReleaseAsset(
                name=str(asset.get("name") or ""),
                download_url=str(asset.get("browser_download_url") or ""),
                content_type=str(asset.get("content_type") or ""),
                size=int(asset.get("size") or 0),
            )
        )

    if not parsed_assets:
        return None

    portable_assets = [
        item
        for item in parsed_assets
        if item.name.casefold().endswith("-portable.zip")
    ]
    if not portable_assets:
        return None

    portable_assets.sort(key=lambda item: len(item.name))
    return portable_assets[0]


def _version_parts(raw: str) -> list[int]:
    text = _extract_version(raw)
    if not text:
        return [0]
    return [int(part) for part in text.split(".")]
