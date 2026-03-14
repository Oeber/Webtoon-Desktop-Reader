import re
from html import unescape
from urllib.parse import urljoin, urlparse, parse_qs, unquote

from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup

from core.site_session import load_site_cookies, load_site_user_agent
from ..base import BaseScraper, ScraperError
from ..models import SeriesInfo, ChapterInfo, PageInfo


class ManhuaTopScraper(BaseScraper):
    site_name = "manhuatop"
    site_display_name = "ManhuaTop"
    site_hosts = ("manhuatop.org", "www.manhuatop.org")
    site_base_url = "https://manhuatop.org/"
    site_required_cookie_names = ("cf_clearance",)
    site_session_cookie_names = (
        "cf_clearance",
        "PHPSESSID",
        "wordpress_logged_in",
        "wordpress_sec",
        "wp-settings-1",
        "wp-settings-time-1",
    )

    # curl_cffi impersonate target — must be a version supported by the
    # installed curl_cffi build. chrome120 is the lowest reliable target
    # that bypasses ManhuaTop's TLS fingerprint check.
    IMPERSONATE = "chrome120"

    BASE = "https://manhuatop.org"

    # URL path segments that indicate a chapter page vs. a series page
    CHAPTER_HINTS = (
        "/chapter-",
        "/chapter/",
        "/episode-",
        "/episode/",
    )

    # Substrings that identify non-reader images to reject
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

    # ------------------------------------------------------------------ #
    # Site session config
    # site_session.py reads these class-level attributes to know the host,
    # display name, and which cookies are required for auth.
    # ------------------------------------------------------------------ #

    @classmethod
    def get_site_session_config(cls) -> dict:
        return {
            "display_name": cls.site_display_name,
            "base_url": cls.site_base_url,
            "hosts": cls.site_hosts,
            "required_cookie_names": cls.site_required_cookie_names,
            "session_cookie_names": cls.site_session_cookie_names,
        }

    # ------------------------------------------------------------------ #
    # URL matching
    # ------------------------------------------------------------------ #

    @classmethod
    def can_handle(cls, url: str) -> bool:
        host = (urlparse(url).netloc or "").lower()
        return "manhuatop.org" in host

    # ------------------------------------------------------------------ #
    # HTTP helpers
    # ------------------------------------------------------------------ #

    def get_request_headers(self, url: str) -> dict:
        # Base UA — will be overridden by the saved browser UA at request time.
        # Must match the UA that solved the CF challenge, so we always load
        # from site_session rather than hardcoding here.
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Referer": self.BASE + "/",
            "Origin": self.BASE,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def _site_cookies(self) -> dict[str, str]:
        # Inject cookies by name/value only — no domain scoping — so that
        # curl_cffi sends them regardless of domain string format in storage.
        return {c["name"]: c["value"] for c in load_site_cookies(self.site_name)}

    def _request_headers(self, url: str) -> dict[str, str]:
        headers = dict(self.get_request_headers(url))
        # Always use the UA that was active when cf_clearance was issued.
        # Cloudflare binds the token to this UA — sending a different one
        # causes an immediate 403 even with a valid cookie.
        headers["User-Agent"] = load_site_user_agent(self.site_name, headers["User-Agent"])
        return headers

    def _is_cloudflare_block(self, response) -> bool:
        if response.status_code == 403:
            return True
        text = str(getattr(response, "text", "") or "").casefold()
        return "just a moment" in text and "cloudflare" in text

    def _get(self, url: str, session=None) -> cffi_requests.Response:
        # ManhuaTop requires TLS fingerprint impersonation — plain requests
        # is rejected with 403 even with valid cf_clearance cookies because
        # Cloudflare validates the TLS client hello against the browser that
        # originally solved the challenge.
        #
        # The session parameter is accepted for interface compatibility with
        # the downloader but is not used here — curl_cffi manages its own
        # connection pool per call.
        try:
            r = cffi_requests.get(
                url,
                headers=self._request_headers(url),
                cookies=self._site_cookies(),
                impersonate=self.IMPERSONATE,
                timeout=20,
            )
        except Exception as e:
            raise ScraperError(f"Request failed for {url}: {e}") from e

        if self._is_cloudflare_block(r):
            raise ScraperError("ManhuaTop blocked the request with Cloudflare.")
        if r.status_code != 200:
            raise ScraperError(f"Failed to load page: {url} ({r.status_code})")
        return r

    # ------------------------------------------------------------------ #
    # URL normalization
    # ------------------------------------------------------------------ #

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

        # Unwrap _next/image optimization wrappers
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

    # ------------------------------------------------------------------ #
    # Chapter number / title helpers
    # ------------------------------------------------------------------ #

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

    def _is_chapter_url(self, url: str, series_slug: str = "") -> bool:
        if not url:
            return False
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        if "manhuatop.org" not in host:
            return False
        path = parsed.path.lower()
        if not any(hint in path for hint in self.CHAPTER_HINTS):
            return False
        # Enforce series slug to avoid picking up chapters from other series
        # that appear in "latest updates" sidebars on the same page.
        if series_slug and series_slug.lower() not in path:
            return False
        return True

    # ------------------------------------------------------------------ #
    # Metadata extraction
    # ------------------------------------------------------------------ #

    def _extract_title(self, soup: BeautifulSoup) -> str:
        meta = soup.find("meta", property="og:title")
        if meta and meta.get("content"):
            title = str(meta["content"]).strip()
            title = re.sub(r"\s*[-|]\s*ManhuaTop.*$", "", title, flags=re.I)
            return title.strip()

        h1 = soup.find("h1")
        if h1:
            return h1.get_text(" ", strip=True)

        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(" ", strip=True)
            title = re.sub(r"\s*[-|]\s*ManhuaTop.*$", "", title, flags=re.I)
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
            label = " ".join(
                part.get_text(" ", strip=True) for part in item.select(".summary-heading, h5, h4")
            ).strip()
            if "author" not in label.casefold() and "artist" not in label.casefold():
                continue
            value_node = item.select_one(".author-content, .summary-content")
            cleaned = self._clean_author_text(
                value_node.get_text(" ", strip=True) if value_node is not None else item.get_text(" ", strip=True)
            )
            if cleaned:
                return cleaned

        for selector in (".author-content a", ".author-content", ".mg_author a", ".mg_author"):
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

    # ------------------------------------------------------------------ #
    # Chapter discovery
    # ------------------------------------------------------------------ #

    def _fetch_chapters_ajax(self, series_url: str) -> list[ChapterInfo]:
        """
        Madara theme exposes a POST endpoint at <series_url>/ajax/chapters/
        that returns the full chapter list as HTML without requiring a nonce.
        This is more reliable than scraping the series page which only shows
        the most recent 2 chapters in static HTML.
        """
        ajax_url = series_url.rstrip("/") + "/ajax/chapters/"
        try:
            r = cffi_requests.post(
                ajax_url,
                headers={
                    **self._request_headers(ajax_url),
                    "X-Requested-With": "XMLHttpRequest",
                },
                cookies=self._site_cookies(),
                impersonate=self.IMPERSONATE,
                timeout=20,
            )
        except Exception as e:
            raise ScraperError(f"Failed to fetch chapter list: {ajax_url}: {e}") from e

        if self._is_cloudflare_block(r):
            raise ScraperError("ManhuaTop blocked the chapter list request with Cloudflare.")
        if r.status_code != 200:
            raise ScraperError(f"Chapter list request failed: {ajax_url} ({r.status_code})")

        soup = BeautifulSoup(r.text, "html.parser")
        slug = self._slug_from_url(series_url)
        return self._extract_chapters_from_links(soup, series_url, slug)

    def _extract_chapters_from_links(self, soup: BeautifulSoup, series_url: str, series_slug: str = "") -> list[ChapterInfo]:
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

    def _sort_chapters(self, chapters: list[ChapterInfo]) -> list[ChapterInfo]:
        if not chapters:
            return chapters

        def sort_key(ch: ChapterInfo):
            if ch.number is not None:
                return (0, ch.number, ch.title.lower())
            return (1, ch.title.lower())

        ordered = sorted(chapters, key=sort_key)

        # Normalize to oldest-first (many sites list newest-first)
        numbered = [c for c in ordered if c.number is not None]
        if len(numbered) >= 2 and numbered[0].number > numbered[-1].number:
            ordered.reverse()

        return ordered

    # ------------------------------------------------------------------ #
    # Series info
    # ------------------------------------------------------------------ #

    def get_series_info(self, url: str, session=None) -> SeriesInfo:
        r = self._get(url, session=session)
        soup = BeautifulSoup(r.text, "html.parser")

        title = self._extract_title(soup)
        slug = self._slug_from_url(url)
        cover_url = self._extract_cover(soup, url)
        author = self._extract_author(soup)
        description = self._extract_description(soup)

        # Use the Madara ajax/chapters/ endpoint for the full chapter list.
        # The static series page only renders the 2 most recent chapters.
        chapters = self._fetch_chapters_ajax(url)
        if not chapters:
            # Fallback to static HTML in case the ajax endpoint changes
            chapters = self._extract_chapters_from_links(soup, url, slug)

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

    # ------------------------------------------------------------------ #
    # Chapter pages
    # ------------------------------------------------------------------ #

    def _is_reader_image(self, url: str) -> bool:
        low = url.lower()

        if not low.startswith(("http://", "https://")):
            return False

        has_ext = any(ext in low for ext in (".jpg", ".jpeg", ".png", ".webp", ".avif"))
        has_wp = "/wp-content/" in low
        if not has_ext and not has_wp:
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

        for match in re.findall(
            r'https?://[^"\']+\.(?:jpg|jpeg|png|webp|avif)(?:\?[^"\']*)?', html, flags=re.I
        ):
            url = self._normalize_url(match, chapter_url)
            if self._is_reader_image(url):
                images.append(url)

        for match in re.findall(
            r'["\']([^"\']+\.(?:jpg|jpeg|png|webp|avif)(?:\?[^"\']*)?)["\']', html, flags=re.I
        ):
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

        if not image_urls:
            raise ScraperError(f"No chapter images found: {chapter_url}")

        return [
            PageInfo(index=i, image_url=image_url)
            for i, image_url in enumerate(image_urls, start=1)
        ]

    def download_asset(self, url: str, dest_path: str) -> bool:
        # s3.manhuatop.org requires TLS fingerprint impersonation just like
        # the main domain. Plain requests is rejected with 403 even with
        # valid cookies, so we must use curl_cffi here too.
        try:
            r = cffi_requests.get(
                url,
                headers=self._request_headers(url),
                cookies=self._site_cookies(),
                impersonate=self.IMPERSONATE,
                timeout=30,
            )
        except Exception as e:
            raise ScraperError(f"Asset download failed: {url}: {e}") from e

        if r.status_code != 200:
            raise ScraperError(f"Asset download failed: {url} ({r.status_code})")

        with open(dest_path, "wb") as f:
            f.write(r.content)
        return True