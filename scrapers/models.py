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