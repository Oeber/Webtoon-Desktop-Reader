import re
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

from core.app_logging import get_logger
from core.site_session import load_site_cookies, load_site_user_agent, site_cookie_header
from scrapers.base import ScraperError
from scrapers.discovery_base import BaseDiscoveryProvider
from scrapers.models import CatalogPage, CatalogSeries

logger = get_logger(__name__)


class MvlempyrDiscoveryProvider(BaseDiscoveryProvider):
    site_name = "mvlempyr"
    site_display_name = "MVLEMPYR"
    site_hosts = ("mvlempyr.io", "www.mvlempyr.io")
    site_base_url = "https://www.mvlempyr.io/"
    site_required_cookie_names = ("cf_clearance",)
    site_session_cookie_names = (
        "cf_clearance",
        "__cf_bm",
        "_cfuvid",
    )

    BASE = "https://www.mvlempyr.io"
    IMPERSONATE = "chrome120"
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Referer": BASE + "/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def __init__(self):
        self._http = cffi_requests.Session()
        self._section_label_cache: dict[int, str] = {}
        self._cover_bytes_cache: dict[str, bytes | None] = {}

    def get_display_name(self) -> str:
        return "MVLEMPYR"

    def get_catalog_page(self, page: int = 1, search_query: str = "") -> CatalogPage:
        page = max(1, int(page))
        query = " ".join(str(search_query or "").split()).strip()
        logger.info("MVLEMPYR discovery: fetching page=%d search=%r", page, query)

        last_error = ""
        blocked_error = ""
        fetch_succeeded = False
        best_page = CatalogPage(site=self.site_name, page=page, entries=[], has_next_page=False)
        best_score = (-1, -1, -1)

        for url in self._candidate_urls(page, query):
            html = ""
            used_browser = False
            try:
                response = self._get(url)
                html = str(response.text or "")
            except ScraperError as exc:
                last_error = str(exc)
                if self._looks_like_access_error(last_error):
                    blocked_error = last_error
                logger.warning("MVLEMPYR discovery: fetch failed for %s: %s", url, exc)
                browser_fetcher = getattr(self, "browser_fetcher", None)
                if browser_fetcher is None:
                    continue
                try:
                    logger.info("MVLEMPYR discovery: attempting browser-backed catalog fetch for %s", url)
                    html = str(browser_fetcher.fetch_html(url, self.site_name, timeout_ms=30000) or "")
                    used_browser = True
                except Exception as browser_exc:
                    last_error = str(browser_exc)
                    logger.warning("MVLEMPYR discovery: browser-backed fetch failed for %s: %s", url, browser_exc)
                    continue

            if not html.strip():
                continue
            fetch_succeeded = True
            page_result = self._catalog_page_from_html(page, html, source_url=url, search_query=query)

            score = (len(page_result.entries), int(page_result.has_next_page), int(used_browser))
            if score > best_score:
                best_page = page_result
                best_score = score

            if page_result.entries or page_result.has_next_page:
                return page_result

        if best_page.entries or fetch_succeeded:
            return best_page

        if blocked_error:
            raise ScraperError(blocked_error)
        if query:
            detail = f": {last_error}" if last_error else ""
            raise ScraperError(f"Failed to load MVLEMPYR search results for '{query}'{detail}")
        detail = f": {last_error}" if last_error else ""
        raise ScraperError(f"Failed to load MVLEMPYR catalog page {page}{detail}")

    def fetch_cover(self, url: str, headers: dict[str, str]) -> bytes | None:
        normalized = self._normalize_cover_fetch_url(url)
        if not normalized:
            return None

        cached = self._cover_bytes_cache.get(normalized)
        if cached is not None:
            return cached

        try:
            parsed = urlparse(normalized)
            host = (parsed.netloc or "").casefold()
            path = (parsed.path or "").casefold()

            is_same_site = any(site_host in host for site_host in self.site_hosts)
            looks_static = (
                path.endswith((".jpg", ".jpeg", ".png", ".webp", ".avif"))
                or "/images/" in path
                or "/covers/" in path
                or "/uploads/" in path
            )

            request_headers = {
                "User-Agent": load_site_user_agent(self.site_name, self.HEADERS["User-Agent"]),
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                "Referer": self.BASE + "/",
            }

            kwargs = {
                "url": normalized,
                "headers": request_headers,
                "timeout": 8,
            }

            if is_same_site and not looks_static:
                kwargs["cookies"] = self._site_cookies()
                kwargs["impersonate"] = self.IMPERSONATE

            response = self._http.get(**kwargs)
            if response.status_code == 200 and response.content:
                self._cover_bytes_cache[normalized] = response.content
                return response.content
        except Exception:
            pass

        self._cover_bytes_cache[normalized] = None
        return None
    
    def _normalize_cover_fetch_url(self, url: str) -> str:
        value = self._normalize_url(str(url or "").strip())
        if not value:
            return ""

        parsed = urlparse(value)

        # unwrap Next.js proxy URLs like /_next/image?url=...
        if "/_next/image" in (parsed.path or ""):
            query = parse_qs(parsed.query)
            wrapped = query.get("url", [])
            if wrapped:
                inner = unquote(str(wrapped[0] or "").strip())
                inner_url = self._normalize_url(inner)
                if inner_url:
                    return inner_url

        return value


    def _looks_like_access_error(self, message: str) -> bool:
        text = " ".join(str(message or "").casefold().split())
        return "cloudflare" in text or "anti-bot" in text or "blocked" in text


    def _candidate_urls(self, page: int, query: str) -> list[str]:
        if query:
            encoded = quote_plus(query)
            candidates = [
                f"{self.BASE}/search?q={encoded}",
                f"{self.BASE}/search?query={encoded}",
                f"{self.BASE}/advanced-search?keyword={encoded}",
                f"{self.BASE}/advanced-search?query={encoded}",
                f"{self.BASE}/novels?search={encoded}",
                f"{self.BASE}/novels?q={encoded}",
                f"{self.BASE}/?s={encoded}",
            ]
            if page > 1:
                with_page = []
                for url in candidates:
                    joiner = "&" if "?" in url else "?"
                    with_page.append(f"{url}{joiner}page={page}")
                candidates = with_page + candidates
            return candidates

        if page <= 1:
            return [
                f"{self.BASE}/",
                f"{self.BASE}/novels?sort=new&page=1",
                f"{self.BASE}/novels?page=1",
            ]

        return [
            f"{self.BASE}/?page={page}",
            f"{self.BASE}/novels?sort=new&page={page}",
            f"{self.BASE}/novels?page={page}",
        ]

    def _catalog_page_from_html(self, page: int, html: str, *, source_url: str, search_query: str) -> CatalogPage:
        soup = BeautifulSoup(html, "html.parser")
        entries = []
        seen_urls: set[str] = set()
        source_low = str(source_url or "").casefold()
        self._section_label_cache.clear()

        for node in soup.select('a[href*="/novel/"]'):
            entry = self._entry_from_link(node, seen_urls, source_url=source_url)
            if entry is None:
                continue
            if search_query and not entry.matches_query(search_query):
                continue
            if not search_query and self._should_skip_for_source(node, source_low):
                continue
            entries.append(entry)

        logger.info(
            "MVLEMPYR discovery: scraped %d entries from %s",
            len(entries),
            source_url,
        )

        return CatalogPage(
            site=self.site_name,
            page=page,
            entries=entries,
            has_next_page=self._has_next_page(soup, html, page),
        )

    def _entry_from_link(self, link, seen_urls: set[str], *, source_url: str = "") -> CatalogSeries | None:
        href = self._normalize_url(str(link.get("href") or "").strip()).rstrip("/")
        if not href or "/novel/" not in href:
            return None
        if "/chapter/" in href:
            return None

        slug = self._extract_series_slug(href)
        if not slug or href in seen_urls:
            return None

        container = self._entry_container(link)
        title = self._extract_title(container, link, slug)
        if not title:
            return None

        seen_urls.add(href)
        full_text = self._compact_text((container or link).get_text(" ", strip=True))
        latest_chapter = self._extract_latest_chapter(full_text)
        total_chapters = self._extract_total_chapters(full_text)
        author = self._extract_author(container, full_text)
        description = self._extract_description(container, full_text, title)
        cover_url = self._extract_cover_url(container or link, slug)


        return CatalogSeries(
            site=self.site_name,
            series_id=slug,
            title=title,
            url=href,
            cover_url=cover_url,
            cover_headers=self._cover_headers(),
            author=author,
            description=description,
            latest_chapter=latest_chapter,
            total_chapters=total_chapters,
        )

    def _should_skip_for_source(self, link, source_low: str) -> bool:
        section_label = self._section_label(link)
        skip_markers = (
            "trending",
            "popular",
            "ranking",
            "rankings",
            "random",
            "weekly",
            "monthly",
            "all time",
        )
        if section_label and any(marker in section_label for marker in skip_markers):
            return True
        if "/rankings" in source_low:
            return True
        return False

    def _section_label(self, link) -> str:
        current = link
        for _ in range(6):
            current = getattr(current, "parent", None)
            if current is None:
                break

            node_id = id(current)
            cached = self._section_label_cache.get(node_id)
            if cached is not None:
                return cached

            heading = current.find_previous(["h1", "h2", "h3", "h4", "h5", "h6"])
            value = ""
            if heading is not None:
                value = self._compact_text(heading.get_text(" ", strip=True)).casefold()
                if value and len(value) <= 80:
                    self._section_label_cache[node_id] = value
                    return value

            self._section_label_cache[node_id] = ""
        return ""


    def _entry_container(self, link):
        card_classes = re.compile(
            r"(?:^|[\s_-])(card|item|novel|book|entry|product|result|tile|row)(?:[\s_-]|$)",
            re.IGNORECASE,
        )
        current = link
        for _ in range(8):
            current = getattr(current, "parent", None)
            if current is None:
                break

            classes = " ".join(str(value) for value in (current.get("class") or []))
            if classes and card_classes.search(classes):
                return current

            novel_links = current.find_all("a", href=re.compile(r"/novel/"), limit=4)
            if len(novel_links) > 3:
                break

            if current.find("a", href=re.compile(r"/novel/")) is not None:
                return current

        return link

    def _extract_title(self, container, link, slug: str) -> str:
        return self._slug_title(slug)

    def _clean_title(self, raw: str, slug: str) -> str:
        text = self._compact_text(raw)
        if not text:
            return ""
        text = re.sub(r"\b(?:ongoing|completed|hiatus|dropped)\b", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\b\d+(?:\.\d+)?\s*/\s*5(?:\.0)?\b", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\bchapter\s*:\s*\d+\b", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\bchapters?\s*:\s*\d+\b", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\bread\b|\bexplore now\b|\bbookmark(?:ed)?\b|\bstart\b|\bcontinue\b", "", text, flags=re.IGNORECASE)
        text = self._dedupe_repeated_halves(text)
        text = " ".join(text.split()).strip(" |-")
        if not text:
            return ""
        if text.casefold() == "mvlempyr":
            return ""
        if len(text) < 3:
            return ""
        return text if len(text) <= 180 else slug.replace("-", " ").title()

    def _dedupe_repeated_halves(self, text: str) -> str:
        compact = " ".join(text.split()).strip()
        parts = compact.split()
        half = len(parts) // 2
        if half and len(parts) % 2 == 0:
            left = " ".join(parts[:half]).casefold()
            right = " ".join(parts[half:]).casefold()
            if left == right:
                return " ".join(parts[:half])
        return compact


    def _slug_title(self, slug: str) -> str:
        text = str(slug or "").replace("-", " ").replace("_", " ").strip()
        text = re.sub(r"\s+", " ", text)
        return text.title()

    def _looks_like_section_title(self, title: str, slug_title: str) -> bool:
        normalized = self._compact_text(title).casefold()
        if not normalized:
            return True
        if normalized == self._compact_text(slug_title).casefold():
            return False
        section_titles = {
            "new arrivals",
            "trending",
            "popular",
            "rankings",
            "ranking",
            "weekly",
            "monthly",
            "all time",
            "latest updates",
            "new updates",
            "completed",
            "ongoing",
            "hot",
        }
        if normalized in section_titles:
            return True
        if len(normalized.split()) <= 3 and any(marker in normalized for marker in section_titles):
            return True
        return False


    def _title_matches_slug(self, title: str, slug_title: str, slug: str) -> bool:
        title_tokens = self._meaningful_tokens(title)
        slug_tokens = self._meaningful_tokens(slug_title or slug)
        if not title_tokens or not slug_tokens:
            return True
        overlap = title_tokens & slug_tokens
        if overlap:
            return True
        title_compact = self._compact_text(title).casefold().replace("'", "")
        slug_compact = self._compact_text(slug_title).casefold().replace("'", "")
        if title_compact == slug_compact:
            return True
        return False

    def _meaningful_tokens(self, value: str) -> set[str]:
        tokens = set(re.findall(r"[a-z0-9]+", str(value or "").casefold()))
        stop_words = {
            "a", "an", "the", "of", "in", "on", "to", "by", "for", "and", "or",
            "my", "i", "is", "are", "with", "from", "at", "as", "into", "be", "can",
        }
        return {token for token in tokens if len(token) >= 3 and token not in stop_words}

    def _extract_cover_url(self, node, slug: str) -> str | None:
        if node is None:
            return None

        slug_title = self._slug_title(slug)
        slug_tokens = self._meaningful_tokens(slug_title)
        best: tuple[int, str] | None = None

        for image in node.find_all("img", limit=8):
            meta_text = " ".join(
                str(image.get(name) or "").strip()
                for name in ("alt", "title", "aria-label")
            )
            meta_tokens = self._meaningful_tokens(meta_text)

            for attr in ("data-src", "data-lazy-src", "data-lazy", "src"):
                raw = str(image.get(attr) or "").strip()
                value = self._normalize_cover_fetch_url(raw)
                if not value:
                    continue

                low = value.casefold()
                if any(marker in low for marker in ("ratingstar", "fire", "logo", "banner", "icon", "avatar")):
                    continue

                if not (low.endswith((".jpg", ".jpeg", ".png", ".webp", ".avif")) or "/_next/image" in low or "/images/" in low):
                    continue

                if meta_tokens and slug_tokens and not (meta_tokens & slug_tokens):
                    continue

                score = 0
                if meta_tokens and slug_tokens:
                    score += 200
                if "/images/" in low:
                    score += 120
                if low.endswith(".webp"):
                    score += 40
                if "/_next/image" in low:
                    score -= 80
                if "placeholder" in low or "default" in low:
                    score -= 200

                if best is None or score > best[0]:
                    best = (score, value)

        return best[1] if best else None

    def _extract_author(self, container, text: str) -> str | None:
        if container is not None:
            for selector in ('a[href*="/author/"]', '[data-testid="author-name"]', ".author"):
                node = container.select_one(selector)
                if node is None:
                    continue
                value = self._compact_text(node.get_text(" ", strip=True))
                if value and value.casefold() != "mvlempyr":
                    return value
        match = re.search(r"Author:\s*(.+?)(?:\s{2,}|$)", text, re.IGNORECASE)
        if match:
            return self._compact_text(match.group(1))
        return None

    def _extract_description(self, container, text: str, title: str) -> str | None:
        if container is not None:
            for selector in (
                ".summary",
                ".description",
                ".synopsis",
                "p",
            ):
                for node in container.select(selector):
                    value = self._compact_text(node.get_text(" ", strip=True))
                    if value and len(value) >= 40 and value.casefold() != title.casefold():
                        return value[:217].rstrip() + "..." if len(value) > 220 else value

        cleaned = text
        if title:
            cleaned = re.sub(re.escape(title), "", cleaned, flags=re.IGNORECASE)
        match = re.search(r"(?:Synopsis\s*:?\s*)?([A-Z][^.]{40,}?(?:\.|...))", cleaned)
        if match:
            value = self._compact_text(match.group(1))
            if value:
                return value[:217].rstrip() + "..." if len(value) > 220 else value
        return None

    def _extract_latest_chapter(self, text: str) -> str | None:
        match = re.search(r"(Chapter\s*:\s*\d+(?:\.\d+)?)", text, re.IGNORECASE)
        if match:
            return self._compact_text(match.group(1))
        match = re.search(r"(Chapter\s+\d+(?:\.\d+)?)", text, re.IGNORECASE)
        if match:
            return self._compact_text(match.group(1))
        return None

    def _extract_total_chapters(self, text: str) -> int | None:
        patterns = (
            r"\bChapters?\s*:\s*(\d{1,4})\b",
            r"\b(\d{1,4})\s+chapters?\b",
        )
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                continue
            try:
                value = int(match.group(1))
            except ValueError:
                continue
            if 0 < value <= 1500 and not (1900 <= value <= 2100):
                return value
        return None

    def _extract_series_slug(self, url: str) -> str | None:
        path = urlparse(url).path.strip("/")
        marker = "novel/"
        if marker not in path:
            return None
        slug = path.split(marker, 1)[-1].split("/", 1)[0].strip()
        return slug or None

    def _request_headers(self, url: str | None = None) -> dict[str, str]:
        headers = dict(self.HEADERS)
        headers["User-Agent"] = load_site_user_agent(self.site_name, headers["User-Agent"])
        if url:
            headers["Referer"] = self.BASE + "/"
        return headers

    def _cover_headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": load_site_user_agent(self.site_name, self.HEADERS["User-Agent"]),
            "Referer": self.BASE + "/",
        }
        cookie = site_cookie_header(self.site_name)
        if cookie:
            headers["Cookie"] = cookie
        return headers

    def _site_cookies(self) -> dict[str, str]:
        return {c["name"]: c["value"] for c in load_site_cookies(self.site_name)}

    def _normalize_url(self, raw: str) -> str:
        value = str(raw or "").strip()
        if not value:
            return ""
        if value.startswith("//"):
            return "https:" + value
        if value.startswith("/"):
            return urljoin(self.BASE, value)
        if value.startswith(("http://", "https://")):
            return value
        return ""

    def _compact_text(self, value: str) -> str:
        return " ".join(str(value or "").split()).strip()

    def _has_next_page(self, soup: BeautifulSoup, html: str, page: int) -> bool:
        for link in soup.select("a[href]"):
            href = str(link.get("href") or "").strip()
            text = self._compact_text(link.get_text(" ", strip=True))
            if re.search(rf"[?&]page={page + 1}\b", href, re.IGNORECASE):
                return True
            if re.search(rf"/page/{page + 1}\b", href, re.IGNORECASE):
                return True
            if text.casefold() in {"next", "next >", ">"}:
                return True
        return bool(re.search(rf"[?&]page={page + 1}\b|/page/{page + 1}\b", html, re.IGNORECASE))

    def _get(self, url: str):
        try:
            response = self._http.get(
                url,
                headers=self._request_headers(url),
                cookies=self._site_cookies(),
                impersonate=self.IMPERSONATE,
                timeout=30,
            )
        except Exception as exc:
            raise ScraperError(f"Request failed for {url}: {exc}") from exc

        if self._looks_like_cloudflare_block(response.text, response.status_code):
            raise ScraperError("MVLEMPYR blocked the catalog request with Cloudflare.")
        if response.status_code != 200:
            raise ScraperError(f"Failed to load catalog page: {url} ({response.status_code})")
        return response

    def _looks_like_cloudflare_block(self, html: str, status_code: int) -> bool:
        if status_code == 403:
            return True
        text = str(html or "").casefold()
        return "just a moment" in text and "cloudflare" in text