import re
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from core.app_logging import get_logger
from core.site_session import apply_site_cookies, load_site_user_agent, site_cookie_header
from scrapers.base import ScraperError
from scrapers.discovery_base import BaseDiscoveryProvider
from scrapers.models import CatalogPage, CatalogSeries

logger = get_logger(__name__)


class ToonGodDiscoveryProvider(BaseDiscoveryProvider):
    site_name = "toongod"

    BASE = "https://www.toongod.org"
    CATALOG_PATH = "/webtoon/"
    MANHWA_PATH = "/webtoon-genre/manhwa/"

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Referer": BASE + "/",
    }

    _CATALOG_ENTRY_SELECTORS = (
        ".page-item-detail",
        ".c-tabs-item__content",
        ".item-summary",
    )

    def get_display_name(self) -> str:
        return "ToonGod"

    def get_catalog_page(self, page: int = 1, search_query: str = "") -> CatalogPage:
        page = max(1, int(page))
        search_query = " ".join(str(search_query or "").split()).strip()
        logger.info("ToonGod discovery: fetching catalog page %d search=%r", page, search_query)

        last_error = ""
        session = requests.Session()
        apply_site_cookies(session, self.site_name)

        try:
            for url in self._candidate_urls(page, search_query):
                try:
                    response = session.get(url, headers=self._request_headers(), timeout=30)
                except Exception as e:
                    logger.warning("ToonGod discovery: fetch failed for %s: %s", url, e)
                    last_error = str(e)
                    continue

                if response.status_code != 200:
                    if self._looks_like_cloudflare_block(response.text, response.status_code):
                        raise ScraperError("ToonGod blocked the catalog request with Cloudflare.")
                    logger.warning(
                        "ToonGod discovery: route %s returned %d",
                        url,
                        response.status_code,
                    )
                    last_error = f"HTTP {response.status_code}"
                    continue

                if self._looks_like_cloudflare_block(response.text, response.status_code):
                    raise ScraperError("ToonGod blocked the catalog request with Cloudflare.")

                result = self._catalog_page_from_html(page, response.text, source_url=url)
                if result.entries or self._page_has_navigation(response.text, page):
                    return result
        finally:
            try:
                session.close()
            except Exception:
                pass

        if search_query:
            raise ScraperError(f"Failed to load ToonGod search results for '{search_query}'")
        detail = f": {last_error}" if last_error else ""
        raise ScraperError(f"Failed to load ToonGod catalog page {page}{detail}")

    def _candidate_urls(self, page: int, search_query: str) -> list[str]:
        candidates = []

        if search_query:
            encoded_query = quote_plus(search_query)
            if page <= 1:
                candidates.extend(
                    [
                        f"{self.BASE}{self.MANHWA_PATH}?s={encoded_query}&post_type=wp-manga",
                        f"{self.BASE}/?s={encoded_query}&post_type=wp-manga",
                        f"{self.BASE}{self.CATALOG_PATH}?s={encoded_query}&post_type=wp-manga",
                    ]
                )
            else:
                candidates.extend(
                    [
                        f"{self.BASE}{self.MANHWA_PATH}page/{page}/?s={encoded_query}&post_type=wp-manga",
                        f"{self.BASE}/page/{page}/?s={encoded_query}&post_type=wp-manga",
                        f"{self.BASE}{self.CATALOG_PATH}page/{page}/?s={encoded_query}&post_type=wp-manga",
                    ]
                )
            return candidates

        if page <= 1:
            candidates.extend(
                [
                    f"{self.BASE}{self.CATALOG_PATH}",
                    f"{self.BASE}{self.CATALOG_PATH}?m_orderby=latest",
                    f"{self.BASE}{self.MANHWA_PATH}",
                    f"{self.BASE}{self.MANHWA_PATH}?m_orderby=latest",
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

        for node in self._catalog_entry_nodes(soup):
            entry = self._entry_from_node(node, seen_urls)
            if entry is not None:
                entries.append(entry)

        logger.info(
            "ToonGod discovery: scraped %d entries from %s",
            len(entries),
            source_url,
        )

        return CatalogPage(
            site=self.site_name,
            page=page,
            entries=entries,
            has_next_page=self._soup_has_next_page(soup, page),
        )

    def _catalog_entry_nodes(self, soup: BeautifulSoup) -> list:
        nodes = []
        seen_ids = set()

        for selector in self._CATALOG_ENTRY_SELECTORS:
            for node in soup.select(selector):
                node_id = id(node)
                if node_id in seen_ids:
                    continue
                seen_ids.add(node_id)
                nodes.append(node)

        if nodes:
            return nodes

        return list(soup.select("a[href*='/webtoon/']"))

    def _entry_from_node(self, node, seen_urls: set[str]) -> CatalogSeries | None:
        link = self._entry_link(node)
        if link is None:
            return None

        href = urljoin(self.BASE, str(link.get("href") or "").strip()).rstrip("/")
        if not href or "/webtoon/" not in href:
            return None

        href_lower = href.lower()
        if any(part in href_lower for part in ("/chapter-", "/chapter/", "/episode-", "/episode/")):
            return None

        slug = self._extract_series_slug(href)
        if not slug or href in seen_urls:
            return None

        container = self._entry_container(node, link)
        title = self._extract_title(container, link, slug)
        if not title:
            return None

        seen_urls.add(href)

        latest_chapter = self._extract_latest_chapter(container)
        total_chapters = self._chapter_number_from_text(latest_chapter)

        return CatalogSeries(
            site=self.site_name,
            series_id=slug,
            title=title,
            url=href + "/",
            cover_url=self._extract_cover_url(container or link),
            cover_headers=self._request_headers(),
            author=None,
            description=self._extract_description(container),
            latest_chapter=latest_chapter,
            total_chapters=total_chapters,
        )

    def _entry_link(self, node):
        if getattr(node, "name", None) == "a" and node.get("href"):
            return node

        for selector in (
            ".item-summary a[href]",
            ".manga-title a[href]",
            ".post-title a[href]",
            "h3 a[href]",
            "h5 a[href]",
            "a[href]",
        ):
            link = node.select_one(selector)
            if link is not None:
                return link

        return None

    def _entry_container(self, node, link):
        current = node
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

        return node if getattr(node, "get_text", None) is not None else link.parent

    def _extract_title(self, container, link, slug: str) -> str:
        for candidate in (
            getattr(container, "select_one", lambda *_: None)(".item-summary a"),
            getattr(container, "select_one", lambda *_: None)(".manga-title a"),
            getattr(container, "select_one", lambda *_: None)(".post-title"),
            getattr(container, "select_one", lambda *_: None)("h3"),
            getattr(container, "select_one", lambda *_: None)("h5"),
            link,
        ):
            if candidate is None:
                continue
            text = " ".join(candidate.get_text(" ", strip=True).split()).strip()
            if text:
                return text

        return slug.replace("-", " ").replace("_", " ").title()

    def _extract_cover_url(self, node) -> str | None:
        if node is None:
            return None

        for selector in ("img", ".thumb img", ".summary_image img", ".post-thumb img"):
            img = node.select_one(selector) if getattr(node, "select_one", None) else None
            if img is None:
                continue
            for attr in ("data-src", "data-lazy-src", "data-lazy", "data-original", "src"):
                raw = str(img.get(attr) or "").strip()
                value = self._normalize_asset_url(raw)
                if value:
                    return value

        return None

    def _clean_author_text(self, value: str | None) -> str | None:
        text = " ".join(str(value or "").split()).strip()
        if not text:
            return None
        text = re.sub(r"^(?:author|artist)\s*\(?s\)?\s*[:\-]?\s*", "", text, flags=re.IGNORECASE).strip()
        if not text:
            return None
        normalized = text.casefold().strip("()[]{}:;,- ")
        if not normalized or normalized in {"s", "author", "authors", "artist", "artists"}:
            return None
        return text if len(text) < 120 else None

    def _extract_author(self, node) -> str | None:
        if node is None:
            return None

        for item in node.select(".post-content_item") if getattr(node, "select", None) else []:
            label = " ".join(part.get_text(" ", strip=True) for part in item.select(".summary-heading, h5, h4")).strip()
            if "author" not in label.casefold() and "artist" not in label.casefold():
                continue
            value_node = item.select_one(".summary-content, .author-content, .mg_author")
            cleaned = self._clean_author_text(value_node.get_text(" ", strip=True) if value_node is not None else item.get_text(" ", strip=True))
            if cleaned:
                return cleaned

        for selector in (
            ".author-content a",
            ".mg_author a",
            ".author-content",
            ".mg_author",
        ):
            candidate = node.select_one(selector) if getattr(node, "select_one", None) else None
            if candidate is None:
                continue
            cleaned = self._clean_author_text(candidate.get_text(" ", strip=True))
            if cleaned:
                return cleaned

        text = " ".join(node.get_text(" ", strip=True).split())
        match = re.search(r"Author(?:s|\(s\))?\s*[:\-]?\s*(.+?)(?:\s{2,}|$)", text, re.IGNORECASE)
        if match:
            return self._clean_author_text(match.group(1))

        return None

    def _extract_description(self, node) -> str | None:
        if node is None:
            return None

        for selector in (
            ".summary__content",
            ".description-summary",
            ".series-synops",
            ".post-content_item .summary__content",
            ".content",
        ):
            candidate = node.select_one(selector) if getattr(node, "select_one", None) else None
            if candidate is None:
                continue
            text = " ".join(candidate.get_text(" ", strip=True).split()).strip()
            if text and len(text) > 20:
                return text

        return None

    def _extract_latest_chapter(self, node) -> str | None:
        if node is None:
            return None

        selectors = (
            ".chapter-item .chapter a",
            ".chapter-item a",
            ".latest-chap",
            ".list-chapter .chapter a",
            ".chapter a",
        )

        for selector in selectors:
            candidate = node.select_one(selector) if getattr(node, "select_one", None) else None
            if candidate is None:
                continue
            text = " ".join(candidate.get_text(" ", strip=True).split()).strip()
            if text:
                return text

        text = " ".join(node.get_text(" ", strip=True).split())
        match = re.search(r"(Chapter|Episode)\s+\d+(?:\.\d+)?", text, re.IGNORECASE)
        if match:
            return match.group(0).strip()

        return None

    def _extract_total_chapters(self, node) -> int | None:
        if node is None:
            return None

        text = " ".join(node.get_text(" ", strip=True).split())
        match = re.search(r"(\d+)\s+chapters?\b", text, re.IGNORECASE)
        if match is None:
            match = re.search(r"(\d+)\s+episodes?\b", text, re.IGNORECASE)
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
        marker = "/webtoon/"
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