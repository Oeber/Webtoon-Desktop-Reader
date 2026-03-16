from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from core.app_paths import app_root, resource_path


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


def _extract_version(raw) -> str:
    text = str(raw or "")
    parts = _VERSION_RE.findall(text)
    if not parts:
        return ""
    return ".".join(parts)


def _load_app_version() -> str:
    version_path = resource_path("data", "app_version.txt")
    try:
        raw = version_path.read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_APP_VERSION

    cleaned = _extract_version(raw)
    return cleaned or DEFAULT_APP_VERSION


APP_VERSION = _load_app_version()


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


def is_self_update_supported() -> bool:
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


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

    try:
        response = requests.get(
            GITHUB_LATEST_RELEASE_API_URL,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return UpdateCheckResult(
            current_version=APP_VERSION,
            checked_at=checked_at,
            error_message=str(exc),
        )
    except ValueError:
        return UpdateCheckResult(
            current_version=APP_VERSION,
            checked_at=checked_at,
            error_message="GitHub returned an invalid release response.",
        )

    release = _parse_release(payload)
    if release is None:
        return UpdateCheckResult(
            current_version=APP_VERSION,
            checked_at=checked_at,
            error_message="Could not read the latest release details from GitHub.",
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

    try:
        with requests.get(asset.download_url, headers=headers, timeout=timeout, stream=True) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length") or asset.size or 0)
            written = 0
            with target_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=UPDATE_DOWNLOAD_CHUNK_SIZE):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    written += len(chunk)
                    if callable(progress_callback):
                        progress_callback(written, total)
    except Exception:
        try:
            target_path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            target_dir.rmdir()
        except OSError:
            pass
        raise

    return target_path


def launch_windows_update_installer(zip_path: str | Path) -> tuple[bool, str]:
    if not is_self_update_supported():
        return False, "Automatic app updates are only supported for packaged Windows builds."

    zip_path = Path(zip_path).resolve()
    if not zip_path.exists():
        return False, "Downloaded update package was not found."

    install_dir = app_root().resolve()
    exe_path = Path(sys.executable).resolve()
    script_path = zip_path.with_name("install-update.ps1")

    script_path.write_text(_windows_update_script(), encoding="utf-8")

    flags = 0
    flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    flags |= getattr(subprocess, "DETACHED_PROCESS", 0)

    try:
        subprocess.Popen(
            [
                "powershell.exe",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
                "-ZipPath",
                str(zip_path),
                "-InstallDir",
                str(install_dir),
                "-ExePath",
                str(exe_path),
                "-ParentPid",
                str(os.getpid()),
            ],
            creationflags=flags,
            close_fds=True,
        )
    except Exception as exc:
        return False, str(exc)

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

    def _score(item: ReleaseAsset) -> tuple[int, int]:
        name = item.name.casefold()
        if name.endswith(".zip"):
            return (0, 0)
        if name.endswith(".exe"):
            return (1, 0)
        return (2, len(name))

    parsed_assets.sort(key=_score)
    return parsed_assets[0]


def _version_parts(raw: str) -> list[int]:
    text = _extract_version(raw)
    if not text:
        return [0]
    return [int(part) for part in text.split(".")]


def _windows_update_script() -> str:
    return r"""
param(
    [Parameter(Mandatory = $true)][string]$ZipPath,
    [Parameter(Mandatory = $true)][string]$InstallDir,
    [Parameter(Mandatory = $true)][string]$ExePath,
    [Parameter(Mandatory = $true)][int]$ParentPid
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$extractDir = Join-Path ([System.IO.Path]::GetTempPath()) ("webtoon-reader-update-" + [Guid]::NewGuid().ToString("N"))
$errorPath = Join-Path $InstallDir "data\last_update_error.txt"

try {
    New-Item -ItemType Directory -Force -Path $extractDir | Out-Null

    for ($i = 0; $i -lt 240; $i++) {
        if (-not (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue)) {
            break
        }
        Start-Sleep -Milliseconds 500
    }

    Expand-Archive -LiteralPath $ZipPath -DestinationPath $extractDir -Force

    Get-ChildItem -LiteralPath $extractDir -Force | ForEach-Object {
        $destination = Join-Path $InstallDir $_.Name
        if ($_.PSIsContainer) {
            New-Item -ItemType Directory -Force -Path $destination | Out-Null
            Get-ChildItem -LiteralPath $_.FullName -Force | ForEach-Object {
                Copy-Item -LiteralPath $_.FullName -Destination $destination -Recurse -Force
            }
        } else {
            Copy-Item -LiteralPath $_.FullName -Destination $destination -Force
        }
    }

    Remove-Item -LiteralPath $errorPath -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 700
    Start-Process -FilePath $ExePath | Out-Null
}
catch {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $errorPath) | Out-Null
    $_ | Out-String | Set-Content -Path $errorPath -Encoding utf8
}
finally {
    Start-Sleep -Seconds 2
    Remove-Item -LiteralPath $extractDir -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $ZipPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
}
"""
