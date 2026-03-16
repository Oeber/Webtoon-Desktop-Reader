import re
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup

from core.app_logging import get_logger
from scrapers.base import ScraperError
from scrapers.discovery_base import BaseDiscoveryProvider
from scrapers.models import CatalogPage, CatalogSeries

logger = get_logger(__name__)


class OmegaScansDiscoveryProvider(BaseDiscoveryProvider):

    site_name = "omega_scans"

    BASE = "https://omegascans.org"
    API_BASE = "https://api.omegascans.org"

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Referer": BASE,
    }

    API_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Referer": BASE + "/",
        "Accept": "application/json",
        "Origin": BASE,
    }

    def get_catalog_page(self, page: int = 1, search_query: str = "") -> CatalogPage:
        logger.info("OmegaScans discovery: fetching catalog page %d search=%r", page, search_query)
        result = self._fetch_catalog_from_api(page, search_query=search_query)
        if result is None and not search_query.strip():
            result = self._fetch_catalog_from_html(page)
        if result is None:
            raise ScraperError(f"Failed to load OmegaScans catalog page {page}")
        return result

    def _fetch_catalog_from_api(self, page: int, *, search_query: str = "") -> CatalogPage | None:
        params = {
            "page": max(1, int(page)),
            "perPage": 100,
            "series_type": "Comic",
            "query_string": str(search_query or "").strip(),
            "orderBy": "created_at",
            "adult": "true",
            "order": "desc",
            "status": "All",
            "tags_ids": "[]",
        }
        url = f"{self.API_BASE}/query?{urlencode(params)}"

        try:
            response = requests.get(url, headers=self.API_HEADERS, timeout=30)
            if response.status_code != 200:
                logger.warning("OmegaScans discovery: query API returned %d for page %d", response.status_code, page)
                return None
            payload = response.json()
        except Exception as e:
            logger.warning("OmegaScans discovery: query API failed for page %d: %s", page, e)
            return None

        if not isinstance(payload, dict):
            return None
        raw_entries = payload.get("data") or []
        meta = payload.get("meta") or {}
        last_page = meta.get("last_page") or meta.get("lastPage") or page

        entries = []
        for raw in raw_entries:
            entry = self._catalog_entry_from_api(raw)
            if entry is not None:
                entries.append(entry)

        return CatalogPage(
            site=self.site_name,
            page=page,
            entries=entries,
            has_next_page=page < int(last_page or page),
        )

    def _catalog_entry_from_api(self, raw: dict) -> CatalogSeries | None:
        if not isinstance(raw, dict):
            return None

        slug = str(raw.get("series_slug") or raw.get("slug") or "").strip()
        title = str(raw.get("title") or raw.get("series_name") or raw.get("name") or "").strip()
        if not slug or not title:
            return None

        cover = raw.get("thumbnail") or raw.get("cover") or raw.get("cover_url") or raw.get("poster")
        description = raw.get("description") or raw.get("summary") or raw.get("synopsis")
        author = raw.get("author") or raw.get("artists") or raw.get("writer")
        meta = raw.get("meta") or {}
        chapters_count = meta.get("chapters_count")
        try:
            total_chapters = int(chapters_count) if chapters_count is not None else None
        except (TypeError, ValueError):
            total_chapters = None
        return CatalogSeries(
            site=self.site_name,
            series_id=slug,
            title=title,
            url=f"{self.BASE}/series/{slug}",
            cover_url=self._normalize_asset_url(str(cover or "")) or None,
            cover_headers=dict(self.HEADERS),
            author=self._compact_text(author),
            description=self._compact_text(description),
            latest_chapter=None,
            total_chapters=total_chapters,
        )

    def _fetch_catalog_from_html(self, page: int) -> CatalogPage | None:
        candidate_urls = self._catalog_candidate_urls(page)
        for url in candidate_urls:
            try:
                response = requests.get(url, headers=self.HEADERS, timeout=20)
            except Exception as e:
                logger.warning("OmegaScans discovery: html fetch failed for %s: %s", url, e)
                continue

            if response.status_code != 200:
                logger.warning("OmegaScans discovery: html route %s returned %d", url, response.status_code)
                continue

            page_result = self._catalog_page_from_html(page, response.text, source_url=url)
            if page_result.entries:
                return page_result

        return None

    def _catalog_page_from_html(self, page: int, html: str, source_url: str) -> CatalogPage:
        soup = BeautifulSoup(html, "html.parser")
        entries = []
        seen_urls = set()

        for link in soup.select("a[href]"):
            entry = self._catalog_entry_from_link(link, seen_urls)
            if entry is not None:
                entries.append(entry)

        has_next_page = bool(soup.find("a", href=re.compile(rf"[?&]page={page + 1}\b")))
        logger.info(
            "OmegaScans discovery: scraped %d entries from %s",
            len(entries),
            source_url,
        )
        return CatalogPage(
            site=self.site_name,
            page=page,
            entries=entries,
            has_next_page=has_next_page,
        )

    def _catalog_candidate_urls(self, page: int) -> list[str]:
        page = max(1, int(page))
        candidates = [
            f"{self.BASE}/comics?page={page}",
            f"{self.BASE}/comics",
        ]
        if page == 1:
            candidates.extend(
                [
                    self.BASE,
                    f"{self.BASE}/novels",
                ]
            )
        return candidates

    def _catalog_entry_from_link(self, link, seen_urls: set[str]) -> CatalogSeries | None:
        href = urljoin(self.BASE, (link.get("href") or "").strip()).rstrip("/")
        if "/series/" not in href:
            return None

        slug = self._extract_series_slug(href)
        if not slug or href in seen_urls:
            return None

        title = self._extract_link_title(link, slug)
        if not title:
            return None

        seen_urls.add(href)
        description = self._extract_card_description(link)
        latest_chapter = self._extract_latest_chapter(link)
        cover_url = self._extract_link_cover(link)
        total_chapters = self._extract_total_chapters(link)

        return CatalogSeries(
            site=self.site_name,
            series_id=slug,
            title=title,
            url=href,
            cover_url=cover_url,
            cover_headers=dict(self.HEADERS),
            description=description,
            latest_chapter=latest_chapter,
            total_chapters=total_chapters,
        )

    def _extract_link_title(self, link, slug: str) -> str:
        title = link.get_text(" ", strip=True)
        if title:
            return " ".join(title.split())
        image = link.find("img")
        if image:
            alt = " ".join((image.get("alt") or "").split()).strip()
            if alt:
                return alt
        return slug.replace("-", " ").title()

    def _extract_card_description(self, link) -> str | None:
        container = link.parent
        if container is None:
            return None
        text = " ".join(container.get_text(" ", strip=True).split())
        title = " ".join(link.get_text(" ", strip=True).split())
        if title and text.startswith(title):
            text = text[len(title):].strip(" |-")
        if len(text) < 30:
            return None
        return text[:217].rstrip() + "..." if len(text) > 220 else text

    def _extract_latest_chapter(self, link) -> str | None:
        container = link.parent
        if container is None:
            return None
        text = " ".join(container.get_text(" ", strip=True).split())
        match = re.search(r"(chapter\s+\d+(?:\.\d+)?)", text, re.IGNORECASE)
        if match:
            return match.group(1)
        return None

    def _extract_total_chapters(self, link) -> int | None:
        container = link.parent
        if container is None:
            return None
        text = " ".join(container.get_text(" ", strip=True).split())
        match = re.search(r"(\d+)\s+chapters?\b", text, re.IGNORECASE)
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    def _extract_link_cover(self, link) -> str | None:
        image = link.find("img")
        if image is None:
            return None
        for attr in ("src", "data-src"):
            value = self._normalize_asset_url((image.get(attr) or "").strip())
            if value:
                return value
        return None

    def _extract_series_slug(self, url: str) -> str | None:
        url = url.rstrip("/")
        marker = "/series/"
        idx = url.find(marker)
        if idx == -1:
            return None
        slug = url[idx + len(marker):]
        if "/" in slug:
            slug = slug.split("/", 1)[0]
        return slug.strip() or None

    def _normalize_asset_url(self, raw: str) -> str:
        value = raw.strip()
        if not value:
            return ""
        if value.startswith("//"):
            return "https:" + value
        if value.startswith("/"):
            return urljoin(self.BASE, value)
        return value if value.startswith("http") else ""

    def _compact_text(self, value) -> str | None:
        if value is None:
            return None
        if isinstance(value, list):
            value = ", ".join(str(item).strip() for item in value if str(item).strip())
        text = " ".join(str(value).split()).strip()
        return text or None
