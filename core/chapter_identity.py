from __future__ import annotations

import hashlib


def _escape_chapter_identity(value: str) -> str:
    return str(value or "").replace(":", "%3A")


def build_local_chapter_key(webtoon_name: str, chapter_name: str) -> str:
    return f"local::{_escape_chapter_identity(webtoon_name)}::{_escape_chapter_identity(chapter_name)}"


def build_remote_chapter_key(site_name: str, series_id: str, chapter_id: str = "", remote_url: str = "") -> str:
    site = _escape_chapter_identity(site_name)
    series = _escape_chapter_identity(series_id)
    chapter = str(chapter_id or "").strip()
    if chapter:
        return f"remote::{site}::{series}::{_escape_chapter_identity(chapter)}"
    url_hash = hashlib.sha1(str(remote_url or "").encode("utf-8", errors="ignore")).hexdigest()
    return f"remote::{site}::{series}::url::{url_hash}"
