import re
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from core.app_logging import get_logger
from core.app_paths import data_path
from ..base import BaseScraper, ScraperError
from ..models import SeriesInfo, ChapterInfo, PageInfo

logger = get_logger(__name__)


class OmegaScansScraper(BaseScraper):

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

    @classmethod
    def can_handle(cls, url: str) -> bool:
        return "omegascans.org" in url

    def get_series_info(self, url: str, session=None) -> SeriesInfo:
        logger.info("OmegaScans: fetching series info from %s", url)
        client = session or requests

        slug = self._extract_series_slug(url)
        if not slug:
            raise ScraperError(f"Could not extract series slug from URL: {url}")

        # Always fetch the page — we need it for metadata AND to extract the
        # numeric series_id that the chapter query API requires.
        r = client.get(url, headers=self.HEADERS, timeout=20)
        if r.status_code != 200:
            raise ScraperError(f"Failed to load page: {url}")

        html = r.text
        soup = BeautifulSoup(html, "html.parser")

        title = self._extract_title(soup)
        cover = self._extract_cover(soup)
        author = self._extract_author(soup)
        description = self._extract_description(soup)

        # Extract the numeric series_id embedded in the Next.js RSC payload,
        # then use it to hit the chapter query API for the full chapter list.
        series_id = self._extract_numeric_series_id(html)
        if series_id:
            logger.info("OmegaScans: found series_id=%s for slug=%s, querying chapter API", series_id, slug)
            chapters = self._fetch_chapters_from_api(series_id, slug, session=client if client is not requests else None)
        else:
            logger.warning("OmegaScans: could not find numeric series_id in page, falling back to HTML chapter extraction for %s", url)
            chapters = self._extract_chapters_from_html(soup, html, url)

        total_chapters = len(chapters) or self._extract_total_chapters(soup)

        if not chapters:
            self._dump_series_html(url, html)

        return SeriesInfo(
            site=self.site_name,
            series_id=slug,
            title=title,
            url=url,
            cover_url=cover,
            author=author,
            description=description,
            total_chapters=total_chapters,
            chapters=chapters,
        )

    # ------------------------------------------------------------------
    # HeanCMS chapter query API
    # ------------------------------------------------------------------

    def _extract_numeric_series_id(self, html: str) -> str | None:
        # OmegaScans bakes the numeric series_id into the Next.js RSC payload.
        # The payload lives inside a <script> JS string, so JSON quotes are
        # backslash-escaped in the raw HTTP response:  \"series_id\":502
        # We match both that escaped form and the bare-quote form.

        # Pattern 1: next to "series_type" — most specific, avoids season/chapter IDs
        m = re.search(r'[\\]?"series_id[\\]?"\s*:\s*(\d+)\s*,[\s\S]{0,20}[\\]?"series_type[\\]?"', html)
        if m:
            return m.group(1)

        # Pattern 2: series_id somewhere before series_type on the same line
        m = re.search(r'series_id[^:]{0,3}:\s*(\d+)[^}]{0,80}series_type', html)
        if m:
            return m.group(1)

        # Pattern 3: any series_id:<number> — skip tiny values (1-10 are season/chapter IDs)
        for m in re.finditer(r'series_id[^:]{0,3}:\s*(\d+)', html):
            val = int(m.group(1))
            if val > 10:
                return str(val)

        return None

    def _fetch_chapters_from_api(self, series_id: str, slug: str, session=None) -> list[ChapterInfo]:
        """
        Calls GET https://api.omegascans.org/chapter/query?series_id=<id>&page=N&perPage=30
        Paginates until all chapters are collected, including all fractional ones.
        """
        api_url = f"{self.API_BASE}/chapter/query"
        all_raw: list[dict] = []
        page = 1
        per_page = 30

        client = session or requests
        while True:
            params = {"series_id": series_id, "perPage": per_page, "page": page}
            try:
                r = client.get(api_url, params=params, headers=self.API_HEADERS, timeout=30)
                if r.status_code != 200:
                    logger.warning("OmegaScans: chapter API returned %d for series_id=%s page=%d", r.status_code, series_id, page)
                    break
                data = r.json()
            except Exception as e:
                logger.warning("OmegaScans: chapter API failed for series_id=%s page=%d: %s", series_id, page, e)
                break

            # Response: {"data": [...], "meta": {"last_page": N}} or flat list
            if isinstance(data, dict):
                page_chapters = data.get("data") or data.get("chapters") or []
                meta = data.get("meta") or {}
                last_page = meta.get("last_page") or meta.get("lastPage") or 1
            elif isinstance(data, list):
                page_chapters = data
                last_page = 1
            else:
                break

            if not page_chapters:
                break

            all_raw.extend(page_chapters)

            if page >= last_page:
                break
            page += 1

        if not all_raw:
            logger.warning("OmegaScans: chapter API returned no chapters for series_id=%s", series_id)
            return []

        logger.info("OmegaScans: chapter API returned %d chapters for series_id=%s", len(all_raw), series_id)
        return self._parse_raw_chapters(all_raw, slug)

    def _parse_raw_chapters(self, raw_chapters: list, slug: str) -> list[ChapterInfo]:
        """
        Normalises a list of raw chapter dicts from the API into ChapterInfo objects.
        Each chapter dict contains fields like:
            chapter_slug, chapter_name, chapter (number), price, ...
        """
        chapters_by_url: dict[str, ChapterInfo] = {}

        for ch in raw_chapters:
            if not isinstance(ch, dict):
                continue

            chapter_slug = (
                ch.get("chapter_slug")
                or ch.get("slug")
                or ""
            )
            chapter_slug = str(chapter_slug).strip().lower()

            # If no slug, build one from the chapter number
            if not chapter_slug:
                num_raw = ch.get("chapter") or ch.get("number") or ""
                if num_raw:
                    chapter_slug = f"chapter-{str(num_raw).replace('.', '-')}"
                else:
                    continue

            if not chapter_slug.startswith("chapter-"):
                chapter_slug = f"chapter-{chapter_slug}"

            normalized_url = f"{self.BASE}/series/{slug}/{chapter_slug}"

            if normalized_url in chapters_by_url:
                continue

            # Chapter number from explicit numeric field first, slug second
            num_raw = ch.get("chapter") or ch.get("number") or ch.get("chapter_number") or ""
            number = self._parse_chapter_number(str(num_raw)) if num_raw else self._extract_chapter_number_value(chapter_slug)

            title = (
                ch.get("chapter_name")
                or ch.get("name")
                or ch.get("title")
                or ""
            ).strip()
            if not title:
                title = self._chapter_title_from_slug(chapter_slug)

            chapters_by_url[normalized_url] = ChapterInfo(
                id=chapter_slug,
                number=number,
                title=title,
                url=normalized_url,
            )

        chapters = list(chapters_by_url.values())
        chapters.sort(key=lambda c: (
            c.number is None,
            c.number if c.number is not None else float("inf"),
            c.url,
        ))
        return chapters

    def _parse_chapter_number(self, value: str) -> float | None:
        """Parse a raw chapter number string like '83', '83.5', '83-5' into a float."""
        value = value.strip().replace("-", ".")
        try:
            return float(value)
        except ValueError:
            return None

    # ------------------------------------------------------------------
    # Page image extraction (unchanged from original)
    # ------------------------------------------------------------------

    def get_chapter_pages(self, chapter_url: str, session=None) -> list[PageInfo]:
        logger.info("OmegaScans: fetching chapter pages from %s", chapter_url)
        client = session or requests
        r = client.get(chapter_url, headers=self.HEADERS, timeout=20)
        if r.status_code != 200:
            raise ScraperError(f"Failed to load chapter page: {chapter_url}")

        html = r.text
        soup = BeautifulSoup(html, "html.parser")

        image_urls = []

        image_urls.extend(self._extract_reader_images_from_dom(soup))
        image_urls.extend(self._extract_reader_images_from_html(html))
        image_urls.extend(self._extract_reader_images_from_scripts(soup))

        image_urls = self._dedupe(image_urls)

        if not image_urls:
            logger.warning("OmegaScans: no reader images found for %s", chapter_url)
            raise ScraperError(
                "No chapter page images found for this chapter. "
                "The page may be premium, gated, or using a different render path."
            )

        if len(image_urls) > 2:
            image_urls = image_urls[1:-1]

        logger.info("OmegaScans: extracted %d page images from %s", len(image_urls), chapter_url)
        return [
            PageInfo(index=i, image_url=url)
            for i, url in enumerate(image_urls, start=1)
        ]

    # ------------------------------------------------------------------
    # HTML fallback helpers (kept from original, used only when API fails)
    # ------------------------------------------------------------------

    def _extract_title(self, soup):
        h1 = soup.find("h1")
        if h1:
            return h1.get_text(" ", strip=True)

        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            title = og["content"].strip()
        else:
            title_tag = soup.find("title")
            title = title_tag.get_text(" ", strip=True) if title_tag else "Unknown Title"

        title = re.sub(r"\s*-\s*Omega\s*Scans\s*$", "", title, flags=re.IGNORECASE)
        return title.strip() or "Unknown Title"

    def _extract_cover(self, soup):
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            return og["content"]

        img = soup.find("img")
        if img and img.get("src"):
            return self._normalize_asset_url(img["src"])

        return None

    def _extract_author(self, soup):
        text = soup.get_text("\n", strip=True)

        m = re.search(
            r"Author\s+(.+?)(?=\n(?:Total chapters|Bookmarks|Reviews|Series comments|Release year|Total views)\b)",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if m:
            return " ".join(m.group(1).split()).strip()

        return None

    def _extract_chapters_from_html(self, soup: BeautifulSoup, html: str, series_url: str) -> list[ChapterInfo]:
        """HTML-only chapter extraction. Used only when the API is unavailable."""
        slug = self._extract_series_slug(series_url)
        if not slug:
            return []

        chapter_prefix = f"/series/{slug}/chapter-"
        chapters_by_url: dict[str, ChapterInfo] = {}

        for link in soup.select("a[href]"):
            href = (link.get("href") or "").strip()
            if not href:
                continue

            normalized_url = urljoin(self.BASE, href).rstrip("/")
            parsed = urlparse(normalized_url)
            if parsed.netloc.lower() != urlparse(self.BASE).netloc.lower():
                continue
            if chapter_prefix not in parsed.path.rstrip("/").lower():
                continue

            chapter_slug = parsed.path.rstrip("/").rsplit("/", 1)[-1]
            number = self._extract_chapter_number_value(chapter_slug)
            title = link.get_text(" ", strip=True) or self._chapter_title_from_slug(chapter_slug)

            existing = chapters_by_url.get(normalized_url)
            if existing is not None and existing.title.strip():
                continue

            chapters_by_url[normalized_url] = ChapterInfo(
                id=chapter_slug,
                number=number,
                title=title,
                url=normalized_url,
            )

        for match in re.finditer(
            rf"/series/{re.escape(slug)}/(chapter-[a-z0-9.-]+)",
            html,
            flags=re.IGNORECASE,
        ):
            chapter_slug = match.group(1).rstrip("/")
            normalized_url = f"{self.BASE}/series/{slug}/{chapter_slug}".rstrip("/")
            if normalized_url in chapters_by_url:
                continue
            number = self._extract_chapter_number_value(chapter_slug)
            chapters_by_url[normalized_url] = ChapterInfo(
                id=chapter_slug,
                number=number,
                title=self._chapter_title_from_slug(chapter_slug),
                url=normalized_url,
            )

        chapters = list(chapters_by_url.values())
        chapters.sort(key=lambda c: (
            c.number is None,
            c.number if c.number is not None else float("inf"),
            c.url,
        ))
        logger.info("OmegaScans: extracted %d chapter links from HTML for %s", len(chapters), series_url)
        return chapters

    def _extract_total_chapters(self, soup):
        text = soup.get_text("\n", strip=True)
        m = re.search(r"Total chapters\s+(\d+)", text, flags=re.IGNORECASE)
        if m:
            return int(m.group(1))
        return None

    def _extract_description(self, soup):
        candidates = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        for text in candidates:
            if not text:
                continue
            if len(text) < 40:
                continue
            if text.lower().startswith("author "):
                continue
            return text

        page_text = soup.get_text("\n", strip=True)
        m = re.search(
            r"(?:Ongoing|Completed|Hiatus).*?\n(.+?)(?=\n(?:Chapters list|Author|Release year|Total views)\b)",
            page_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if m:
            return " ".join(m.group(1).split()).strip()

        return None

    def _extract_series_slug(self, url):
        url = url.rstrip("/")
        marker = "/series/"
        idx = url.find(marker)
        if idx == -1:
            return None
        slug = url[idx + len(marker):]
        if "/" in slug:
            slug = slug.split("/", 1)[0]
        return slug.strip() or None

    def _extract_chapter_number_value(self, value: str) -> float | None:
        match = re.search(r"chapter[-/ ]?(\d+(?:[.-]\d+)?)", value, re.IGNORECASE)
        if not match:
            return None
        try:
            return float(match.group(1).replace("-", "."))
        except ValueError:
            return None

    def _chapter_title_from_slug(self, chapter_slug: str) -> str:
        return chapter_slug.replace("-", " ").title()

    def _dump_series_html(self, url: str, html: str):
        try:
            dump_dir = data_path("debug")
            dump_dir.mkdir(parents=True, exist_ok=True)
            slug = self._extract_series_slug(url) or "omega_scans"
            dump_path = dump_dir / f"{slug}.series.html"
            dump_path.write_text(html, encoding="utf-8", errors="replace")
            logger.warning("OmegaScans: dumped series HTML to %s for inspection", dump_path)
        except Exception as e:
            logger.warning("OmegaScans: failed to dump series HTML for %s", url, exc_info=e)

    # ------------------------------------------------------------------
    # Image extraction helpers (unchanged from original)
    # ------------------------------------------------------------------

    def _extract_reader_images_from_dom(self, soup) -> list[str]:
        urls = []
        for img in soup.find_all("img"):
            candidates = []
            for attr in ("src", "data-src", "data-lazy-src", "data-original"):
                value = (img.get(attr) or "").strip()
                if value:
                    candidates.append(value)
            for attr in ("srcset", "data-srcset"):
                srcset = (img.get(attr) or "").strip()
                if srcset:
                    candidates.extend(self._split_srcset(srcset))
            for raw in candidates:
                normalized = self._normalize_asset_url(raw)
                if self._looks_like_reader_page(normalized):
                    urls.append(normalized)
        return self._dedupe(urls)

    def _extract_reader_images_from_html(self, html: str) -> list[str]:
        candidates = []
        patterns = [
            r'https?://[^"\'>\s]+',
            r'https?:\\?/\\?/[^"\'>\s]+',
            r'/(?:_next/image\?[^"\'>\s]+|[^"\'>\s]+\.(?:jpg|jpeg|png|webp|avif)(?:\?[^"\'>\s]*)?)',
            r'/\\u002F_next\\u002Fimage\\u003F[^"\'>\s]+',
        ]
        for pattern in patterns:
            candidates.extend(re.findall(pattern, html, flags=re.IGNORECASE))
        cleaned = []
        for raw in candidates:
            normalized = self._normalize_asset_url(raw)
            if self._looks_like_reader_page(normalized):
                cleaned.append(normalized)
        return self._dedupe(cleaned)

    def _extract_reader_images_from_scripts(self, soup) -> list[str]:
        urls = []
        for script in soup.find_all("script"):
            content = script.string or script.get_text(" ", strip=False)
            if not content:
                continue
            urls.extend(self._extract_reader_images_from_html(content))
        return self._dedupe(urls)

    def _split_srcset(self, srcset: str) -> list[str]:
        parts = []
        for item in srcset.split(","):
            item = item.strip()
            if not item:
                continue
            parts.append(item.split(" ")[0].strip())
        return parts

    def _normalize_asset_url(self, raw: str) -> str:
        if not raw:
            return ""
        raw = raw.strip()
        raw = raw.replace("\\u002F", "/")
        raw = raw.replace("\\/", "/")
        raw = raw.replace("&amp;", "&")
        raw = raw.rstrip("\\").rstrip(",").rstrip(";").strip('\'"')
        if raw.startswith("//"):
            raw = "https:" + raw
        if raw.startswith("/"):
            raw = urljoin(self.BASE, raw)
        if not raw.startswith("http"):
            return ""
        parsed = urlparse(raw)
        if "/_next/image" in parsed.path:
            query = parse_qs(parsed.query)
            inner = query.get("url", [])
            if inner:
                inner_url = unquote(inner[0]).strip()
                inner_url = inner_url.rstrip("\\").rstrip(",").rstrip(";").strip('\'"')
                if inner_url.startswith("//"):
                    inner_url = "https:" + inner_url
                elif inner_url.startswith("/"):
                    inner_url = urljoin(self.BASE, inner_url)
                return inner_url
        return raw

    def _looks_like_reader_page(self, url: str) -> bool:
        lower = url.lower()
        if not lower.startswith("http"):
            return False
        if not any(ext in lower for ext in [".jpg", ".jpeg", ".png", ".webp", ".avif"]):
            return False
        blocked = [
            "icon.png", "logo", "avatar", "banner", "cover",
            "/icons/", "/avatars/", "/covers/", "wetried_only", "favicon",
        ]
        if any(bad in lower for bad in blocked):
            return False
        allowed_hints = [
            "media.omegascans.org/",
            "/uploads/series/",
            "/uploads/chapters/",
            "/file/",
        ]
        if not any(hint in lower for hint in allowed_hints):
            return False
        parsed = urlparse(url)
        if parsed.netloc.lower() == "omegascans.org" and parsed.path.count("/") <= 1:
            return False
        return True

    def _dedupe(self, items: list[str]) -> list[str]:
        seen = set()
        out = []
        for item in items:
            if not item:
                continue
            if item in seen:
                continue
            seen.add(item)
            out.append(item)
        return out

    def get_request_headers(self, url: str) -> dict:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Referer": "https://omegascans.org/",
        }
