import json
import re
import time
from urllib.parse import urlparse

import requests

from ..base import BaseScraper, ScraperError
from ..models import ChapterInfo, PageInfo, ScraperConfigField, ScraperConfigOption, SeriesInfo


class HitomiScraper(BaseScraper):
    site_name = "hitomi"
    site_display_name = "Hitomi"
    content_type = "manga"
    site_hosts = ("hitomi.la", "www.hitomi.la")
    site_base_url = "https://hitomi.la/"

    BASE = "https://hitomi.la"
    CDN_BASE = "https://ltn.gold-usergeneratedcontent.net"
    asset_download_workers = 3

    LANGUAGE_OPTIONS = (
        ScraperConfigOption("all", "All Languages"),
        ScraperConfigOption("english", "English"),
        ScraperConfigOption("japanese", "Japanese"),
        ScraperConfigOption("chinese", "Chinese"),
        ScraperConfigOption("korean", "Korean"),
        ScraperConfigOption("spanish", "Spanish"),
        ScraperConfigOption("french", "French"),
        ScraperConfigOption("portuguese", "Portuguese"),
        ScraperConfigOption("thai", "Thai"),
        ScraperConfigOption("vietnamese", "Vietnamese"),
        ScraperConfigOption("german", "German"),
        ScraperConfigOption("italian", "Italian"),
        ScraperConfigOption("russian", "Russian"),
    )
    source_config_fields = (
        ScraperConfigField(
            key="languages",
            label="Languages",
            control="multi_select",
            options=list(LANGUAGE_OPTIONS),
            default=["all"],
            description="Choose which Hitomi gallery languages should appear in discovery.",
        ),
    )

    GALLERY_PATH_RE = re.compile(
        r"^/(?:doujinshi|manga|cg|imageset|gamecg|artistcg)/.+-(\d+)(?:\.html)?/?$",
        re.IGNORECASE,
    )
    GALLERY_INFO_RE = re.compile(r"var\s+galleryinfo\s*=\s*(\{.*\})\s*;?\s*$", re.DOTALL)
    GG_B_RE = re.compile(r"b:\s*'([^']+)'")
    GG_ZERO_SECTION_RE = re.compile(r"switch\s*\(g\)\s*\{(.*?)o\s*=\s*0;\s*break;", re.DOTALL)
    GG_CASE_RE = re.compile(r"case\s+(\d+)\s*:")

    def __init__(self):
        super().__init__()
        self._gg_base_path: str | None = None
        self._gg_zero_values: set[int] | None = None
        self._asset_candidates: dict[str, list[str]] = {}

    @classmethod
    def normalize_source_config(cls, config: dict | None) -> dict:
        normalized = super().normalize_source_config(config)
        languages = [
            str(language or "").strip().casefold()
            for language in normalized.get("languages", [])
            if str(language or "").strip()
        ]
        if not languages or "all" in languages:
            normalized["languages"] = ["all"]
        else:
            normalized["languages"] = list(dict.fromkeys(languages))
        return normalized

    def selected_languages(self) -> list[str]:
        languages = self.get_source_config_value("languages", ["all"])
        if not isinstance(languages, list):
            return ["all"]
        normalized = [
            str(language or "").strip().casefold()
            for language in languages
            if str(language or "").strip()
        ]
        if not normalized or "all" in normalized:
            return ["all"]
        return list(dict.fromkeys(normalized))

    @classmethod
    def can_handle(cls, url: str) -> bool:
        parsed = urlparse(url)
        host = (parsed.netloc or "").casefold()
        if "hitomi.la" not in host:
            return False
        return cls.GALLERY_PATH_RE.match(parsed.path or "") is not None

    def get_request_headers(self, url: str) -> dict:
        return {
            "User-Agent": "Mozilla/5.0",
            "Referer": self.BASE + "/",
            "Origin": self.BASE,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def get_series_info(self, url: str, session=None) -> SeriesInfo:
        gallery_url = self._normalize_gallery_url(url)
        gallery_id = self._extract_gallery_id(gallery_url)
        info = self._fetch_galleryinfo(gallery_id, session=session)

        title = str(info.get("japanese_title") or info.get("title") or f"Gallery {gallery_id}").strip()
        artists = info.get("artists") or []
        author = ", ".join(
            str(artist.get("artist") or "").strip()
            for artist in artists
            if str(artist.get("artist") or "").strip()
        ) or None
        description_bits = []
        gallery_type = str(info.get("type") or "").strip()
        language = str(info.get("language_localname") or info.get("language") or "").strip()
        if gallery_type:
            description_bits.append(gallery_type)
        if language:
            description_bits.append(language)
        parodys = info.get("parodys") or []
        series_names = [
            str(item.get("parody") or "").strip()
            for item in parodys
            if str(item.get("parody") or "").strip()
        ]
        if series_names:
            description_bits.append("Series: " + ", ".join(series_names))
        description = " | ".join(description_bits) or None

        files = info.get("files") or []
        chapters = [
            ChapterInfo(
                id=str(gallery_id),
                number=1,
                title="Chapter 1",
                url=gallery_url,
            )
        ]
        cover_url = self._build_primary_image_url(files[0], session=session) if files else None

        return SeriesInfo(
            site=self.site_name,
            series_id=str(gallery_id),
            title=title,
            url=gallery_url,
            content_type=self.content_type,
            cover_url=cover_url,
            author=author,
            description=description,
            total_chapters=1,
            chapters=chapters,
        )

    def get_chapter_pages(self, chapter_url: str, session=None) -> list[PageInfo]:
        gallery_url = self._normalize_gallery_url(chapter_url)
        gallery_id = self._extract_gallery_id(gallery_url)
        info = self._fetch_galleryinfo(gallery_id, session=session)
        files = info.get("files") or []
        pages: list[PageInfo] = []
        for index, image in enumerate(files, start=1):
            image_url = self._build_primary_image_url(image, session=session)
            if not image_url:
                continue
            pages.append(PageInfo(index=index, image_url=image_url))
        return pages

    def is_chapter_url(self, url: str) -> bool:
        return False

    def series_url_from_chapter_url(self, url: str) -> str:
        return self._normalize_gallery_url(url)

    def download_asset(self, url: str, dest_path: str) -> bool:
        data = self._fetch_binary(url, candidate_urls=self._asset_candidates.get(url))
        if not data:
            return False
        with open(dest_path, "wb") as handle:
            handle.write(data)
        return True

    def fetch_cover(self, url: str, headers: dict[str, str] | None = None) -> bytes | None:
        return self._fetch_binary(url, headers=headers, candidate_urls=self._asset_candidates.get(url))

    def _normalize_gallery_url(self, url: str) -> str:
        parsed = urlparse(url)
        if not self.GALLERY_PATH_RE.match(parsed.path or ""):
            raise ScraperError(f"Unsupported Hitomi gallery URL: {url}")
        return f"{parsed.scheme or 'https'}://{parsed.netloc}{parsed.path}"

    def _extract_gallery_id(self, url: str) -> str:
        parsed = urlparse(url)
        match = self.GALLERY_PATH_RE.match(parsed.path or "")
        if not match:
            raise ScraperError(f"Could not determine Hitomi gallery id from: {url}")
        return match.group(1)

    def _fetch_galleryinfo(self, gallery_id: str, session=None) -> dict:
        script_url = f"{self.CDN_BASE}/galleries/{gallery_id}.js"
        client = session or requests
        response = client.get(script_url, headers=self.get_request_headers(script_url), timeout=20)
        response.raise_for_status()
        text = str(response.text or "").strip()
        match = self.GALLERY_INFO_RE.search(text)
        if not match:
            raise ScraperError("Could not parse Hitomi gallery metadata.")
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise ScraperError("Hitomi gallery metadata was invalid JSON.") from exc

    def _gg_base(self, session=None) -> str:
        if self._gg_base_path is not None and self._gg_zero_values is not None:
            return self._gg_base_path
        client = session or requests
        response = client.get(f"{self.CDN_BASE}/gg.js", headers=self.get_request_headers(self.BASE), timeout=20)
        response.raise_for_status()
        text = str(response.text or "")
        match = self.GG_B_RE.search(text)
        if not match:
            raise ScraperError("Could not determine Hitomi image path base.")
        self._gg_base_path = match.group(1)
        section_match = self.GG_ZERO_SECTION_RE.search(text)
        zero_values: set[int] = set()
        if section_match:
            zero_values = {int(value) for value in self.GG_CASE_RE.findall(section_match.group(1))}
        self._gg_zero_values = zero_values
        return self._gg_base_path

    def _webp_host_for_hash(self, image_hash: str, session=None) -> str:
        match = re.search(r"(..)(.)$", str(image_hash or ""))
        if not match:
            raise ScraperError("Hitomi image hash was invalid.")
        g_value = int(match.group(2) + match.group(1), 16)
        self._gg_base(session=session)
        zero_values = self._gg_zero_values or set()
        suffix = 1 if g_value in zero_values else 2
        return f"w{suffix}.gold-usergeneratedcontent.net"

    def _full_path_from_hash(self, image_hash: str, session=None) -> str:
        match = re.search(r"(..)(.)$", str(image_hash or ""))
        if not match:
            raise ScraperError("Hitomi image hash was invalid.")
        suffix = int(match.group(2) + match.group(1), 16)
        return f"{self._gg_base(session=session)}{suffix}/{image_hash}"

    def _build_primary_image_url(self, image: dict, session=None) -> str:
        candidates = self._build_image_candidates(image, session=session)
        if not candidates:
            return ""
        primary_url = candidates[0]
        self._asset_candidates[primary_url] = candidates
        return primary_url

    def _build_image_candidates(self, image: dict, session=None) -> list[str]:
        image_hash = str((image or {}).get("hash") or "").strip()
        name = str((image or {}).get("name") or "").strip()
        if not image_hash or not name or "." not in name:
            return []
        path = self._full_path_from_hash(image_hash, session=session)
        host = self._webp_host_for_hash(image_hash, session=session)
        candidates = [f"https://{host}/{path}.webp"]

        # Preserve order while dropping duplicates.
        return list(dict.fromkeys(candidates))

    def _download_candidates(self, url: str) -> list[str]:
        parsed = urlparse(url)
        host = str(parsed.netloc or "")
        if host.startswith("w1.gold-usergeneratedcontent.net"):
            return [url, url.replace("://w1.gold-usergeneratedcontent.net/", "://w2.gold-usergeneratedcontent.net/", 1)]
        if host.startswith("w2.gold-usergeneratedcontent.net"):
            return [url, url.replace("://w2.gold-usergeneratedcontent.net/", "://w1.gold-usergeneratedcontent.net/", 1)]
        if host.startswith("1.gold-usergeneratedcontent.net"):
            return [url, url.replace("://1.gold-usergeneratedcontent.net/", "://2.gold-usergeneratedcontent.net/", 1)]
        if host.startswith("2.gold-usergeneratedcontent.net"):
            return [url, url.replace("://2.gold-usergeneratedcontent.net/", "://1.gold-usergeneratedcontent.net/", 1)]
        return [url]

    def _fetch_binary(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        candidate_urls: list[str] | None = None,
    ) -> bytes | None:
        candidates = list(dict.fromkeys((candidate_urls or []) + self._download_candidates(url)))
        rounds = 4
        for attempt in range(rounds):
            for candidate in candidates:
                response = None
                try:
                    request_headers = dict(headers or self.get_request_headers(candidate))
                    response = requests.get(candidate, headers=request_headers, timeout=45)
                    if response.status_code == 200 and response.content:
                        return response.content
                except Exception:
                    pass
                finally:
                    if response is not None:
                        response.close()
            if attempt < rounds - 1:
                time.sleep(0.35 * (attempt + 1))
        return None
