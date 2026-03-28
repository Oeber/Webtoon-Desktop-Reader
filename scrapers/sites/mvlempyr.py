import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, urljoin, urlparse

import soupsieve as sv
from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup

from core.app_logging import get_logger
from core.app_paths import data_path
from core.site_session import has_site_cookies, load_site_cookies, load_site_user_agent
from ..base import BaseScraper, ScraperError
from ..models import ChapterContent, ChapterInfo, SeriesInfo

logger = get_logger(__name__)


class MvlempyrScraper(BaseScraper):
    site_name = "mvlempyr"
    site_display_name = "MVLEMPYR"
    content_type = "webnovel"
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

    # Pre-compiled selectors — avoids soupsieve recompiling on every call
    _SEL_CHAPTER_LINKS = sv.compile('a[href*="/chapter/"]')
    _SEL_NOVEL_LINK_1 = sv.compile('a[href*="/novel/"]')
    _SEL_NOVEL_LINK_2 = sv.compile('a[href*="/novels/"]')
    _SEL_STRIP_TAGS = sv.compile("script, style, button, svg, nav, footer, form")
    _SEL_OG_TITLE = sv.compile('meta[property="og:title"]')
    _SEL_OG_IMAGE = sv.compile('meta[property="og:image"]')
    _SEL_TWITTER_IMAGE = sv.compile('meta[name="twitter:image"]')
    _SEL_ALL_IMAGES = sv.compile("img[src], img[data-src]")
    _SEL_AUTHOR_LINK = sv.compile('a[href*="/author/"]')
    _SEL_AUTHOR_TESTID = sv.compile('[data-testid="author-name"]')
    _SEL_DESCRIPTION_SOURCES = [
        sv.compile('[data-testid="novel-synopsis"]'),
        sv.compile(".novel-synopsis"),
        sv.compile(".summary"),
        sv.compile(".description"),
        sv.compile("article"),
    ]
    _SEL_CHAPTER_CONTAINER = [
        sv.compile("article .prose"),
        sv.compile("article"),
        sv.compile("main .prose"),
        sv.compile("main article"),
        sv.compile("main"),
        sv.compile(".chapter-content"),
        sv.compile(".chapter-body"),
        sv.compile(".entry-content"),
        sv.compile(".novel-content"),
        sv.compile(".reading-content"),
        sv.compile("[data-testid='chapter-content']"),
        sv.compile("[class*='chapter'][class*='content']"),
        sv.compile("[class*='novel'][class*='content']"),
        sv.compile("[class*='reader'][class*='content']"),
        sv.compile("[class*='prose']"),
        sv.compile("[class*='break-words']"),
        sv.compile("[id*='chapter']"),
        sv.compile("[id*='content']"),
    ]

    def __init__(self):
        self._http = cffi_requests.Session()
        self._last_request_at = 0.0
        self._series_cache: dict[str, SeriesInfo] = {}
        self._user_agent_cache: str | None = None
        self._cookies_cache: dict[str, str] | None = None

    @classmethod
    def can_handle(cls, url: str) -> bool:
        host = (urlparse(url).netloc or "").lower()
        return host in {"mvlempyr.io", "www.mvlempyr.io"}

    def get_request_headers(self, url):
        headers = dict(self.HEADERS)
        if self._user_agent_cache is None:
            self._user_agent_cache = load_site_user_agent(self.site_name, headers["User-Agent"])
        headers["User-Agent"] = self._user_agent_cache
        return headers

    def is_chapter_url(self, url: str) -> bool:
        return "/chapter/" in str(url or "").lower()

    def series_url_from_chapter_url(self, url: str) -> str:
        response = self._get(url)
        soup = BeautifulSoup(response.text, "lxml")
        for sel in (self._SEL_NOVEL_LINK_1, self._SEL_NOVEL_LINK_2):
            node = sel.select_one(soup)
            href = self._normalize_url(node.get("href", "")) if node is not None else ""
            if "/novel/" in href:
                return href
        raise ScraperError(f"Could not determine MVLEMPYR series URL from chapter: {url}")

    def extract_chapter_number(self, url: str) -> int | None:
        match = re.search(r"/chapter/\d+-(\d+)(?:/)?$", str(url or ""), re.IGNORECASE)
        if match:
            return int(match.group(1))
        match = re.search(r"/chapter/(?:\d+-)?(\d+)(?:/)?$", str(url or ""), re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None

    def get_series_info(self, url: str, session=None) -> SeriesInfo:
        series_url = self._normalize_series_url(url)
        cached = self._series_cache.get(series_url)
        if cached is not None:
            return cached

        response = self._get(series_url, session=session)
        soup = BeautifulSoup(response.text, "lxml")

        title = self._extract_title(soup)
        description = self._extract_description(soup)
        author = self._extract_author(soup)
        cover_url = self._extract_cover(soup)
        total_hint = self._extract_total_chapters_hint(soup, response.text)
        series_id = self._extract_series_id(response.text, series_url, cover_url=cover_url, total_hint=total_hint)
        chapters = self._extract_chapters(soup, response.text, series_url, series_id, total_hint=total_hint)
        if not chapters:
            self._dump_series_debug(series_url, response.text, {})
            raise ScraperError(
                "MVLEMPYR chapter list was not found. If the site is protected, authorize it in the app and retry."
            )

        total_chapters = max(len(chapters), total_hint or 0) or None

        series = SeriesInfo(
            site=self.site_name,
            series_id=series_id,
            title=title,
            url=series_url,
            content_type=self.content_type,
            cover_url=cover_url,
            author=author,
            description=description,
            total_chapters=total_chapters,
            chapters=chapters,
        )
        self._series_cache[series_url] = series
        return series

    def get_chapter_pages(self, chapter_url: str, session=None):
        raise ScraperError("MVLEMPYR is a webnovel source and does not provide image pages.")

    def get_chapter_content(self, chapter_url: str):
        response = self._get(chapter_url)
        return self.parse_chapter_content_html(chapter_url, response.text)

    def parse_chapter_content_html(self, chapter_url: str, html: str) -> ChapterContent:
        title = self._extract_chapter_title_from_html(html, chapter_url)

        direct_html, direct_text = self._extract_direct_chapter_html(html)
        if direct_html or direct_text:
            return ChapterContent(title=title, html=direct_html or None, text=direct_text or None)

        next_data = self._extract_next_data(html)
        if next_data is not None:
            script_title, script_html, script_text = self._extract_chapter_content_from_data(next_data)
            if script_html or script_text:
                return ChapterContent(
                    title=script_title or title,
                    html=script_html or None,
                    text=script_text or None,
                )

        # Parse once and reuse for both container search and title fallback
        soup = BeautifulSoup(html, "lxml")
        container = self._find_chapter_container(soup)
        if container is not None:
            cleaned_html = self._clean_chapter_html(container)
            text = self._normalize_text(container.get_text("\n", strip=True))
            if cleaned_html or text:
                # Refine title from soup if the regex title was weak
                if not title or title == self._chapter_title_from_url(chapter_url):
                    title = self._extract_chapter_title(soup, chapter_url)
                return ChapterContent(title=title, html=cleaned_html, text=text)

        self._dump_failed_chapter_html(chapter_url, html)
        raise ScraperError(f"Could not extract chapter content from {chapter_url}")

    def _extract_chapter_title_from_html(self, html: str, chapter_url: str) -> str:
        patterns = (
            r'<h1[^>]*>(.*?)</h1>',
            r'<h2[^>]*>(.*?)</h2>',
            r"<meta[^>]+property=[\"']og:title[\"'][^>]+content=[\"'](.*?)[\"']",
            r'<title>(.*?)</title>',
        )
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
            if not match:
                continue
            value = BeautifulSoup(match.group(1), "lxml").get_text(" ", strip=True)
            value = re.sub(r"\s*\|\s*MVLEMPYR\s*$", "", value, flags=re.IGNORECASE).strip()
            if value and value.casefold() != "mvlempyr":
                return value
        return self._chapter_title_from_url(chapter_url)

    def _extract_direct_chapter_html(self, html: str) -> tuple[str, str]:
        marker_patterns = (
            r'<article[^>]*>(.*?)</article>',
            r'<main[^>]*>(.*?)</main>',
            r'<div[^>]+class=["\'][^"\']*(?:chapter-content|chapter-body|novel-content|reading-content|prose|break-words)[^"\']*["\'][^>]*>(.*?)</div>',
        )
        for pattern in marker_patterns:
            for match in re.finditer(pattern, html, re.IGNORECASE | re.DOTALL):
                fragment = match.group(1)
                cleaned_html, text = self._normalize_fragment_html(fragment)
                if len(text) >= 80:
                    return cleaned_html, text
        return "", ""

    def _normalize_fragment_html(self, fragment: str) -> tuple[str, str]:
        soup = BeautifulSoup(fragment, "lxml")
        for tag in self._SEL_STRIP_TAGS.select(soup):
            tag.decompose()
        text = self._normalize_text(soup.get_text("\n", strip=True))
        if not text:
            return "", ""
        allowed = {"p", "div", "section", "article", "blockquote", "em", "strong", "i", "b", "span", "br", "h1", "h2", "h3", "h4", "ul", "ol", "li"}
        for tag in soup.find_all(True):
            if tag.name not in allowed:
                tag.unwrap()
                continue
            tag.attrs = {}
        cleaned_html = "".join(str(child) for child in soup.contents).strip()
        return cleaned_html, text

    def _extract_title(self, soup: BeautifulSoup) -> str:
        for selector in ("h1", 'meta[property="og:title"]', "title"):
            node = soup.select_one(selector)
            if node is None:
                continue
            if node.name == "meta":
                value = str(node.get("content") or "").strip()
            else:
                value = node.get_text(" ", strip=True)
            value = re.sub(r"\s*\|\s*MVLEMPYR\s*$", "", value, flags=re.IGNORECASE).strip()
            if value:
                return value
        raise ScraperError("Could not extract MVLEMPYR series title")

    def _extract_description(self, soup: BeautifulSoup) -> str | None:
        for sel in self._SEL_DESCRIPTION_SOURCES:
            node = sel.select_one(soup)
            if node is None:
                continue
            text = node.get_text(" ", strip=True)
            if text and "Synopsis" in text:
                return text.replace("Synopsis", "", 1).strip() or text

        meta = soup.find("meta", attrs={"name": "description"})
        if meta and meta.get("content"):
            text = str(meta["content"]).strip()
            return text or None
        return None

    def _extract_author(self, soup: BeautifulSoup) -> str | None:
        text = soup.get_text("\n", strip=True)
        match = re.search(r"Author:\s*(.+)", text, re.IGNORECASE)
        if match:
            return match.group(1).split("\n")[0].strip() or None
        for sel in (self._SEL_AUTHOR_LINK, self._SEL_AUTHOR_TESTID):
            node = sel.select_one(soup)
            if node is not None:
                value = node.get_text(" ", strip=True)
                if value:
                    return value
        return None

    def _extract_cover(self, soup: BeautifulSoup) -> str | None:
        title = self._extract_title(soup)
        candidates: list[tuple[int, str]] = []

        for sel, attr in (
            (self._SEL_OG_IMAGE, "content"),
            (self._SEL_TWITTER_IMAGE, "content"),
        ):
            node = sel.select_one(soup)
            if node is None:
                continue
            value = self._normalize_url(node.get(attr, ""))
            score = self._cover_score(value, title, "")
            if value and score > 0:
                candidates.append((score + 200, value))

        for image in self._SEL_ALL_IMAGES.select(soup):
            value = self._normalize_url(image.get("data-src") or image.get("src") or "")
            alt_text = " ".join(
                str(image.get(name, "") or "")
                for name in ("alt", "title", "aria-label")
            )
            score = self._cover_score(value, title, alt_text)
            if value and score > 0:
                candidates.append((score, value))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    def _extract_series_id(self, html: str, series_url: str, cover_url: str | None = None, total_hint: int | None = None) -> str:
        cover_match = re.search(r"/images/\d+/(\d+)\.(?:jpg|jpeg|png|webp|avif)\b", str(cover_url or ""), re.IGNORECASE)
        if cover_match:
            return cover_match.group(1)
        if total_hint and total_hint > 1:
            chapter_match = re.search(r"/chapter/(\d+)-1\b", html, re.IGNORECASE)
            if chapter_match:
                return chapter_match.group(1)
        match = re.search(r"\b([0-9a-f]{24})\b", html, re.IGNORECASE)
        if match:
            return match.group(1)
        path = urlparse(series_url).path.strip("/")
        return path.split("/")[-1] if path else "unknown"

    def _extract_chapters(
        self,
        soup: BeautifulSoup,
        html: str,
        series_url: str,
        series_id: str,
        total_hint: int | None = None,
    ) -> list[ChapterInfo]:
        real_candidates: list[list[ChapterInfo]] = []
        debug_payload: dict[str, object] = {"total_hint": total_hint or 0}

        html_chapters = self._extract_chapters_from_links(soup, series_url)
        if html_chapters:
            html_chapters = self._dedupe_chapters(html_chapters)
            debug_payload["html_links"] = len(html_chapters)
            real_candidates.append(html_chapters)

        script_chapters = self._extract_chapters_from_scripts(html, series_url)
        if script_chapters:
            script_chapters = self._dedupe_chapters(script_chapters)
            debug_payload["script_links"] = len(script_chapters)
            real_candidates.append(script_chapters)

        for payload in self._candidate_chapter_payloads(series_id, series_url):
            try:
                data = self._get_json(payload["url"], payload.get("params"))
            except ScraperError:
                continue

            api_chapters = self._extract_chapters_from_json(data, series_url)
            if not api_chapters:
                continue

            api_chapters = self._dedupe_chapters(api_chapters)
            debug_payload[payload["url"]] = len(api_chapters)
            real_candidates.append(api_chapters)

        if real_candidates:
            best = max(real_candidates, key=self._chapter_source_score)
            best = self._dedupe_chapters(best)

            # If we got a reasonable real list, trust it.
            if len(best) >= 2:
                return best

            # If the page exposes a believable total, prefer synthesizing from it
            # instead of paying for chapter-existence probes during lightweight loads.
            if total_hint and total_hint > len(best) and self._can_synthesize_chapters(series_id):
                synthesized = self._synthesize_chapters(series_id, total_hint)
                debug_payload["synthesized_from_total_hint"] = len(synthesized)
                logger.info(
                    "MVLEMPYR synthesized chapter list from total hint series_id=%s total=%s",
                    series_id,
                    len(synthesized),
                )
                return synthesized

            # Only probe/synthesize when the real result is weak.
            if self._can_synthesize_chapters(series_id):
                logger.info("MVLEMPYR chapter list weak for series_id=%s; probing chapter count", series_id)
                discovered_total = self._discover_chapter_count(series_id)
                debug_payload["discovered_total"] = discovered_total
                logger.info("MVLEMPYR probed chapter count series_id=%s total=%s", series_id, discovered_total)

                if discovered_total > len(best):
                    synthesized = self._synthesize_chapters(series_id, discovered_total)
                    logger.info("MVLEMPYR synthesized chapter list series_id=%s total=%s", series_id, len(synthesized))
                    return synthesized

            if len(best) <= 1 and (total_hint or 0) > len(best):
                self._dump_series_debug(series_url, html, debug_payload)
            return best

        # No real sources worked at all: now try synthesis as a last resort.
        if total_hint and total_hint > 1 and self._can_synthesize_chapters(series_id):
            synthesized = self._synthesize_chapters(series_id, total_hint)
            debug_payload["synthesized"] = len(synthesized)
            return synthesized

        if self._can_synthesize_chapters(series_id):
            discovered_total = self._discover_chapter_count(series_id)
            debug_payload["discovered_total"] = discovered_total
            if discovered_total > 0:
                synthesized = self._synthesize_chapters(series_id, discovered_total)
                logger.info("MVLEMPYR synthesized fallback chapter list series_id=%s total=%s", series_id, len(synthesized))
                return synthesized

        self._dump_series_debug(series_url, html, debug_payload)
        return []

    def _extract_chapters_from_links(self, soup: BeautifulSoup, base_url: str) -> list[ChapterInfo]:
        found: dict[str, ChapterInfo] = {}
        for link in self._SEL_CHAPTER_LINKS.select(soup):
            href = self._normalize_url(link.get("href", ""), base_url)
            if "/chapter/" not in href:
                continue
            title = link.get_text(" ", strip=True) or self._chapter_title_from_url(href)
            found[href] = ChapterInfo(
                id=href.rstrip("/").rsplit("/", 1)[-1],
                number=self._chapter_number(title, href),
                title=title,
                url=href,
            )
        return self._sort_chapters(found.values())

    def _extract_chapters_from_scripts(self, html: str, base_url: str) -> list[ChapterInfo]:
        found: dict[str, ChapterInfo] = {}
        for match in re.finditer(r'https://www\\.mvlempyr\\.io/chapter/\\d+-\\d+|/chapter/\\d+-\\d+', html, re.IGNORECASE):
            href = self._normalize_url(match.group(0), base_url)
            found[href] = ChapterInfo(
                id=href.rstrip("/").rsplit("/", 1)[-1],
                number=self._chapter_number("", href),
                title=self._chapter_title_from_url(href),
                url=href,
            )

        next_data = self._extract_next_data(html)
        if next_data is not None:
            for href, title, chapter_id, number in self._iter_chapter_records(next_data):
                normalized = self._normalize_url(href, base_url)
                if "/chapter/" not in normalized:
                    continue
                found[normalized] = ChapterInfo(
                    id=chapter_id or normalized.rstrip("/").rsplit("/", 1)[-1],
                    number=number,
                    title=title or self._chapter_title_from_url(normalized),
                    url=normalized,
                )
        return self._sort_chapters(found.values())

    def _candidate_chapter_payloads(self, series_id: str, series_url: str) -> list[dict]:
        slug = urlparse(series_url).path.rstrip("/").split("/")[-1]
        return [
            {"url": f"{self.BASE}/api/novel/{series_id}/chapters"},
            {"url": f"{self.BASE}/api/novels/{series_id}/chapters"},
            {"url": f"{self.BASE}/api/chapters/{series_id}"},
            {"url": f"{self.BASE}/api/chapters", "params": {"novelId": series_id}},
            {"url": f"{self.BASE}/api/chapters", "params": {"seriesId": series_id}},
            {"url": f"{self.BASE}/api/chapter/list", "params": {"novelId": series_id}},
            {"url": f"{self.BASE}/api/chapter/list", "params": {"seriesId": series_id}},
            {"url": f"{self.BASE}/api/chapter/list", "params": {"slug": slug}},
        ]

    def _extract_chapters_from_json(self, payload, base_url: str) -> list[ChapterInfo]:
        found: dict[str, ChapterInfo] = {}
        for href, title, chapter_id, number in self._iter_chapter_records(payload):
            normalized = self._normalize_url(href, base_url)
            if "/chapter/" not in normalized:
                continue
            found[normalized] = ChapterInfo(
                id=chapter_id or normalized.rstrip("/").rsplit("/", 1)[-1],
                number=number,
                title=title or self._chapter_title_from_url(normalized),
                url=normalized,
            )
        return self._sort_chapters(found.values())

    def _iter_chapter_records(self, payload):
        if isinstance(payload, dict):
            if any(key in payload for key in ("url", "href", "path", "slug", "chapterNumber", "chapter_number", "chapterNo")):
                href = (
                    payload.get("url")
                    or payload.get("href")
                    or payload.get("link")
                    or payload.get("path")
                    or payload.get("slug")
                    or ""
                )
                title = (
                    payload.get("title")
                    or payload.get("name")
                    or payload.get("chapterTitle")
                    or payload.get("chapter_title")
                    or ""
                )
                chapter_id = str(payload.get("id") or payload.get("_id") or "").strip()
                number = self._coerce_number(
                    payload.get("chapterNumber")
                    or payload.get("chapter_number")
                    or payload.get("chapterNo")
                    or payload.get("number")
                    or payload.get("index")
                )
                if href:
                    yield href, title, chapter_id, number
            for value in payload.values():
                if isinstance(value, (dict, list)):
                    yield from self._iter_chapter_records(value)
            return

        if isinstance(payload, list):
            for item in payload:
                yield from self._iter_chapter_records(item)

    def _sort_chapters(self, chapters) -> list[ChapterInfo]:
        ordered = list(chapters)
        ordered.sort(
            key=lambda chapter: (
                chapter.number is None,
                chapter.number if chapter.number is not None else float("inf"),
                chapter.url,
            )
        )
        return ordered

    def _dedupe_chapters(self, chapters: list[ChapterInfo]) -> list[ChapterInfo]:
        deduped: dict[str, ChapterInfo] = {}
        for chapter in chapters:
            key = chapter.url.rstrip("/")
            existing = deduped.get(key)
            if existing is None or (existing.number is None and chapter.number is not None) or len(chapter.title) > len(existing.title):
                deduped[key] = chapter
        return self._sort_chapters(deduped.values())

    def _extract_total_chapters_hint(self, soup: BeautifulSoup, html: str) -> int | None:
        candidates: list[tuple[int, int]] = []

        text_sources = [soup.get_text(" ", strip=True), html]
        strong_patterns = (
            r"total\s+chapters?\s*[:\-]?\s*(\d{1,4})",
            r"chapters?\s*[:\-]?\s*(\d{1,4})",
            r"(\d{1,4})\s+chapters?",
            r"total\s+episodes?\s*[:\-]?\s*(\d{1,4})",
            r"episodes?\s*[:\-]?\s*(\d{1,4})",
            r"(\d{1,4})\s+episodes?",
        )
        for source in text_sources:
            for pattern in strong_patterns:
                for match in re.finditer(pattern, source, re.IGNORECASE):
                    try:
                        value = int(match.group(1))
                    except Exception:
                        continue
                    if not self._looks_like_real_total(value):
                        continue
                    score = 300 if "total" in match.group(0).casefold() else 200
                    candidates.append((score, value))

        next_data = self._extract_next_data(html)
        if next_data is not None:
            for key, value in self._iter_numeric_fields(next_data):
                key_text = str(key or "").casefold()
                if key_text not in {"chapters", "chaptercount", "chapter_count", "totalchapters", "total_chapters", "episodes", "episodecount", "episode_count", "totalepisodes", "total_episodes"}:
                    continue
                if not self._looks_like_real_total(value):
                    continue
                score = 400 if "total" in key_text else 320
                candidates.append((score, int(value)))

        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return candidates[0][1]

    def _can_synthesize_chapters(self, series_id: str) -> bool:
        return bool(re.fullmatch(r"\d+", str(series_id or "")))

    def _looks_like_real_total(self, value: int) -> bool:
        try:
            number = int(value)
        except Exception:
            return False
        if number <= 0 or number > 1500:
            return False
        if 1900 <= number <= 2100:
            return False
        return True

    def _iter_numeric_fields(self, payload, prefix: str = ""):
        if isinstance(payload, dict):
            for key, value in payload.items():
                key_name = f"{prefix}.{key}" if prefix else str(key)
                if isinstance(value, int):
                    yield key_name, value
                elif isinstance(value, str) and value.isdigit():
                    yield key_name, int(value)
                elif isinstance(value, (dict, list)):
                    yield from self._iter_numeric_fields(value, key_name)
            return
        if isinstance(payload, list):
            for index, item in enumerate(payload):
                yield from self._iter_numeric_fields(item, f"{prefix}[{index}]")

    def _synthesize_chapters(self, series_id: str, total_chapters: int) -> list[ChapterInfo]:
        chapters = []
        for index in range(1, int(total_chapters) + 1):
            url = f"{self.BASE}/chapter/{series_id}-{index}"
            chapters.append(ChapterInfo(
                id=f"{series_id}-{index}",
                number=float(index),
                title=f"Chapter {index}",
                url=url,
            ))
        return chapters

    def _discover_chapter_count(self, series_id: str, max_limit: int = 2000) -> int:
        if not self._can_synthesize_chapters(series_id):
            return 1
        if not self._chapter_exists(series_id, 1):
            return 0

        # Exponential expansion to find upper bound — batch concurrent probes
        low = 1
        high = 2
        while high <= max_limit:
            # Probe next two doublings in parallel to reduce round trips
            candidates = [high, min(high * 2, max_limit)]
            results = self._batch_chapter_exists(series_id, candidates)
            if results.get(high):
                low = high
                high = high * 2
            else:
                break

        high = min(high, max_limit + 1)
        left = low + 1
        right = min(high - 1, max_limit)
        best = low

        # Binary search — probe each level in a small batch
        while left <= right:
            mid = (left + right) // 2
            # Speculatively probe mid and the midpoint of each half simultaneously
            probe_low = (left + mid - 1) // 2
            probe_high = (mid + 1 + right) // 2
            probes = list({mid, probe_low, probe_high} & set(range(left, right + 1)))
            results = self._batch_chapter_exists(series_id, probes)
            if results.get(mid):
                best = mid
                left = mid + 1
            else:
                right = mid - 1

        return best

    def _batch_chapter_exists(self, series_id: str, chapter_numbers: list[int]) -> dict[int, bool]:
        """Check multiple chapter numbers concurrently."""
        if not chapter_numbers:
            return {}
        try:
            with ThreadPoolExecutor(max_workers=min(len(chapter_numbers), 4)) as pool:
                futures = {pool.submit(self._chapter_exists, series_id, n): n for n in chapter_numbers}
                return {futures[f]: f.result() for f in as_completed(futures)}
        except RuntimeError as exc:
            if "interpreter shutdown" in str(exc).casefold():
                logger.info(
                    "Skipping MVLEMPYR chapter probe during interpreter shutdown for series_id=%s",
                    series_id,
                )
                return {}
            raise

    def _chapter_exists(self, series_id: str, chapter_number: int) -> bool:
        url = f"{self.BASE}/chapter/{series_id}-{int(chapter_number)}"
        try:
            response = self._client().get(
                url,
                headers=self.get_request_headers(url),
                cookies=self._site_cookies(),
                impersonate=self.IMPERSONATE,
                timeout=12,
                allow_redirects=True,
            )
        except Exception:
            return False
        if response.status_code != 200 or self._is_cloudflare_block(response):
            return False
        final_url = str(getattr(response, 'url', '') or '')
        if final_url and '/chapter/' not in final_url:
            return False
        text = str(getattr(response, 'text', '') or '')
        title = self._extract_page_title(text)
        if title and any(marker in title.casefold() for marker in ('not found', '404', 'error')):
            return False
        return True

    def _extract_page_title(self, html: str) -> str:
        match = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
        if not match:
            return ''
        return ' '.join(match.group(1).split())

    def _chapter_source_score(self, chapters: list[ChapterInfo]) -> tuple[int, int, float]:
        count = len(chapters)
        numbered = [chapter.number for chapter in chapters if chapter.number is not None]
        unique_numbers = len(set(numbered))
        highest = max(numbered) if numbered else 0.0
        return (count, unique_numbers, highest)

    def _cover_score(self, url: str, series_title: str, alt_text: str) -> int:
        value = str(url or "").strip()
        if not value:
            return 0
        low = value.casefold()
        low_alt = str(alt_text or "").casefold()
        low_title = str(series_title or "").casefold()
        bad_markers = ("ratingstar", "rating-star", "fire", "icon", "logo", "avatar", "banner", "ads", "placeholder")
        if any(marker in low for marker in bad_markers) or any(marker in low_alt for marker in bad_markers):
            return 0
        score = 10
        if low.endswith((".jpg", ".jpeg", ".png", ".webp", ".avif")):
            score += 10
        if "cover" in low or "poster" in low:
            score += 20
        if low_title and low_title in low_alt:
            score += 50
        if low_title and all(part in low for part in low_title.split()[:3] if part):
            score += 30
        if "/_next/image" not in low:
            score += 5
        return score

    def _normalize_text(self, text: str) -> str:
        lines = [line.strip() for line in str(text or "").splitlines()]
        return "\n".join(line for line in lines if line)

    def _extract_chapter_title(self, soup: BeautifulSoup, chapter_url: str) -> str:
        for selector in ("h1", "h2", 'meta[property="og:title"]', "title"):
            node = soup.select_one(selector)
            if node is None:
                continue
            if node.name == "meta":
                value = str(node.get("content") or "").strip()
            else:
                value = node.get_text(" ", strip=True)
            value = re.sub(r"\s*\|\s*MVLEMPYR\s*$", "", value, flags=re.IGNORECASE).strip()
            if value:
                return value
        return self._chapter_title_from_url(chapter_url)

    # Selectors at or before this index are "high-confidence" — if one matches
    # with substantial text we stop early rather than scanning all 18 selectors.
    _CONTAINER_EARLY_EXIT_THRESHOLD = 4   # covers article .prose, article, main .prose, main article
    _CONTAINER_EARLY_EXIT_MIN_CHARS = 500

    def _find_chapter_container(self, soup: BeautifulSoup):
        best_node = None
        best_score = 0
        for idx, sel in enumerate(self._SEL_CHAPTER_CONTAINER):
            for node in sel.select(soup):
                text = self._normalize_text(node.get_text("\n", strip=True))
                score = len(text)
                if score > best_score and score >= 20:
                    best_node = node
                    best_score = score
            # Early exit: high-confidence selector found substantial content
            if (
                idx <= self._CONTAINER_EARLY_EXIT_THRESHOLD
                and best_score >= self._CONTAINER_EARLY_EXIT_MIN_CHARS
            ):
                break
        return best_node

    def _clean_chapter_html(self, node) -> str:
        clone = BeautifulSoup(str(node), "lxml")
        for tag in self._SEL_STRIP_TAGS.select(clone):
            tag.decompose()
        allowed = {"p", "div", "section", "article", "blockquote", "em", "strong", "i", "b", "span", "br", "h1", "h2", "h3", "h4", "ul", "ol", "li"}
        for tag in clone.find_all(True):
            if tag.name not in allowed:
                tag.unwrap()
                continue
            tag.attrs = {}
        return "".join(str(child) for child in clone.contents).strip()

    def _extract_next_data(self, html: str):
        match = re.search(
            r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>\s*(\{.*?\})\s*</script>',
            html,
            re.DOTALL | re.IGNORECASE,
        )
        if not match:
            return None
        try:
            return json.loads(match.group(1))
        except Exception:
            return None

    def _chapter_title_from_url(self, url: str) -> str:
        number = self._chapter_number("", url)
        if number is not None:
            if float(number).is_integer():
                return f"Chapter {int(number)}"
            return f"Chapter {format(number, 'g')}"
        return url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()

    def _chapter_number(self, text: str, url: str) -> float | None:
        match = re.search(r"/chapter/\d+-([0-9]+(?:\.[0-9]+)?)", url, re.IGNORECASE)
        if match:
            return self._coerce_number(match.group(1))
        for value in (text, url):
            match = re.search(r"chapter[^0-9]*([0-9]+(?:\.[0-9]+)?)", value, re.IGNORECASE)
            if match:
                return self._coerce_number(match.group(1))
        return None

    def _coerce_number(self, value) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return None

    def _site_cookies(self) -> dict[str, str]:
        if self._cookies_cache is None:
            self._cookies_cache = {c["name"]: c["value"] for c in load_site_cookies(self.site_name)}
        return self._cookies_cache

    def invalidate_session_cache(self) -> None:
        """Call this after updating cookies or user-agent so next request re-reads them."""
        self._cookies_cache = None
        self._user_agent_cache = None

    def _client(self):
        session = getattr(self, "_http", None)
        if session is None:
            session = cffi_requests.Session()
            self._http = session
        return session

    def _compatible_client(self, session):
        if session is None:
            return None
        module_name = str(getattr(session.__class__, "__module__", "") or "")
        if module_name.startswith("curl_cffi"):
            return session
        return None

    def _throttle(self, minimum_delay: float = 0.85) -> None:
        now = time.monotonic()
        wait_for = minimum_delay - (now - self._last_request_at)
        if wait_for > 0:
            time.sleep(wait_for)
        self._last_request_at = time.monotonic()

    def fetch_cover(self, url: str, headers: dict[str, str] | None = None) -> bytes | None:
        try:
            response = self._client().get(
                url,
                headers=self.get_request_headers(url),
                cookies=self._site_cookies(),
                impersonate=self.IMPERSONATE,
                timeout=20,
            )
            if response.status_code == 200:
                return response.content
        except Exception:
            pass
        return None

    def validate_session(self, cookies: list[dict], user_agent: str, url: str | None = None) -> tuple[bool, str]:
        cookie_dict = {str(item.get("name") or "").strip(): str(item.get("value") or "") for item in cookies if str(item.get("name") or "").strip()}
        headers = dict(self.get_request_headers(url or self.site_base_url))
        if user_agent:
            headers["User-Agent"] = str(user_agent)
        try:
            response = self._client().get(
                url or self.site_base_url,
                headers=headers,
                cookies=cookie_dict,
                impersonate=self.IMPERSONATE,
                timeout=15,
            )
        except Exception as exc:
            return False, f"Impersonated validation failed: {exc}"
        if response.status_code == 200 and not self._is_cloudflare_block(response):
            return True, "Validated by browser impersonation."
        detail = f"Impersonated validation returned HTTP {response.status_code}."
        if self._is_cloudflare_block(response):
            detail = "Impersonated validation still hit the block page."
        return False, detail

    def _normalize_series_url(self, url: str) -> str:
        value = self._normalize_url(url)
        if "/chapter/" in value:
            return self.series_url_from_chapter_url(value)
        return value.rstrip("/") + "/"

    def _normalize_url(self, url: str, base_url: str | None = None) -> str:
        raw = str(url or "").strip()
        if not raw:
            return ""
        if raw.startswith("//"):
            raw = "https:" + raw
        elif raw.startswith("/"):
            raw = urljoin(base_url or self.BASE, raw)
        elif not raw.startswith(("http://", "https://")):
            raw = urljoin(base_url or self.BASE, raw)
        return raw

    def _get(self, url: str, session=None, params: dict | None = None):
        client = self._compatible_client(session) or self._client()
        last_status = None
        for attempt in range(4):
            self._throttle()
            try:
                response = client.get(
                    url,
                    params=params,
                    headers=self.get_request_headers(url),
                    cookies=self._site_cookies(),
                    impersonate=self.IMPERSONATE,
                    timeout=25,
                )
            except Exception as exc:
                if attempt >= 3:
                    raise ScraperError(f"MVLEMPYR request failed for {url}: {exc}") from exc
                time.sleep(1.5 * (attempt + 1))
                continue

            last_status = response.status_code
            blocked = self._is_cloudflare_block(response)
            if response.status_code == 200 and not blocked:
                return response

            if blocked or response.status_code in {403, 429, 503}:
                logger.warning(
                    "MVLEMPYR request throttled url=%s status=%s attempt=%d",
                    url,
                    response.status_code,
                    attempt + 1,
                )
                if attempt < 3:
                    time.sleep(2.5 * (attempt + 1))
                    continue
                if has_site_cookies(self.site_name):
                    raise ScraperError(
                        f"MVLEMPYR temporarily rate-limited or blocked the request after repeated chapter fetches (HTTP {response.status_code})."
                    )
                raise ScraperError("MVLEMPYR blocked the request with Cloudflare.")

            raise ScraperError(f"Failed to load page: {url} (HTTP {response.status_code})")

        raise ScraperError(f"Failed to load page: {url} (HTTP {last_status or 'unknown'})")

    def _get_json(self, url: str, params: dict | None = None):
        response = self._get(url, params=params)
        try:
            return response.json()
        except ValueError as exc:
            raise ScraperError(f"MVLEMPYR returned invalid JSON from {url}") from exc

    def _is_cloudflare_block(self, response) -> bool:
        if response.status_code == 403:
            return True
        text = str(getattr(response, "text", "") or "").casefold()
        return "just a moment" in text and "cloudflare" in text
