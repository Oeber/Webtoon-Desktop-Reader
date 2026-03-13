from dataclasses import dataclass, field
from typing import List, Optional


def normalize_catalog_text(value: str) -> str:
    return " ".join((value or "").casefold().split())


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

    def normalized_title(self) -> str:
        return normalize_catalog_text(self.title)

    def source_key(self) -> tuple[str, str] | None:
        site = str(self.site or "").strip()
        series_id = str(self.series_id or "").strip()
        if not site or not series_id:
            return None
        return site, series_id

    def identity_key(self) -> str:
        return str(self.url or self.series_id or self.title or "")

    def search_text(self) -> str:
        return " ".join(
            [
                str(self.title or ""),
                str(self.author or ""),
                str(self.description or ""),
                str(self.latest_chapter or ""),
            ]
        )

    def matches_query(self, query: str) -> bool:
        normalized_query = normalize_catalog_text(query)
        if not normalized_query:
            return True
        return normalized_query in normalize_catalog_text(self.search_text())


@dataclass
class CatalogPage:
    site: str
    page: int
    entries: List[CatalogSeries] = field(default_factory=list)
    has_next_page: bool = False
