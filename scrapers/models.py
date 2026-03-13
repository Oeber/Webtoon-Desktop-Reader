from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class PageInfo:
    index: int
    image_url: str


@dataclass
class ChapterInfo:
    id: str
    number: Optional[float]
    title: str
    url: str
    pages: Optional[List[PageInfo]] = None


@dataclass
class SeriesInfo:
    site: str
    series_id: str
    title: str
    url: str
    cover_url: Optional[str] = None
    author: Optional[str] = None
    description: Optional[str] = None
    total_chapters: Optional[int] = None
    chapters: List[ChapterInfo] = field(default_factory=list)


@dataclass
class CatalogSeries:
    site: str
    series_id: str
    title: str
    url: str
    cover_url: Optional[str] = None
    cover_headers: dict[str, str] = field(default_factory=dict)
    author: Optional[str] = None
    description: Optional[str] = None
    latest_chapter: Optional[str] = None
    total_chapters: Optional[int] = None


@dataclass
class CatalogPage:
    site: str
    page: int
    entries: List[CatalogSeries] = field(default_factory=list)
    has_next_page: bool = False
