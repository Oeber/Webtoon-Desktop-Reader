import re
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup

from core.app_logging import get_logger
from core.site_session import apply_site_cookies, load_site_user_agent, site_cookie_header
from scrapers.base import ScraperError
from scrapers.discovery_base import BaseDiscoveryProvider
from scrapers.models import CatalogPage, CatalogSeries

logger = get_logger(__name__)


class HiperCoolDiscoveryProvider(BaseDiscoveryProvider):

    site_name = "hiper_cool"

    BASE = "https://hiper.cool"
    CATALOG_PATH = "/manga/"

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Referer": BASE + "/",
    }

    def get_display_name(self) -> str:
        return "HiperCool"

    def get_catalog_page(self, page: int = 1, search_query: str = "") -> CatalogPage:
        page = max(1, int(page))
        search_query = " ".join(str(search_query or "").split()).strip()
        logger.info("HiperCool discovery: fetching catalog page %d search=%r", page, search_query)

        last_error = ""
        for url in self._candidate_urls(page, search_query):
            session = requests.Session()
            apply_site_cookies(session, self.site_name)
            try:
                response = session.get(url, headers=self._request_headers(), timeout=30)
            except Exception as e:
                logger.warning("HiperCool discovery: fetch failed for %s: %s", url, e)
                last_error = str(e)
                continue
            finally:
                try:
                    session.close()
                except Exception:
                    pass

            if response.status_code != 200:
                if self._looks_like_cloudflare_block(response.text, response.status_code):
                    raise ScraperError("HiperCool blocked the catalog request with Cloudflare.")
                logger.warning(
                    "HiperCool discovery: route %s returned %d",
                    url,
                    response.status_code,
                )
                last_error = f"HTTP {response.status_code}"
                continue

            if self._looks_like_cloudflare_block(response.text, response.status_code):
                raise ScraperError("HiperCool blocked the catalog request with Cloudflare.")

            result = self._catalog_page_from_html(page, response.text, source_url=url)
            if result.entries or self._page_has_navigation(response.text, page):
                return result

        if search_query:
            raise ScraperError(f"Failed to load HiperCool search results for '{search_query}'")
        detail = f": {last_error}" if last_error else ""
        raise ScraperError(f"Failed to load HiperCool catalog page {page}{detail}")

    def _candidate_urls(self, page: int, search_query: str) -> list[str]:
        candidates = []
        if search_query:
            encoded_query = quote_plus(search_query)
            if page <= 1:
                candidates.append(f"{self.BASE}/?s={encoded_query}&post_type=wp-manga")
                candidates.append(f"{self.BASE}{self.CATALOG_PATH}?s={encoded_query}&post_type=wp-manga")
            else:
                candidates.append(f"{self.BASE}/page/{page}/?s={encoded_query}&post_type=wp-manga")
                candidates.append(f"{self.BASE}{self.CATALOG_PATH}page/{page}/?s={encoded_query}&post_type=wp-manga")
            return candidates

        if page <= 1:
            candidates.extend(
                [
                    f"{self.BASE}{self.CATALOG_PATH}",
                    f"{self.BASE}{self.CATALOG_PATH}?m_orderby=latest",
                ]
            )
        else:
            candidates.extend(
                [
                    f"{self.BASE}{self.CATALOG_PATH}page/{page}/",
                    f"{self.BASE}{self.CATALOG_PATH}page/{page}/?m_orderby=latest",
                ]
            )
        return candidates

    def _catalog_page_from_html(self, page: int, html: str, *, source_url: str) -> CatalogPage:
        soup = BeautifulSoup(html, "html.parser")
        entries = []
        seen_urls = set()

        for link in soup.select("a[href]"):
            entry = self._entry_from_link(link, seen_urls)
            if entry is not None:
                entries.append(entry)

        logger.info(
            "HiperCool discovery: scraped %d entries from %s",
            len(entries),
            source_url,
        )
        return CatalogPage(
            site=self.site_name,
            page=page,
            entries=entries,
            has_next_page=self._soup_has_next_page(soup, page),
        )

    def _entry_from_link(self, link, seen_urls: set[str]) -> CatalogSeries | None:
        href = urljoin(self.BASE, str(link.get("href") or "").strip()).rstrip("/")
        if not href or "/manga/" not in href or "/capitulo-" in href.lower():
            return None

        slug = self._extract_series_slug(href)
        if not slug or href in seen_urls:
            return None

        title = self._extract_title(link, slug)
        if not title:
            return None

        seen_urls.add(href)
        container = self._entry_container(link)
        latest_chapter = self._extract_latest_chapter(container)
        total_chapters = self._extract_total_chapters(container)
        if total_chapters is None:
            total_chapters = self._chapter_number_from_text(latest_chapter)

        return CatalogSeries(
            site=self.site_name,
            series_id=slug,
            title=title,
            url=href + "/",
            cover_url=self._extract_cover_url(container or link),
            cover_headers=self._request_headers(),
            author=self._extract_author(container),
            description=self._extract_description(container),
            latest_chapter=latest_chapter,
            total_chapters=total_chapters,
        )

    def _entry_container(self, link):
        current = link
        for _ in range(6):
            if current is None:
                break
            classes = current.get("class") or []
            class_text = " ".join(classes)
            if any(
                marker in class_text
                for marker in (
                    "page-item-detail",
                    "item-summary",
                    "c-tabs-item__content",
                    "row",
                )
            ):
                return current
            current = current.parent
        return link.parent

    def _extract_title(self, link, slug: str) -> str:
        for node in (
            link.select_one(".item-summary a"),
            link.select_one(".manga-title a"),
            link.select_one(".post-title"),
            link.select_one("h3"),
            link.select_one("h5"),
            link,
        ):
            if node is None:
                continue
            text = " ".join(node.get_text(" ", strip=True).split()).strip()
            if text:
                return text

        image = link.find("img")
        if image is not None:
            alt = " ".join(str(image.get("alt") or "").split()).strip()
            if alt:
                return alt

        return slug.replace("-", " ").title()

    def _extract_cover_url(self, node) -> str | None:
        if node is None:
            return None
        image = node.find("img")
        if image is None:
            return None
        for attr in ("data-src", "data-lazy-src", "src"):
            value = self._normalize_asset_url(str(image.get(attr) or "").strip())
            if value:
                return value
        return None

    def _extract_author(self, node) -> str | None:
        if node is None:
            return None
        for selector in (".author-content", ".mg_author", ".author"):
            author = node.select_one(selector)
            if author is None:
                continue
            text = " ".join(author.get_text(" ", strip=True).split()).strip()
            if text:
                return text
        return None

    def _extract_description(self, node) -> str | None:
        if node is None:
            return None
        for selector in (".post-content_item", ".summary__content", ".tab-summary .summary_content"):
            description = node.select_one(selector)
            if description is None:
                continue
            text = " ".join(description.get_text(" ", strip=True).split()).strip()
            if text and len(text) > 20:
                return text[:217].rstrip() + "..." if len(text) > 220 else text
        return None

    def _extract_latest_chapter(self, node) -> str | None:
        if node is None:
            return None
        text = " ".join(node.get_text(" ", strip=True).split())
        match = re.search(r"(cap\S*tulo\s+\d+(?:\.\d+)?)", text, re.IGNORECASE)
        if match:
            return match.group(1)
        match = re.search(r"(chapter\s+\d+(?:\.\d+)?)", text, re.IGNORECASE)
        if match:
            return match.group(1)
        return None

    def _extract_total_chapters(self, node) -> int | None:
        if node is None:
            return None
        text = " ".join(node.get_text(" ", strip=True).split())
        match = re.search(r"(\d+)\s+cap\S*tulos\b", text, re.IGNORECASE)
        if match is None:
            match = re.search(r"(\d+)\s+chapters?\b", text, re.IGNORECASE)
        if match is None:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    def _chapter_number_from_text(self, value: str | None) -> int | None:
        text = str(value or "")
        match = re.search(r"(\d+(?:\.\d+)?)", text)
        if match is None:
            return None
        try:
            return int(float(match.group(1)))
        except ValueError:
            return None

    def _extract_series_slug(self, url: str) -> str | None:
        normalized = url.rstrip("/")
        marker = "/manga/"
        index = normalized.lower().find(marker)
        if index == -1:
            return None
        slug = normalized[index + len(marker):]
        if "/" in slug:
            slug = slug.split("/", 1)[0]
        slug = slug.strip()
        return slug or None

    def _request_headers(self) -> dict[str, str]:
        headers = dict(self.HEADERS)
        headers["User-Agent"] = load_site_user_agent(self.site_name, headers["User-Agent"])
        cookie_header = site_cookie_header(self.site_name)
        if cookie_header:
            headers["Cookie"] = cookie_header
        return headers

    def _normalize_asset_url(self, raw: str) -> str:
        value = raw.strip()
        if not value:
            return ""
        if value.startswith("//"):
            return "https:" + value
        if value.startswith("/"):
            return urljoin(self.BASE, value)
        return value if value.startswith("http") else ""

    def _soup_has_next_page(self, soup: BeautifulSoup, page: int) -> bool:
        return bool(
            soup.find("a", href=re.compile(rf"/page/{page + 1}/"))
            or soup.find("a", class_=re.compile(r"\bnext\b", re.IGNORECASE))
        )

    def _page_has_navigation(self, html: str, page: int) -> bool:
        return bool(re.search(rf"/page/{page + 1}/", html))

    def _looks_like_cloudflare_block(self, html: str, status_code: int) -> bool:
        if status_code == 403:
            return True
        text = (html or "").casefold()
        return "just a moment" in text and "cloudflare" in text
