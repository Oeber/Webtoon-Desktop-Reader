import re
import threading
import time
from urllib.parse import urlparse

import requests

from ..base import BaseScraper, ScraperError
from ..models import ChapterInfo, PageInfo, SeriesInfo


class MangaDexScraper(BaseScraper):
    site_name = "mangadex"
    site_display_name = "MangaDex"
    content_type = "manga"
    site_hosts = ("mangadex.org", "www.mangadex.org")
    site_base_url = "https://mangadex.org/"

    BASE = "https://mangadex.org"
    API_BASE = "https://api.mangadex.org"
    COVER_BASE = "https://uploads.mangadex.org/covers"
    DEFAULT_LANGUAGE_PRIORITY = (
        "en",
        "pt-br",
        "pt",
        "es-la",
        "es",
        "fr",
        "de",
        "it",
        "ja-ro",
        "ja",
    )
    TITLE_RE = re.compile(r"^/title/([0-9a-f-]{36})(?:/[^/?#]+)?/?$", re.IGNORECASE)
    CHAPTER_RE = re.compile(r"^/chapter/([0-9a-f-]{36})(?:/[^/?#]+)?/?$", re.IGNORECASE)
    API_RETRY_ATTEMPTS = 4
    DEFAULT_RETRY_AFTER_SECONDS = 1.5
    AT_HOME_MIN_INTERVAL_SECONDS = 0.35

    def __init__(self):
        self._at_home_cache: dict[str, dict] = {}
        self._at_home_lock = threading.Lock()
        self._next_at_home_request_at = 0.0

    @classmethod
    def can_handle(cls, url: str) -> bool:
        parsed = urlparse(str(url or "").strip())
        host = (parsed.netloc or "").casefold()
        if host not in {"mangadex.org", "www.mangadex.org"}:
            return False
        path = parsed.path or ""
        return bool(cls.TITLE_RE.match(path) or cls.CHAPTER_RE.match(path))

    def get_request_headers(self, url: str) -> dict:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            "Referer": self.BASE + "/",
            "Origin": self.BASE,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def is_chapter_url(self, url: str) -> bool:
        parsed = urlparse(str(url or "").strip())
        return self.CHAPTER_RE.match(parsed.path or "") is not None

    def series_url_from_chapter_url(self, url: str) -> str:
        chapter_id = self._extract_chapter_id(url)
        payload = self._api_get(f"/chapter/{chapter_id}")
        manga_id = self._relationship_id(payload.get("data", {}).get("relationships"), "manga")
        if not manga_id:
            raise ScraperError(f"Could not determine MangaDex title for chapter: {url}")
        return self._title_url(manga_id)

    def get_series_info(self, url: str, session=None) -> SeriesInfo:
        if self.is_chapter_url(url):
            url = self.series_url_from_chapter_url(url)

        manga_id = self._extract_manga_id(url)
        payload = self._api_get(
            f"/manga/{manga_id}",
            params={
                "includes[]": ["cover_art", "author", "artist"],
            },
            session=session,
        )
        data = payload.get("data") or {}
        attributes = data.get("attributes") or {}
        relationships = data.get("relationships") or []

        title = self._pick_localized_text(
            attributes.get("title"),
            fallback_sources=attributes.get("altTitles") or [],
        ) or f"MangaDex {manga_id}"
        description = self._pick_localized_text(attributes.get("description")) or None
        language = self._preferred_language(attributes.get("availableTranslatedLanguages") or [])
        chapters = self._fetch_chapters(manga_id, translated_language=language, session=session)

        cover_file = self._relationship_attr(relationships, "cover_art", "fileName")
        cover_url = f"{self.COVER_BASE}/{manga_id}/{cover_file}" if cover_file else None

        author_names = self._relationship_names(relationships, {"author"})
        if not author_names:
            author_names = self._relationship_names(relationships, {"artist"})
        author = ", ".join(author_names) or None

        return SeriesInfo(
            site=self.site_name,
            series_id=manga_id,
            title=title,
            url=self._title_url(manga_id),
            content_type=self.content_type,
            cover_url=cover_url,
            author=author,
            description=description,
            total_chapters=len(chapters),
            chapters=chapters,
        )

    def get_chapter_pages(self, chapter_url: str, session=None) -> list[PageInfo]:
        chapter_id = self._extract_chapter_id(chapter_url)
        payload = self._at_home_payload(chapter_id, session=session)
        base_url = str(payload.get("baseUrl") or "").strip()
        chapter = payload.get("chapter") or {}
        chapter_hash = str(chapter.get("hash") or "").strip()
        filenames = chapter.get("data") or []

        if not base_url or not chapter_hash or not filenames:
            raise ScraperError(f"No chapter images found: {chapter_url}")

        pages = []
        for index, filename in enumerate(filenames, start=1):
            name = str(filename or "").strip()
            if not name:
                continue
            image_url = f"{base_url}/data/{chapter_hash}/{name}"
            pages.append(PageInfo(index=index, image_url=image_url))

        if not pages:
            raise ScraperError(f"No chapter images found: {chapter_url}")
        return pages

    def _at_home_payload(self, chapter_id: str, session=None) -> dict:
        with self._at_home_lock:
            cached = self._at_home_cache.get(chapter_id)
            if cached is not None:
                return cached

            wait_seconds = self._next_at_home_request_at - time.monotonic()
            if wait_seconds > 0:
                time.sleep(wait_seconds)

            payload = self._api_get(f"/at-home/server/{chapter_id}", session=session)
            self._at_home_cache[chapter_id] = payload
            self._next_at_home_request_at = time.monotonic() + self.AT_HOME_MIN_INTERVAL_SECONDS
            return payload

    def _api_get(self, path: str, params: dict | None = None, session=None) -> dict:
        client = session or requests
        url = f"{self.API_BASE}{path}"

        last_status = None
        for attempt in range(self.API_RETRY_ATTEMPTS):
            response = client.get(url, params=params, headers=self.get_request_headers(url), timeout=30)
            last_status = response.status_code
            if response.status_code == 200:
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise ScraperError(f"MangaDex API returned invalid JSON for: {url}") from exc
                if str(payload.get("result") or "").strip().casefold() != "ok":
                    raise ScraperError(f"MangaDex API returned an unexpected result for: {url}")
                return payload

            if response.status_code == 429 and attempt < (self.API_RETRY_ATTEMPTS - 1):
                time.sleep(self._retry_delay_seconds(response, attempt))
                continue

            raise ScraperError(f"MangaDex API request failed: {url} ({response.status_code})")

        raise ScraperError(f"MangaDex API request failed: {url} ({last_status or 'unknown'})")

    def _retry_delay_seconds(self, response, attempt: int) -> float:
        header = str(response.headers.get("Retry-After") or "").strip()
        if header:
            try:
                return max(0.5, float(header))
            except ValueError:
                pass
        return self.DEFAULT_RETRY_AFTER_SECONDS * (attempt + 1)

    def _fetch_chapters(self, manga_id: str, translated_language: str | None, session=None) -> list[ChapterInfo]:
        chapters_by_key: dict[str, tuple[dict, dict]] = {}
        offset = 0
        limit = 100
        total = None

        while total is None or offset < total:
            params = {
                "limit": limit,
                "offset": offset,
                "order[volume]": "asc",
                "order[chapter]": "asc",
                "order[readableAt]": "asc",
                "includes[]": ["scanlation_group"],
            }
            if translated_language:
                params["translatedLanguage[]"] = [translated_language]
            payload = self._api_get(f"/manga/{manga_id}/feed", params=params, session=session)
            items = payload.get("data") or []
            total = int(payload.get("total") or 0)

            for item in items:
                attributes = item.get("attributes") or {}
                if attributes.get("externalUrl") or attributes.get("isUnavailable"):
                    continue

                chapter_id = str(item.get("id") or "").strip()
                if not chapter_id:
                    continue

                dedupe_key = self._chapter_dedupe_key(attributes, chapter_id)
                current = chapters_by_key.get(dedupe_key)
                if current is None or self._chapter_sort_key(item) > self._chapter_sort_key(current[0]):
                    chapters_by_key[dedupe_key] = (item, attributes)

            offset += limit
            if not items:
                break

        chapters: list[ChapterInfo] = []
        for item, attributes in chapters_by_key.values():
            chapter_id = str(item.get("id") or "").strip()
            chapter_number = self._parse_number(attributes.get("chapter"))
            chapter_title = self._chapter_title(attributes, chapter_number, chapter_id)
            chapters.append(
                ChapterInfo(
                    id=chapter_id,
                    number=chapter_number,
                    title=chapter_title,
                    url=self._chapter_url(chapter_id),
                )
            )

        chapters.sort(
            key=lambda chapter: (
                chapter.number is None,
                chapter.number if chapter.number is not None else float("inf"),
                chapter.title.casefold(),
                chapter.url,
            )
        )
        return chapters

    def _chapter_dedupe_key(self, attributes: dict, chapter_id: str) -> str:
        chapter_number = str(attributes.get("chapter") or "").strip()
        volume_number = str(attributes.get("volume") or "").strip()
        title = " ".join(str(attributes.get("title") or "").split()).strip()
        if chapter_number:
            return f"chapter:{volume_number}:{chapter_number}"
        if title:
            return f"title:{title.casefold()}"
        return f"id:{chapter_id}"

    def _chapter_sort_key(self, item: dict) -> tuple:
        attributes = item.get("attributes") or {}
        group_names = self._relationship_names(item.get("relationships") or [], {"scanlation_group"})
        return (
            int(attributes.get("pages") or 0),
            str(attributes.get("readableAt") or ""),
            str(attributes.get("updatedAt") or ""),
            1 if group_names else 0,
            str(item.get("id") or ""),
        )

    def _chapter_title(self, attributes: dict, chapter_number: float | None, chapter_id: str) -> str:
        title = " ".join(str(attributes.get("title") or "").split()).strip()
        if chapter_number is not None:
            label = self._format_chapter_number(chapter_number)
            if title:
                return f"Chapter {label} - {title}"
            return f"Chapter {label}"
        if title:
            return title
        return f"Chapter {chapter_id}"

    def _format_chapter_number(self, value: float) -> str:
        if float(value).is_integer():
            return str(int(value))
        return format(value, "g")

    def _preferred_language(self, languages: list[str]) -> str | None:
        normalized = [
            str(language or "").strip().casefold()
            for language in (languages or [])
            if str(language or "").strip()
        ]
        for candidate in self.DEFAULT_LANGUAGE_PRIORITY:
            if candidate in normalized:
                return candidate
        return normalized[0] if normalized else None

    def _pick_localized_text(self, mapping, fallback_sources: list[dict] | None = None) -> str:
        candidates = []
        if isinstance(mapping, dict):
            candidates.append(mapping)
        for item in fallback_sources or []:
            if isinstance(item, dict):
                candidates.append(item)

        for language in self.DEFAULT_LANGUAGE_PRIORITY:
            for candidate in candidates:
                value = " ".join(str(candidate.get(language) or "").split()).strip()
                if value:
                    return value

        for candidate in candidates:
            for value in candidate.values():
                text = " ".join(str(value or "").split()).strip()
                if text:
                    return text
        return ""

    def _relationship_id(self, relationships: list[dict], expected_type: str) -> str:
        for rel in relationships or []:
            if str(rel.get("type") or "").strip() != expected_type:
                continue
            value = str(rel.get("id") or "").strip()
            if value:
                return value
        return ""

    def _relationship_attr(self, relationships: list[dict], expected_type: str, attr_name: str) -> str:
        for rel in relationships or []:
            if str(rel.get("type") or "").strip() != expected_type:
                continue
            attributes = rel.get("attributes") or {}
            value = str(attributes.get(attr_name) or "").strip()
            if value:
                return value
        return ""

    def _relationship_names(self, relationships: list[dict], allowed_types: set[str]) -> list[str]:
        names = []
        seen = set()
        for rel in relationships or []:
            rel_type = str(rel.get("type") or "").strip()
            if rel_type not in allowed_types:
                continue
            attributes = rel.get("attributes") or {}
            name = " ".join(str(attributes.get("name") or "").split()).strip()
            if not name:
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            names.append(name)
        return names

    def _parse_number(self, value) -> float | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def _extract_manga_id(self, url: str) -> str:
        parsed = urlparse(str(url or "").strip())
        match = self.TITLE_RE.match(parsed.path or "")
        if not match:
            raise ScraperError(f"Unsupported MangaDex title URL: {url}")
        return match.group(1)

    def _extract_chapter_id(self, url: str) -> str:
        parsed = urlparse(str(url or "").strip())
        match = self.CHAPTER_RE.match(parsed.path or "")
        if not match:
            raise ScraperError(f"Unsupported MangaDex chapter URL: {url}")
        return match.group(1)

    def _title_url(self, manga_id: str) -> str:
        return f"{self.BASE}/title/{manga_id}"

    def _chapter_url(self, chapter_id: str) -> str:
        return f"{self.BASE}/chapter/{chapter_id}"
