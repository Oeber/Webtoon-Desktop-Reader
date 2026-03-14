import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from core.app_logging import get_logger
from core.site_session import apply_site_cookies, load_site_user_agent
from ..base import BaseScraper, ScraperError
from ..models import ChapterInfo, PageInfo, SeriesInfo

logger = get_logger(__name__)


class HiperCoolScraper(BaseScraper):
    site_name = "hiper_cool"
    site_display_name = "HiperCool"
    site_hosts = ("hiper.cool", "www.hiper.cool")
    site_base_url = "https://hiper.cool/"
    site_required_cookie_names = ("cf_clearance",)
    site_session_cookie_names = (
        "cf_clearance",
        "PHPSESSID",
        "wordpress_logged_in",
        "wordpress_sec",
        "wp-settings-1",
        "wp-settings-time-1",
    )

    BASE = "https://hiper.cool"

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Referer": BASE + "/",
    }

    @classmethod
    def can_handle(cls, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        return host in {"hiper.cool", "www.hiper.cool"}

    def get_series_info(self, url: str, session=None) -> SeriesInfo:
        series_url = self._normalize_series_url(url)
        logger.info("HiperCool: fetching series info from %s", series_url)

        client, should_close = self._prepare_client(session)
        try:
            response = client.get(series_url, headers=self._request_headers(), timeout=20)
        finally:
            if should_close:
                client.close()
        if self._is_cloudflare_block(response):
            raise ScraperError("HiperCool blocked the request with Cloudflare.")
        if response.status_code != 200:
            raise ScraperError(f"Failed to load page: {series_url} (HTTP {response.status_code})")

        soup = BeautifulSoup(response.text, "html.parser")
        title = self._extract_title(soup)
        slug = self._extract_series_slug(series_url)
        chapters = self._extract_chapters(soup)

        return SeriesInfo(
            site=self.site_name,
            series_id=slug,
            title=title,
            url=series_url,
            cover_url=self._extract_cover(soup),
            author=self._extract_author(soup),
            description=self._extract_description(soup),
            total_chapters=len(chapters),
            chapters=chapters,
        )

    def get_chapter_pages(self, chapter_url: str, session=None) -> list[PageInfo]:
        logger.info("HiperCool: fetching chapter pages from %s", chapter_url)

        client, should_close = self._prepare_client(session)
        try:
            response = client.get(chapter_url, headers=self._request_headers(), timeout=20)
        finally:
            if should_close:
                client.close()
        if self._is_cloudflare_block(response):
            raise ScraperError("HiperCool blocked the chapter request with Cloudflare.")
        if response.status_code != 200:
            raise ScraperError(f"Failed to load chapter page: {chapter_url} (HTTP {response.status_code})")

        soup = BeautifulSoup(response.text, "html.parser")
        image_urls = []

        for image in soup.select("img.wp-manga-chapter-img"):
            src = self._normalize_image_url(image.get("data-src") or image.get("src") or "")
            if src:
                image_urls.append(src)

        image_urls = self._dedupe(image_urls)
        if not image_urls:
            raise ScraperError(f"No chapter images found: {chapter_url}")

        return [PageInfo(index=i, image_url=url) for i, url in enumerate(image_urls, start=1)]

    def get_request_headers(self, url):
        return self._request_headers()

    def is_chapter_url(self, url: str) -> bool:
        return "/capitulo-" in url.rstrip("/").lower()

    def series_url_from_chapter_url(self, url: str) -> str:
        return self._normalize_series_url(url).rstrip("/")

    def extract_chapter_number(self, url: str) -> int | None:
        match = re.search(r"capitulo[-/ ]?(\d+)", url, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None

    def _normalize_series_url(self, url: str) -> str:
        stripped = url.rstrip("/")
        if "/capitulo-" in stripped.lower():
            stripped = re.split(r"/capitulo-[^/]+$", stripped, flags=re.IGNORECASE)[0]
        return stripped + "/"

    def _extract_title(self, soup: BeautifulSoup) -> str:
        heading = soup.find("h1")
        if heading:
            return heading.get_text(" ", strip=True)

        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            title = og_title["content"].strip()
            return re.sub(r"\s*-\s*Hipercool\s*$", "", title, flags=re.IGNORECASE).strip()

        raise ScraperError("Could not extract series title")

    def _extract_cover(self, soup: BeautifulSoup) -> str | None:
        image = soup.select_one(".summary_image img")
        if image and image.get("src"):
            return urljoin(self.BASE, image["src"].strip())

        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            return og_image["content"].strip()

        return None

    def _extract_author(self, soup: BeautifulSoup) -> str | None:
        author = soup.select_one(".author-content a, .author-content")
        if author:
            text = author.get_text(" ", strip=True)
            return text or None
        return None

    def _extract_description(self, soup: BeautifulSoup) -> str | None:
        for selector in (".summary__content p", ".description-summary p", ".summary_content p"):
            parts = [node.get_text(" ", strip=True) for node in soup.select(selector)]
            text = " ".join(part for part in parts if part)
            if text:
                return text
        return None

    def _extract_chapters(self, soup: BeautifulSoup) -> list[ChapterInfo]:
        chapters_by_url: dict[str, ChapterInfo] = {}

        for link in soup.select(".wp-manga-chapter a[href], .listing-chapters_wrap a[href]"):
            href = link.get("href", "").strip()
            if not href or "/capitulo-" not in href.lower():
                continue

            chapter_url = href.rstrip("/") + "/"
            title = link.get_text(" ", strip=True) or self._chapter_title_from_url(chapter_url)
            number = self._chapter_number(title, chapter_url)
            chapter_id = chapter_url.rstrip("/").rsplit("/", 1)[-1]

            existing = chapters_by_url.get(chapter_url)
            if existing is not None and existing.title.strip():
                continue

            chapters_by_url[chapter_url] = ChapterInfo(
                id=chapter_id,
                number=number,
                title=title,
                url=chapter_url,
            )

        chapters = list(chapters_by_url.values())
        chapters.sort(key=lambda chapter: (
            chapter.number is None,
            chapter.number if chapter.number is not None else float("inf"),
            chapter.url,
        ))
        return chapters

    def _extract_series_slug(self, url: str) -> str:
        path = urlparse(url).path.strip("/")
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 2 and parts[0] == "manga":
            return parts[1]
        return parts[-1] if parts else "unknown"

    def _chapter_title_from_url(self, url: str) -> str:
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        return slug.replace("-", " ").title()

    def _chapter_number(self, title: str, url: str) -> float | None:
        for value in (title, url):
            match = re.search(r"(\d+(?:\.\d+)?)", value)
            if match:
                try:
                    return float(match.group(1))
                except ValueError:
                    return None
        return None

    def _normalize_image_url(self, raw: str) -> str:
        value = raw.strip()
        if not value:
            return ""
        if value.startswith("//"):
            value = "https:" + value
        elif value.startswith("/"):
            value = urljoin(self.BASE, value)
        return value

    def _dedupe(self, items: list[str]) -> list[str]:
        seen = set()
        result = []
        for item in items:
            if not item or item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result

    def _is_cloudflare_block(self, response) -> bool:
        if response.status_code == 403:
            return True
        text = str(getattr(response, "text", "") or "").casefold()
        return "just a moment" in text and "cloudflare" in text

    def _prepare_client(self, session=None):
        if session is not None:
            apply_site_cookies(session, self.site_name)
            return session, False
        session = requests.Session()
        apply_site_cookies(session, self.site_name)
        return session, True

    def _request_headers(self) -> dict[str, str]:
        headers = dict(self.HEADERS)
        headers["User-Agent"] = load_site_user_agent(self.site_name, headers["User-Agent"])
        return headers
