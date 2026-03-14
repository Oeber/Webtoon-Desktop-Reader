import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

from scrapers.base import ScraperError
from scrapers.discovery_base import BaseDiscoveryProvider
from scrapers.models import CatalogPage, CatalogSeries
from core.site_session import load_site_cookies, load_site_user_agent, site_cookie_header, site_cookie_header


class ManhuaTopDiscoveryProvider(BaseDiscoveryProvider):
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

    BASE = "https://manhuatop.org"

    # curl_cffi impersonate target — must match the download scraper so that
    # both use the same TLS fingerprint when sharing a cf_clearance token.
    IMPERSONATE = "chrome120"

    # ------------------------------------------------------------------ #
    # HTTP helpers
    # ------------------------------------------------------------------ #

    def _site_cookies(self) -> dict[str, str]:
        # Inject by name/value only — no domain argument — so curl_cffi
        # sends all cookies regardless of domain string format in storage.
        return {c["name"]: c["value"] for c in load_site_cookies(self.site_name)}

    def _request_headers(self) -> dict[str, str]:
        return {
            # Always use the UA that was active when cf_clearance was issued.
            # Cloudflare binds the token to this exact UA string.
            "User-Agent": load_site_user_agent(
                self.site_name,
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            ),
            "Referer": self.BASE + "/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def _cover_headers(self) -> dict[str, str]:
        # Cover images on manhuatop.org/wp-content/ are served under the same
        # Cloudflare zone and require the session cookies to avoid a 403.
        headers = {
            "User-Agent": load_site_user_agent(self.site_name, "Mozilla/5.0"),
            "Referer": self.BASE + "/",
        }
        cookie = site_cookie_header(self.site_name)
        if cookie:
            headers["Cookie"] = cookie
        return headers

    def _looks_like_cloudflare_block(self, html: str, status_code: int) -> bool:
        if status_code == 403:
            return True
        text = (html or "").casefold()
        return "just a moment" in text and "cloudflare" in text

    def _get(self, url: str) -> cffi_requests.Response:
        try:
            r = cffi_requests.get(
                url,
                headers=self._request_headers(),
                cookies=self._site_cookies(),
                impersonate=self.IMPERSONATE,
                timeout=30,
            )
        except Exception as e:
            raise ScraperError(f"Request failed for {url}: {e}") from e

        if self._looks_like_cloudflare_block(r.text, r.status_code):
            raise ScraperError("ManhuaTop blocked the catalog request with Cloudflare.")
        if r.status_code != 200:
            raise ScraperError(f"Failed to load catalog page: {url} ({r.status_code})")
        return r

    # ------------------------------------------------------------------ #
    # Display name
    # ------------------------------------------------------------------ #

    def get_display_name(self) -> str:
        return "ManhuaTop"

    # ------------------------------------------------------------------ #
    # Catalog parsing
    # ------------------------------------------------------------------ #

    def _slug_from_url(self, url: str) -> str:
        path = urlparse(url).path.strip("/")
        return path.split("/")[-1] if path else ""

    def _parse_latest_chapter(self, card) -> str | None:
        # First chapter-item link in the card is the most recent chapter
        link = card.select_one(".list-chapter .chapter-item .chapter a")
        if link:
            text = link.get_text(" ", strip=True)
            return text if text else None
        return None

    def _parse_cards(self, soup: BeautifulSoup) -> list[CatalogSeries]:
        entries = []
        for card in soup.select(".page-item-detail"):
            # Title and URL
            title_link = card.select_one(".post-title a")
            if not title_link:
                continue
            title = title_link.get_text(" ", strip=True)
            url = (title_link.get("href") or "").strip()
            if not title or not url:
                continue

            slug = self._slug_from_url(url)
            if not slug:
                continue

            # Cover image — prefer srcset first entry, fall back to src
            cover_url = None
            img = card.select_one(".item-thumb img")
            if img:
                # srcset first entry is highest resolution available in the card
                srcset = img.get("srcset", "")
                if srcset:
                    cover_url = srcset.split(",")[0].strip().split(" ")[0]
                if not cover_url:
                    cover_url = (img.get("src") or "").strip() or None

            # Latest chapter
            latest_chapter = self._parse_latest_chapter(card)

            entries.append(
                CatalogSeries(
                    site=self.site_name,
                    series_id=slug,
                    title=title,
                    url=url,
                    cover_url=cover_url or None,
                    cover_headers=self._cover_headers(),
                    latest_chapter=latest_chapter,
                )
            )

        return entries

    def _has_next_page(self, soup: BeautifulSoup, current_page: int) -> bool:
        # Madara uses /page/N/ URL pattern. Check for a next-page link.
        for a in soup.select(".nav-links a, .wp-pagenavi a"):
            href = (a.get("href") or "").strip()
            text = a.get_text(" ", strip=True).lower()
            if "next" in text or f"/page/{current_page + 1}/" in href:
                return True
        # Fallback: if the page has a full set of cards, assume more pages exist
        cards = soup.select(".page-item-detail")
        return len(cards) >= 18

    # ------------------------------------------------------------------ #
    # Search
    # ------------------------------------------------------------------ #

    def _search_url(self, search_query: str, page: int) -> str:
        # Madara search uses /?s=<query>&post_type=wp-manga
        query = search_query.strip().replace(" ", "+")
        base = f"{self.BASE}/?s={query}&post_type=wp-manga"
        if page > 1:
            base += f"&paged={page}"
        return base

    def _parse_search_cards(self, soup: BeautifulSoup) -> list[CatalogSeries]:
        # Search results use .c-tabs-item instead of .page-item-detail
        entries = []
        for card in soup.select(".c-tabs-item"):
            title_link = card.select_one(".post-title a")
            if not title_link:
                continue
            title = title_link.get_text(" ", strip=True)
            url = (title_link.get("href") or "").strip()
            if not title or not url:
                continue

            slug = self._slug_from_url(url)
            if not slug:
                continue

            # Cover
            cover_url = None
            img = card.select_one(".tab-thumb img")
            if img:
                cover_url = (img.get("src") or "").strip() or None

            # Latest chapter
            latest_chapter = None
            chap_link = card.select_one(".meta-item.latest-chap .chapter a")
            if chap_link:
                latest_chapter = chap_link.get_text(" ", strip=True) or None

            # Author — skip "N/A" placeholders
            author = None
            author_node = card.select_one(".mg_author .summary-content")
            if author_node:
                text = author_node.get_text(" ", strip=True)
                if text and text.upper() != "N/A":
                    author = text

            entries.append(
                CatalogSeries(
                    site=self.site_name,
                    series_id=slug,
                    title=title,
                    url=url,
                    cover_url=cover_url,
                    cover_headers=self._cover_headers(),
                    latest_chapter=latest_chapter,
                    author=author,
                )
            )
        return entries

    # ------------------------------------------------------------------ #
    # Main entry point
    # ------------------------------------------------------------------ #

    def get_catalog_page(self, page: int = 1, search_query: str = "") -> CatalogPage:
        page = max(1, int(page))
        query = str(search_query or "").strip()

        if query:
            url = self._search_url(query, page)
        else:
            url = f"{self.BASE}/manga/page/{page}/"

        r = self._get(url)
        soup = BeautifulSoup(r.text, "html.parser")

        # Search results use a different card structure than the catalog
        if query:
            entries = self._parse_search_cards(soup)
            has_next = len(entries) >= 18
        else:
            entries = self._parse_cards(soup)
            has_next = self._has_next_page(soup, page)

        return CatalogPage(
            site=self.site_name,
            page=page,
            entries=entries,
            has_next_page=has_next,
        )
    
    def fetch_cover(self, url: str, headers: dict[str, str]) -> bytes | None:
        try:
            r = cffi_requests.get(
                url,
                headers=self._request_headers(),
                cookies=self._site_cookies(),
                impersonate=self.IMPERSONATE,
                timeout=20,
            )
            if r.status_code == 200:
                return r.content
        except Exception:
            pass
        return None