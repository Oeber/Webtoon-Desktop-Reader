from __future__ import annotations

import json
import shutil
from pathlib import Path

from core.app_paths import data_path


_REMOTE_CACHE_ROOT = data_path("remote_cache")


def remote_cache_root() -> Path:
    _REMOTE_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    return _REMOTE_CACHE_ROOT


def tracked_title_cache_root(track_id: str) -> Path:
    root = remote_cache_root() / "titles" / str(track_id or "").strip()
    root.mkdir(parents=True, exist_ok=True)
    return root


def tracked_title_metadata_path(track_id: str) -> Path:
    return tracked_title_cache_root(track_id) / "meta.json"


def cached_chapter_root(track_id: str, chapter_key: str) -> Path:
    chapter_hash = _chapter_key_hash(chapter_key)
    root = tracked_title_cache_root(track_id) / "chapters" / chapter_hash
    root.mkdir(parents=True, exist_ok=True)
    return root


def cached_chapter_metadata_path(track_id: str, chapter_key: str) -> Path:
    return cached_chapter_root(track_id, chapter_key) / "chapter.json"


def cached_chapter_image_path(track_id: str, chapter_key: str, filename: str) -> Path:
    return cached_chapter_root(track_id, chapter_key) / str(filename or "").strip()


def write_json_atomic(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    temp_path.replace(path)
    return path


def replace_tree_atomic(target_root: Path, source_root: Path) -> Path:
    target_root = Path(target_root)
    source_root = Path(source_root)
    target_root.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target_root.with_name(target_root.name + ".tmp")
    if temp_target.exists():
        shutil.rmtree(temp_target, ignore_errors=True)
    source_root.replace(temp_target)
    if target_root.exists():
        shutil.rmtree(target_root, ignore_errors=True)
    temp_target.replace(target_root)
    return target_root


def _chapter_key_hash(chapter_key: str) -> str:
    import hashlib

    return hashlib.sha1(str(chapter_key or "").encode("utf-8", errors="ignore")).hexdigest()
