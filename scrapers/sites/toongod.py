import json
import re
from html import unescape
from urllib.parse import urljoin, urlparse, parse_qs, unquote

import requests
from bs4 import BeautifulSoup

from core.site_session import apply_site_cookies, load_site_user_agent
from ..base import BaseScraper, ScraperError
from ..models import SeriesInfo, ChapterInfo, PageInfo


class ToonGodScraper(BaseScraper):
    site_name = "toongod"
    site_display_name = "ToonGod"
    site_hosts = ("toongod.org", "www.toongod.org")
    site_base_url = "https://www.toongod.org/"
    site_required_cookie_names = ("cf_clearance",)
    site_session_cookie_names = (
        "cf_clearance",
        "PHPSESSID",
        "wordpress_logged_in",
        "wordpress_sec",
        "wp-settings-1",
        "wp-settings-time-1",
    )
    BASE = "https://www.toongod.org"

    SERIES_PATTERNS = (
        "/webtoon/",
        "/manga/",
        "/series/",
    )

    CHAPTER_HINTS = (
        "/chapter-",
        "/chapter/",
        "/episode-",
        "/episode/",
    )

    BAD_IMAGE_HINTS = (
        "logo",
        "icon",
        "favicon",
        "avatar",
        "banner",
        "cover",
        "thumb",
        "thumbnail",
        "ads",
        "doubleclick",
        "gravatar",
        "emoji",
        "comment",
        "header",
        "footer",
    )

    @classmethod
    def can_handle(cls, url: str) -> bool:
        host = (urlparse(url).netloc or "").lower()
        return "toongod.org" in host

    def get_request_headers(self, url: str) -> dict:
        return {
            "User-Agent": "Mozilla/5.0",
            "Referer": self.BASE + "/",
            "Origin": self.BASE,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def _prepare_client(self, session=None):
        if session is not None:
            apply_site_cookies(session, self.site_name)
            return session, False
        session = requests.Session()
        apply_site_cookies(session, self.site_name)
        return session, True

    def _request_headers(self, url: str) -> dict[str, str]:
        headers = dict(self.get_request_headers(url))
        headers["User-Agent"] = load_site_user_agent(self.site_name, headers["User-Agent"])
        return headers

    def _is_cloudflare_block(self, response) -> bool:
        if response.status_code == 403:
            return True
        text = str(getattr(response, "text", "") or "").casefold()
        return "just a moment" in text and "cloudflare" in text

    def _get(self, url: str, session=None) -> requests.Response:
        client, should_close = self._prepare_client(session)
        try:
            r = client.get(url, headers=self._request_headers(url), timeout=20)
        finally:
            if should_close:
                client.close()
        if self._is_cloudflare_block(r):
            raise ScraperError("ToonGod blocked the request with Cloudflare.")
        if r.status_code != 200:
            raise ScraperError(f"Failed to load page: {url} ({r.status_code})")
        return r

    def _normalize_url(self, raw: str, base_url: str) -> str:
        raw = (raw or "").strip()
        if not raw:
            return ""

        raw = unescape(raw)
        raw = raw.replace("\\u002F", "/")
        raw = raw.replace("\\/", "/")
        raw = raw.replace("&amp;", "&")
        raw = raw.rstrip("\\").rstrip(",").rstrip(";").strip("'\"")

        if raw.startswith("//"):
            raw = "https:" + raw
        elif raw.startswith("/"):
            raw = urljoin(base_url, raw)

        if "_next/image" in raw:
            try:
                qs = parse_qs(urlparse(raw).query)
                wrapped = qs.get("url", [""])[0]
                if wrapped:
                    raw = unquote(wrapped)
                    if raw.startswith("/"):
                        raw = urljoin(base_url, raw)
            except Exception:
                pass

        return raw

    def _slug_from_url(self, url: str) -> str:
        path = urlparse(url).path.strip("/")
        if not path:
            return ""
        return path.split("/")[-1]

    def _chapter_number(self, text: str, url: str) -> float | None:
        haystack = f"{text} {url}".lower()

        patterns = [
            r"chapter[\s\-:]*([0-9]+(?:\.[0-9]+)?)",
            r"episode[\s\-:]*([0-9]+(?:\.[0-9]+)?)",
            r"/chapter[\-\/]([0-9]+(?:\.[0-9]+)?)",
            r"/episode[\-\/]([0-9]+(?:\.[0-9]+)?)",
        ]
        for pattern in patterns:
            m = re.search(pattern, haystack, re.I)
            if m:
                try:
                    return float(m.group(1))
                except Exception:
                    return None
        return None

    def _chapter_title_from_number(self, chapter_number: float | None, fallback: str) -> str:
        if chapter_number is None:
            return fallback
        if float(chapter_number).is_integer():
            return f"Chapter {int(chapter_number)}"
        return f"Chapter {format(chapter_number, 'g')}"

    def _is_chapter_url(self, url: str, series_slug: str) -> bool:
        if not url:
            return False

        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        if "toongod.org" not in host:
            return False

        path = parsed.path.lower()
        if not any(hint in path for hint in self.CHAPTER_HINTS):
            return False

        if series_slug and series_slug.lower() not in path:
            # keep this soft; some sites use numeric ids or different slugs
            pass

        return True

    def _extract_title(self, soup: BeautifulSoup) -> str:
        meta = soup.find("meta", property="og:title")
        if meta and meta.get("content"):
            title = str(meta["content"]).strip()
            title = re.sub(r"\s*[-|]\s*ToonGod.*$", "", title, flags=re.I)
            return title.strip()

        h1 = soup.find("h1")
        if h1:
            return h1.get_text(" ", strip=True)

        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(" ", strip=True)
            title = re.sub(r"\s*[-|]\s*ToonGod.*$", "", title, flags=re.I)
            return title.strip()

        raise ScraperError("Could not determine series title")

    def _extract_cover(self, soup: BeautifulSoup, base_url: str) -> str | None:
        selectors = [
            ('meta[property="og:image"]', "content"),
            ('meta[name="twitter:image"]', "content"),
            (".summary_image img", "src"),
            (".post-thumb img", "src"),
            (".thumb img", "src"),
            (".series-thumb img", "src"),
            ("img.wp-post-image", "src"),
        ]

        for selector, attr in selectors:
            node = soup.select_one(selector)
            if node and node.get(attr):
                url = self._normalize_url(str(node.get(attr)), base_url)
                if url:
                    return url

        return None

    def _clean_author_text(self, value: str | None) -> str | None:
        text = " ".join(str(value or "").split()).strip()
        if not text:
            return None
        text = re.sub(r"^(?:author|artist)(?:\(s\)|s)?\s*[:\-]?\s*", "", text, flags=re.IGNORECASE).strip()
        if not text:
            return None
        normalized = text.casefold().strip("()[]{}:;,- ")
        if not normalized or normalized in {"s", "author", "authors", "artist", "artists"}:
            return None
        return text

    def _extract_author(self, soup: BeautifulSoup) -> str | None:
        for item in soup.select(".post-content_item"):
            label = " ".join(part.get_text(" ", strip=True) for part in item.select(".summary-heading, h5, h4")).strip()
            if "author" not in label.casefold() and "artist" not in label.casefold():
                continue
            value_node = item.select_one(".author-content, .summary-content")
            cleaned = self._clean_author_text(
                value_node.get_text(" ", strip=True) if value_node is not None else item.get_text(" ", strip=True)
            )
            if cleaned:
                return cleaned

        for selector in (
            ".author-content a",
            ".author-content",
            ".mg_author a",
            ".mg_author",
        ):
            node = soup.select_one(selector)
            if node:
                cleaned = self._clean_author_text(node.get_text(" ", strip=True))
                if cleaned:
                    return cleaned

        text = soup.get_text("\n", strip=True)
        for pattern in (
            r"Author(?:s|\(s\))?\s*[:\-]?\s*(.+)",
            r"Artist(?:s|\(s\))?\s*[:\-]?\s*(.+)",
        ):
            m = re.search(pattern, text, re.I)
            if m:
                cleaned = self._clean_author_text(m.group(1).split("\n")[0].strip())
                if cleaned:
                    return cleaned

        return None

    def _extract_description(self, soup: BeautifulSoup) -> str | None:
        selectors = [
            ".summary__content",
            ".description-summary",
            ".series-synops",
            ".post-content_item .summary__content",
            ".entry-content",
        ]
        for selector in selectors:
            node = soup.select_one(selector)
            if node:
                text = node.get_text(" ", strip=True)
                if text:
                    return text

        meta = soup.find("meta", attrs={"name": "description"})
        if meta and meta.get("content"):
            text = str(meta["content"]).strip()
            if text:
                return text

        return None

    def _extract_chapters_from_links(self, soup: BeautifulSoup, series_url: str, series_slug: str) -> list[ChapterInfo]:
        found = {}
        for a in soup.find_all("a", href=True):
            href = self._normalize_url(a["href"], series_url)
            if not self._is_chapter_url(href, series_slug):
                continue

            raw_title = a.get_text(" ", strip=True) or self._slug_from_url(href).replace("-", " ").title()
            chapter_number = self._chapter_number(raw_title, href)
            if chapter_number is None:
                continue
            title = self._chapter_title_from_number(chapter_number, raw_title)
            key = href.rstrip("/")

            if key not in found:
                found[key] = ChapterInfo(
                    id=self._slug_from_url(href),
                    number=chapter_number,
                    title=title,
                    url=href,
                )

        return list(found.values())

    def _extract_chapters_from_scripts(self, html: str, series_url: str) -> list[ChapterInfo]:
        chapters = {}
        candidates = set()

        for pattern in [
            r'https?://[^"\']+toongod\.org/[^"\']*(?:chapter|episode)[^"\']+',
            r'\/[^"\']*(?:chapter|episode)[^"\']+',
        ]:
            for match in re.findall(pattern, html, flags=re.I):
                url = self._normalize_url(match, series_url)
                if url and "toongod.org" in url:
                    candidates.add(url)

        for raw_url in sorted(candidates):
            raw_title = self._slug_from_url(raw_url).replace("-", " ").title()
            chapter_number = self._chapter_number(raw_title, raw_url)
            if chapter_number is None:
                continue
            title = self._chapter_title_from_number(chapter_number, raw_title)
            chapters[raw_url.rstrip("/")] = ChapterInfo(
                id=self._slug_from_url(raw_url),
                number=chapter_number,
                title=title,
                url=raw_url,
            )

        return list(chapters.values())

    def _sort_chapters(self, chapters: list[ChapterInfo]) -> list[ChapterInfo]:
        if not chapters:
            return chapters

        def sort_key(ch: ChapterInfo):
            if ch.number is not None:
                return (0, ch.number, ch.title.lower())
            return (1, ch.title.lower())

        ordered = sorted(chapters, key=sort_key)

        # many sites list newest-first; normalize to oldest-first for downloading
        numbered = [c for c in ordered if c.number is not None]
        if len(numbered) >= 2 and numbered[0].number > numbered[-1].number:
            ordered.reverse()

        return ordered

    def get_series_info(self, url: str, session=None) -> SeriesInfo:
        r = self._get(url, session=session)
        soup = BeautifulSoup(r.text, "html.parser")

        title = self._extract_title(soup)
        slug = self._slug_from_url(url)
        cover_url = self._extract_cover(soup, url)
        author = self._extract_author(soup)
        description = self._extract_description(soup)

        chapters = self._extract_chapters_from_links(soup, url, slug)
        if not chapters:
            chapters = self._extract_chapters_from_scripts(r.text, url)

        chapters = self._sort_chapters(chapters)

        if not chapters:
            raise ScraperError(f"No chapters found for series: {url}")

        return SeriesInfo(
            site=self.site_name,
            series_id=slug,
            title=title,
            url=url,
            cover_url=cover_url,
            author=author,
            description=description,
            total_chapters=len(chapters),
            chapters=chapters,
        )

    def _is_reader_image(self, url: str) -> bool:
        low = url.lower()

        if not low.startswith(("http://", "https://")):
            return False

        if not any(ext in low for ext in (".jpg", ".jpeg", ".png", ".webp", ".avif")) and "/wp-content/" not in low:
            return False

        if any(bad in low for bad in self.BAD_IMAGE_HINTS):
            return False

        return True

    def _extract_images_from_dom(self, soup: BeautifulSoup, chapter_url: str) -> list[str]:
        images = []

        selectors = [
            ".reading-content img",
            ".reader-area img",
            ".chapter-content img",
            ".entry-content img",
            ".text-left img",
            "img",
        ]

        for selector in selectors:
            for img in soup.select(selector):
                for attr in ("data-src", "data-lazy-src", "data-lazy", "data-original", "src"):
                    raw = img.get(attr)
                    if not raw:
                        continue
                    url = self._normalize_url(str(raw), chapter_url)
                    if self._is_reader_image(url):
                        images.append(url)
                        break

            if images:
                break

        return images

    def _extract_images_from_scripts(self, html: str, chapter_url: str) -> list[str]:
        images = []

        # generic URL scraping
        for match in re.findall(r'https?://[^"\']+\.(?:jpg|jpeg|png|webp|avif)(?:\?[^"\']*)?', html, flags=re.I):
            url = self._normalize_url(match, chapter_url)
            if self._is_reader_image(url):
                images.append(url)

        # escaped or relative asset URLs
        for match in re.findall(r'["\']([^"\']+\.(?:jpg|jpeg|png|webp|avif)(?:\?[^"\']*)?)["\']', html, flags=re.I):
            url = self._normalize_url(match, chapter_url)
            if self._is_reader_image(url):
                images.append(url)

        return images

    def _dedupe_preserve_order(self, urls: list[str]) -> list[str]:
        seen = set()
        out = []
        for url in urls:
            key = url.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(key)
        return out

    def get_chapter_pages(self, chapter_url: str, session=None) -> list[PageInfo]:
        r = self._get(chapter_url, session=session)
        soup = BeautifulSoup(r.text, "html.parser")

        image_urls = self._extract_images_from_dom(soup, chapter_url)
        if not image_urls:
            image_urls = self._extract_images_from_scripts(r.text, chapter_url)

        image_urls = self._dedupe_preserve_order(image_urls)

        if len(image_urls) > 2:
            # optional trim for sites that include a cover/end card;
            # comment this out if Toongod does not need it.
            pass

        if not image_urls:
            raise ScraperError(f"No chapter images found: {chapter_url}")

        return [
            PageInfo(index=i, image_url=image_url)
            for i, image_url in enumerate(image_urls, start=1)
        ]
