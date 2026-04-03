from __future__ import annotations

import json
import hashlib
import shutil
import tarfile
import zipfile
from pathlib import Path

from core.app_paths import data_path


SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".avif"}
SUPPORTED_ARCHIVE_EXTENSIONS = {".cbz", ".cbt"}
TEXT_PAYLOAD_FILENAMES = {"chapter.json", "chapter.html", "chapter.txt"}
_ARCHIVE_CACHE_ROOT = data_path("_chapter_cache")


def is_supported_image_name(name: str) -> bool:
    return Path(str(name or "")).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS


def is_archive_chapter_name(name: str) -> bool:
    return Path(str(name or "")).suffix.lower() in SUPPORTED_ARCHIVE_EXTENSIONS


def list_series_chapters(series_path: str) -> list[str]:
    root = Path(series_path)
    if not root.exists() or not root.is_dir():
        return []

    names: list[str] = []
    for entry in root.iterdir():
        if entry.is_dir() or (entry.is_file() and is_archive_chapter_name(entry.name)):
            names.append(entry.name)
    return names


def chapter_storage_path(series_path: str, chapter_name: str) -> Path:
    return Path(series_path) / str(chapter_name or "")


def chapter_cache_token(series_path: str, chapter_name: str) -> tuple[str, int]:
    chapter_path = chapter_storage_path(series_path, chapter_name)
    try:
        stat = chapter_path.stat()
        return str(chapter_path), int(stat.st_mtime_ns)
    except OSError:
        return str(chapter_path), -1


def chapter_content_path(series_path: str, chapter_name: str) -> str | None:
    chapter_path = chapter_storage_path(series_path, chapter_name)
    if chapter_path.is_dir():
        return str(chapter_path)
    if chapter_path.is_file() and is_archive_chapter_name(chapter_path.name):
        return _ensure_archive_cache(chapter_path)
    return None


def chapter_is_editable(series_path: str, chapter_name: str) -> bool:
    return chapter_storage_path(series_path, chapter_name).is_dir()


def chapter_has_text_payload(series_path: str, chapter_name: str) -> bool:
    content_path = chapter_content_path(series_path, chapter_name)
    if not content_path:
        return False
    root = Path(content_path)
    if root.joinpath("chapter.html").is_file() or root.joinpath("chapter.txt").is_file():
        return True
    json_path = root / "chapter.json"
    if not json_path.is_file():
        return False
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    return bool(str(payload.get("html") or "").strip() or str(payload.get("text") or "").strip())


def list_chapter_image_paths(series_path: str, chapter_name: str) -> list[str]:
    content_path = chapter_content_path(series_path, chapter_name)
    if not content_path:
        return []

    root = Path(content_path)
    direct_files = [
        entry
        for entry in root.iterdir()
        if entry.is_file() and is_supported_image_name(entry.name)
    ]
    if direct_files:
        return [str(path) for path in sorted(direct_files, key=lambda item: item.name.casefold())]

    nested_files = [
        entry
        for entry in root.rglob("*")
        if entry.is_file() and is_supported_image_name(entry.name)
    ]
    return [
        str(path)
        for path in sorted(
            nested_files,
            key=lambda item: str(item.relative_to(root)).replace("\\", "/").casefold(),
        )
    ]


def count_chapter_images(series_path: str, chapter_name: str) -> int:
    return len(list_chapter_image_paths(series_path, chapter_name))


def _ensure_archive_cache(chapter_path: Path) -> str | None:
    try:
        stat = chapter_path.stat()
        token = f"{chapter_path.resolve()}|{int(stat.st_mtime_ns)}|{int(stat.st_size)}"
    except OSError:
        return None

    cache_key = hashlib.sha1(token.encode("utf-8", errors="ignore")).hexdigest()
    target_root = _ARCHIVE_CACHE_ROOT / cache_key
    done_marker = target_root / ".ready"
    if done_marker.is_file():
        return str(target_root)

    if target_root.exists():
        shutil.rmtree(target_root, ignore_errors=True)
    target_root.mkdir(parents=True, exist_ok=True)

    try:
        suffix = chapter_path.suffix.lower()
        if suffix == ".cbz":
            with zipfile.ZipFile(chapter_path) as archive:
                archive.extractall(target_root)
        elif suffix == ".cbt":
            with tarfile.open(chapter_path) as archive:
                archive.extractall(target_root)
        else:
            return None
        done_marker.write_text("ok", encoding="utf-8")
        return str(target_root)
    except Exception:
        shutil.rmtree(target_root, ignore_errors=True)
        return None
