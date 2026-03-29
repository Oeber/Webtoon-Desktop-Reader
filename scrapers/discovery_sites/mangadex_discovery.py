from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time

import requests

from scrapers.base import ScraperError
from scrapers.discovery_base import BaseDiscoveryProvider
from scrapers.models import CatalogPage, CatalogSeries
from scrapers.sites.mangadex import MangaDexScraper
from stores.settings_store import load_scraper_default_config


class MangaDexDiscoveryProvider(BaseDiscoveryProvider):
    site_name = "mangadex"
    site_display_name = "MangaDex"
    site_hosts = ("mangadex.org", "www.mangadex.org")
    site_base_url = "https://mangadex.org/"

    BASE = "https://mangadex.org"
    API_BASE = "https://api.mangadex.org"
    COVER_BASE = "https://uploads.mangadex.org/covers"
    PAGE_SIZE = 24
    COUNT_FETCH_WORKERS = 6
    API_RETRY_ATTEMPTS = 3
    DEFAULT_RETRY_AFTER_SECONDS = 1.0
    LANGUAGE_PRIORITY = (
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
    CONTENT_RATINGS = ("safe", "suggestive", "erotica", "pornographic")

    _count_cache: dict[tuple[str, tuple[str, ...]], int | None] = {}
    _count_cache_lock = threading.Lock()

    def _configured_scraper(self) -> MangaDexScraper:
        scraper = MangaDexScraper()
        scraper.apply_source_config(load_scraper_default_config(self.site_name))
        return scraper

    def _configured_languages(self) -> list[str]:
        return list(self._configured_scraper()._language_priority())

    def get_display_name(self) -> str:
        return self.site_display_name

    def get_catalog_page(self, page: int = 1, search_query: str = "") -> CatalogPage:
        page = max(1, int(page))
        query = " ".join(str(search_query or "").split()).strip()
        offset = (page - 1) * self.PAGE_SIZE

        params = {
            "limit": self.PAGE_SIZE,
            "offset": offset,
            "includes[]": ["cover_art", "author", "artist"],
            "availableTranslatedLanguage[]": self._configured_languages(),
            "contentRating[]": list(self.CONTENT_RATINGS),
        }
        if query:
            params["title"] = query
            params["order[relevance]"] = "desc"
        else:
            params["order[latestUploadedChapter]"] = "desc"

        payload = self._api_get("/manga", params=params)
        items = payload.get("data") or []
        total = int(payload.get("total") or 0)

        entries = []
        seen = set()
        for item in items:
            entry = self._catalog_entry_from_item(item)
            if entry is None:
                continue
            key = entry.identity_key()
            if key in seen:
                continue
            seen.add(key)
            entries.append(entry)

        self._populate_language_counts(entries)

        return CatalogPage(
            site=self.site_name,
            page=page,
            entries=entries,
            has_next_page=(offset + self.PAGE_SIZE) < total,
        )

    def _api_get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.API_BASE}{path}"
        last_status = None
        for attempt in range(self.API_RETRY_ATTEMPTS):
            response = requests.get(url, params=params, headers=self._request_headers(url), timeout=30)
            last_status = response.status_code
            if response.status_code == 200:
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise ScraperError(f"MangaDex discovery returned invalid JSON for: {url}") from exc
                if str(payload.get("result") or "").strip().casefold() != "ok":
                    raise ScraperError(f"MangaDex discovery returned an unexpected result for: {url}")
                return payload
            if response.status_code == 429 and attempt < (self.API_RETRY_ATTEMPTS - 1):
                time.sleep(self._retry_delay_seconds(response, attempt))
                continue
            raise ScraperError(f"MangaDex discovery request failed: {url} ({response.status_code})")
        raise ScraperError(f"MangaDex discovery request failed: {url} ({last_status or 'unknown'})")

    def _retry_delay_seconds(self, response, attempt: int) -> float:
        header = str(response.headers.get("Retry-After") or "").strip()
        if header:
            try:
                return max(0.5, float(header))
            except ValueError:
                pass
        return self.DEFAULT_RETRY_AFTER_SECONDS * (attempt + 1)

    def _catalog_entry_from_item(self, item: dict) -> CatalogSeries | None:
        scraper = self._configured_scraper()
        manga_id = str(item.get("id") or "").strip()
        if not manga_id:
            return None

        attributes = item.get("attributes") or {}
        relationships = item.get("relationships") or []

        title = scraper._pick_localized_text(
            attributes.get("title"),
            fallback_sources=attributes.get("altTitles") or [],
        )
        if not title:
            return None

        description = scraper._pick_localized_text(attributes.get("description")) or None
        author_names = self._relationship_names(relationships, {"author"})
        if not author_names:
            author_names = self._relationship_names(relationships, {"artist"})
        author = ", ".join(author_names) or None

        cover_file = self._relationship_attr(relationships, "cover_art", "fileName")
        cover_url = f"{self.COVER_BASE}/{manga_id}/{cover_file}" if cover_file else None

        return CatalogSeries(
            site=self.site_name,
            series_id=manga_id,
            title=title,
            url=f"{self.BASE}/title/{manga_id}",
            cover_url=cover_url,
            cover_headers=self._request_headers(cover_url or self.BASE),
            author=author,
            description=description,
            latest_chapter=None,
            total_chapters=None,
        )

    def _populate_language_counts(self, entries: list[CatalogSeries]) -> None:
        uncached = []
        language_key = tuple(self._configured_languages())
        for entry in entries:
            manga_id = str(getattr(entry, "series_id", "") or "").strip()
            if not manga_id:
                continue
            cache_key = (manga_id, language_key)
            with self._count_cache_lock:
                if cache_key in self._count_cache:
                    entry.total_chapters = self._count_cache[cache_key]
                    continue
            uncached.append((manga_id, entry, cache_key))

        if not uncached:
            return

        workers = min(self.COUNT_FETCH_WORKERS, len(uncached))
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {
                pool.submit(self._language_count_for_manga, manga_id): (entry, cache_key)
                for manga_id, entry, cache_key in uncached
            }
            for future in as_completed(futures):
                entry, cache_key = futures[future]
                count = None
                try:
                    count = future.result()
                except Exception:
                    count = None
                with self._count_cache_lock:
                    self._count_cache[cache_key] = count
                entry.total_chapters = count

    def _language_count_for_manga(self, manga_id: str) -> int | None:
        scraper = self._configured_scraper()
        chapters = scraper._fetch_chapters(manga_id, translated_languages=self._configured_languages())
        count = len(chapters)
        return count if count > 0 else None

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

    def _request_headers(self, url: str) -> dict[str, str]:
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
