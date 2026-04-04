from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class TrackedTitle:
    track_id: str
    site_name: str
    series_id: str
    title: str
    source_url: str = ""
    content_type: str = "webtoon"
    cover_url: str = ""
    status: str = "tracked"
    cache_status: str = "none"
    local_webtoon_name: str = ""
    last_read_chapter_key: str = ""


@dataclass(slots=True)
class HybridChapterRef:
    chapter_key: str
    owner_kind: str
    owner_id: str
    site_name: str = ""
    series_id: str = ""
    remote_chapter_id: str = ""
    remote_url: str = ""
    local_chapter_name: str = ""
    chapter_title: str = ""
    chapter_number: float | None = None
    cache_path: str = ""
    cache_state: str = "none"


@dataclass(slots=True)
class ViewerChapterSource:
    chapter_key: str
    title: str
    number: float | None = None
    content_type: str = "webtoon"
    source_kind: str = "local"
    storage_path: str = ""
    remote_url: str = ""
    page_urls: list[str] | None = None
    local_chapter_name: str = ""
