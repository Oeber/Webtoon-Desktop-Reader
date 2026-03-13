from hashlib import md5

from .models import CatalogSeries, ChapterInfo, SeriesInfo


DEMO_BASE = "https://cool-webtoons.local"
DEMO_COVER_URL = "https://placehold.co/600x900/FFFFFF/FFFFFF.png"
DEMO_DESCRIPTION = "Lorem ipsum dolor sit amet, consectetur adipiscing elit."
DEMO_AUTHOR = "Lorem Ipsum"
DEMO_TITLES = [
    "Cool Webtoon",
    "The Best Webtoon",
    "Amazing Webtoon",
    "Top Tier Webtoon",
    "Super Cool Comic",
    "Legendary Webtoon",
    "Ultimate Webtoon",
    "Greatest Comic Ever",
]


def demo_title_for(series_id: str, *, prefix: str = "") -> str:
    normalized = str(series_id or "").strip().casefold()
    digest = md5(normalized.encode("utf-8")).hexdigest() if normalized else "0"
    base = DEMO_TITLES[int(digest[:8], 16) % len(DEMO_TITLES)]
    if prefix:
        return f"{prefix} {base}".strip()
    return base


def demo_series_url(series_id: str) -> str:
    return f"{DEMO_BASE}/series/{series_id}".rstrip("/")


def demo_chapter_url(series_id: str, chapter_slug: str) -> str:
    return f"{DEMO_BASE}/series/{series_id}/{chapter_slug}".rstrip("/")


def rewrite_catalog_entry(entry: CatalogSeries, *, site_name: str) -> CatalogSeries:
    series_id = str(entry.series_id or "").strip()
    return CatalogSeries(
        site=site_name,
        series_id=series_id,
        title=demo_title_for(series_id),
        url=demo_series_url(series_id),
        cover_url=DEMO_COVER_URL,
        cover_headers={},
        author=DEMO_AUTHOR,
        description=DEMO_DESCRIPTION,
        latest_chapter=entry.latest_chapter,
        total_chapters=entry.total_chapters,
    )


def rewrite_series_info(series: SeriesInfo, *, site_name: str) -> SeriesInfo:
    series_id = str(series.series_id or "").strip()
    rewritten_chapters = []
    for chapter in list(series.chapters or []):
        chapter_slug = str(chapter.id or "").strip() or chapter.url.rstrip("/").rsplit("/", 1)[-1]
        chapter_number = chapter.number
        chapter_title = f"Chapter {int(chapter_number)}" if isinstance(chapter_number, float) and chapter_number.is_integer() else (
            f"Chapter {chapter_number}" if chapter_number is not None else "Chapter"
        )
        rewritten_chapters.append(
            ChapterInfo(
                id=chapter_slug,
                number=chapter_number,
                title=chapter_title,
                url=demo_chapter_url(series_id, chapter_slug),
                pages=chapter.pages,
            )
        )

    return SeriesInfo(
        site=site_name,
        series_id=series_id,
        title=demo_title_for(series_id),
        url=demo_series_url(series_id),
        cover_url=DEMO_COVER_URL,
        author=DEMO_AUTHOR,
        description=DEMO_DESCRIPTION,
        total_chapters=series.total_chapters,
        chapters=rewritten_chapters,
    )
