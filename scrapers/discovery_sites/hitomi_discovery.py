import math
import re
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from core.app_logging import get_logger
from scrapers.base import ScraperError
from scrapers.discovery_base import BaseDiscoveryProvider
from scrapers.models import CatalogPage, CatalogSeries

logger = get_logger(__name__)


class HitomiDiscoveryProvider(BaseDiscoveryProvider):
    site_name = "hitomi"
    site_display_name = "Hitomi"
    site_hosts = ("hitomi.la", "www.hitomi.la")
    site_base_url = "https://hitomi.la/"

    BASE = "https://hitomi.la"
    CDN_BASE = "https://ltn.gold-usergeneratedcontent.net"
    PAGE_SIZE = 25
    BLOCK_FETCH_WORKERS = 8
    MAX_NODE_SIZE = 464
    BTREE_ORDER = 16
    INDEX_URL = f"{CDN_BASE}/n/index-all.nozomi"
    GALLERY_BLOCK_URL = f"{CDN_BASE}/galleryblock"
    GALLERIES_INDEX_VERSION_URL = f"{CDN_BASE}/galleriesindex/version"
    GALLERY_ID_RE = re.compile(r"-(\d+)(?:\.html)?/?$", re.IGNORECASE)

    HEADERS = {
        "User-Agent": "Mozilla/5.0",
        "Referer": BASE + "/",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "identity",
    }
    THUMB_HOST_PRIMARY = "tn.gold-usergeneratedcontent.net"
    THUMB_HOST_LEGACY = "tn.hitomi.la"

    def __init__(self):
        self._galleries_index_version: str | None = None

    def get_catalog_page(self, page: int = 1, search_query: str = "") -> CatalogPage:
        page = max(1, int(page))
        search_query = " ".join(str(search_query or "").split()).strip()
        logger.info("Hitomi discovery: fetching page=%d search=%r", page, search_query)

        if search_query:
            return self._search_results_page(page, search_query)

        session = requests.Session()
        try:
            gallery_ids, total_items = self._fetch_nozomi_page(session, page)
        finally:
            try:
                session.close()
            except Exception:
                pass

        entries = self._fetch_entries_for_gallery_ids(gallery_ids)
        last_page = math.ceil(total_items / self.PAGE_SIZE) if total_items > 0 else page
        return CatalogPage(
            site=self.site_name,
            page=page,
            entries=entries,
            has_next_page=page < last_page,
        )

    def _search_results_page(self, page: int, raw_query: str) -> CatalogPage:
        terms, state = self._parse_search_query(raw_query)
        matched_ids = self._search_gallery_ids(terms, state)
        start = (page - 1) * self.PAGE_SIZE
        end = start + self.PAGE_SIZE
        page_ids = matched_ids[start:end]
        entries = self._fetch_entries_for_gallery_ids(page_ids)
        logger.info(
            "Hitomi discovery: search=%r matched_ids=%d page_entries=%d",
            raw_query,
            len(matched_ids),
            len(entries),
        )
        return CatalogPage(
            site=self.site_name,
            page=page,
            entries=entries,
            has_next_page=len(matched_ids) > end,
        )

    def _parse_search_query(self, query: str) -> tuple[dict, dict]:
        parts = str(query or "").lower().strip().split()
        positive_terms: list[str] = []
        negative_terms: list[str] = []
        or_terms: list[list[str]] = [[]]
        state = {
            "area": "all",
            "tag": "index",
            "language": "all",
            "orderby": "date",
            "orderbykey": None,
            "orderbydirection": "desc",
        }

        for index, term in enumerate(parts):
            term = term.replace("_", " ")
            if re.match(r"^(?:sort|order)by(?:key|direction)?:", term):
                left_side, right_side = term.split(":", 1)
                if re.match(r"^(?:sort|order)(?:by)?key$", left_side):
                    state["orderbykey"] = re.sub(r"[^0-9a-z]", "", right_side)
                elif re.match(r"^(?:sort|order)by$", left_side) and right_side in {"popular", "popularity"}:
                    state["orderby"] = "popular"
                elif re.match(r"^(?:sort|order)by$", left_side) and right_side == "date":
                    state["orderby"] = "date"
                elif re.match(r"^(?:sort|order)by$", left_side) and right_side == "datepublished":
                    state["orderby"] = "date"
                    state["orderbykey"] = "published"
                elif re.match(r"^(?:sort|order)by$", left_side) and right_side in {"random", "rand"}:
                    state["orderbydirection"] = "random"
                elif left_side in {"orderbydirection", "sortbydirection"}:
                    state["orderbydirection"] = re.sub(r"[^0-9a-z]", "", right_side)
                continue

            if term == "or":
                continue
            or_previous = index > 0 and parts[index - 1] == "or"
            or_next = index + 1 < len(parts) and parts[index + 1] == "or"
            if or_previous or or_next:
                or_terms[-1].append(term)
                if not or_next:
                    or_terms.append([])
                continue

            if term.startswith("-"):
                negative_terms.append(term[1:])
            else:
                positive_terms.append(term)

        positive_terms.sort(key=lambda value: 0 if ":" in value else 1)
        or_terms = [group for group in or_terms if group]

        if state["orderbykey"] is None:
            state["orderbykey"] = "year" if state["orderby"] == "popular" else "added"

        return {
            "positive_terms": positive_terms,
            "negative_terms": negative_terms,
            "or_terms": or_terms,
        }, state

    def _search_gallery_ids(self, terms: dict, state: dict) -> list[int]:
        positive_terms = list(terms.get("positive_terms") or [])
        negative_terms = list(terms.get("negative_terms") or [])
        or_terms = [list(group) for group in (terms.get("or_terms") or [])]

        if not positive_terms or (":" not in positive_terms[0] and state.get("orderbykey") != "added"):
            results = self._get_galleryids_from_nozomi_state(state)
        else:
            first_term = positive_terms.pop(0)
            results = self._get_galleryids_for_term(first_term, state)

        for term_group in or_terms:
            union_ids: set[int] = set()
            for term in term_group:
                union_ids.update(self._get_galleryids_for_term(term, state))
            if union_ids:
                results = [gallery_id for gallery_id in results if gallery_id in union_ids]
            else:
                results = []

        for term in positive_terms:
            include_ids = set(self._get_galleryids_for_term(term, state))
            if not include_ids:
                return []
            results = [gallery_id for gallery_id in results if gallery_id in include_ids]

        for term in negative_terms:
            exclude_ids = set(self._get_galleryids_for_term(term, state))
            if not exclude_ids:
                continue
            results = [gallery_id for gallery_id in results if gallery_id not in exclude_ids]

        if state.get("orderbydirection") in {"asc", "ascending"}:
            results = list(reversed(results))
        elif state.get("orderbydirection") in {"rand", "random"}:
            # Keep deterministic ordering for discovery paging.
            pass
        return results

    def _get_galleryids_for_term(self, query: str, state: dict) -> list[int]:
        query = str(query or "").replace("_", " ")
        if ":" in query:
            left_side, right_side = query.split(":", 1)
            scoped_state = dict(state)
            if left_side in {"female", "male"}:
                scoped_state["area"] = "tag"
                scoped_state["tag"] = query
            elif left_side == "language":
                scoped_state["language"] = right_side
            else:
                scoped_state["area"] = left_side
                scoped_state["tag"] = right_side
            return self._get_galleryids_from_nozomi_state(scoped_state)

        data = self._btree_search_gallery_data(query)
        if data is None:
            return []
        return self._get_galleryids_from_data(*data)

    def _get_galleryids_from_nozomi_state(self, state: dict) -> list[int]:
        url = self._nozomi_address_from_state(state)
        response = requests.get(url, headers=self._request_headers(), timeout=45)
        if response.status_code != 200:
            return []
        payload = response.content or b""
        return [
            int.from_bytes(payload[offset:offset + 4], byteorder="big", signed=True)
            for offset in range(0, len(payload) - (len(payload) % 4), 4)
        ]

    def _nozomi_address_from_state(self, state: dict) -> str:
        area = str(state.get("area") or "all")
        tag = str(state.get("tag") or "index")
        language = str(state.get("language") or "all")
        orderby = str(state.get("orderby") or "date")
        orderbykey = str(state.get("orderbykey") or "added")

        if orderby != "date" or orderbykey == "published":
            if area == "all":
                return f"{self.CDN_BASE}/n/{orderby}/{orderbykey}-{language}.nozomi"
            return f"{self.CDN_BASE}/n/{area}/{orderby}/{orderbykey}/{tag}-{language}.nozomi"
        if area == "all":
            return f"{self.CDN_BASE}/n/{tag}-{language}.nozomi"
        return f"{self.CDN_BASE}/n/{area}/{tag}-{language}.nozomi"

    def _btree_search_gallery_data(self, query: str) -> tuple[int, int] | None:
        key = hashlib.sha256(query.encode("utf-8")).digest()[:4]
        node = self._get_galleries_node(0)
        while node is not None:
            if not node["keys"]:
                return None
            found, index = self._locate_key(key, node["keys"])
            if found:
                return node["datas"][index]
            subnode_addresses = node["subnode_addresses"]
            if not any(subnode_addresses):
                return None
            next_address = subnode_addresses[index]
            if next_address == 0:
                return None
            node = self._get_galleries_node(next_address)
        return None

    def _locate_key(self, key: bytes, keys: list[bytes]) -> tuple[bool, int]:
        for index, existing_key in enumerate(keys):
            cmp_result = self._compare_buffers(key, existing_key)
            if cmp_result <= 0:
                return cmp_result == 0, index
        return False, len(keys)

    def _compare_buffers(self, left: bytes, right: bytes) -> int:
        top = min(len(left), len(right))
        for index in range(top):
            if left[index] < right[index]:
                return -1
            if left[index] > right[index]:
                return 1
        return 0

    def _get_galleries_node(self, address: int) -> dict | None:
        version = self._get_galleries_index_version()
        url = f"{self.CDN_BASE}/galleriesindex/galleries.{version}.index"
        data = self._fetch_range(url, address, address + self.MAX_NODE_SIZE - 1)
        if not data:
            return None
        return self._decode_node(data)

    def _get_galleryids_from_data(self, offset: int, length: int) -> list[int]:
        if length <= 0 or length > 100000000:
            return []
        version = self._get_galleries_index_version()
        url = f"{self.CDN_BASE}/galleriesindex/galleries.{version}.data"
        data = self._fetch_range(url, offset, offset + length - 1)
        if not data or len(data) < 4:
            return []

        number_of_galleryids = int.from_bytes(data[0:4], byteorder="big", signed=True)
        expected_length = number_of_galleryids * 4 + 4
        if number_of_galleryids <= 0 or number_of_galleryids > 10000000:
            return []
        if len(data) != expected_length:
            return []

        gallery_ids = []
        position = 4
        for _ in range(number_of_galleryids):
            gallery_ids.append(int.from_bytes(data[position:position + 4], byteorder="big", signed=True))
            position += 4
        return gallery_ids

    def _decode_node(self, data: bytes) -> dict | None:
        try:
            position = 0
            number_of_keys = int.from_bytes(data[position:position + 4], byteorder="big", signed=True)
            position += 4
            keys = []
            for _ in range(number_of_keys):
                key_size = int.from_bytes(data[position:position + 4], byteorder="big", signed=True)
                position += 4
                if key_size <= 0 or key_size > 32:
                    return None
                keys.append(data[position:position + key_size])
                position += key_size

            number_of_datas = int.from_bytes(data[position:position + 4], byteorder="big", signed=True)
            position += 4
            datas = []
            for _ in range(number_of_datas):
                offset = int.from_bytes(data[position:position + 8], byteorder="big", signed=False)
                position += 8
                length = int.from_bytes(data[position:position + 4], byteorder="big", signed=True)
                position += 4
                datas.append((offset, length))

            subnode_addresses = []
            for _ in range(self.BTREE_ORDER + 1):
                subnode_addresses.append(int.from_bytes(data[position:position + 8], byteorder="big", signed=False))
                position += 8
            return {
                "keys": keys,
                "datas": datas,
                "subnode_addresses": subnode_addresses,
            }
        except Exception:
            return None

    def _get_galleries_index_version(self) -> str:
        if self._galleries_index_version:
            return self._galleries_index_version
        response = requests.get(self.GALLERIES_INDEX_VERSION_URL, headers=self._request_headers(), timeout=20)
        response.raise_for_status()
        version = str(response.text or "").strip()
        if not version:
            raise ScraperError("Could not determine Hitomi galleries index version.")
        self._galleries_index_version = version
        return version

    def _fetch_range(self, url: str, start: int, end: int) -> bytes:
        headers = self._request_headers()
        headers["Range"] = f"bytes={int(start)}-{int(end)}"
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code not in {200, 206}:
            return b""
        return response.content or b""

    def _fetch_nozomi_page(self, session: requests.Session, page: int) -> tuple[list[int], int]:
        start_byte = (page - 1) * self.PAGE_SIZE * 4
        end_byte = start_byte + (self.PAGE_SIZE * 4) - 1
        headers = self._request_headers()
        headers["Range"] = f"bytes={start_byte}-{end_byte}"
        response = session.get(self.INDEX_URL, headers=headers, timeout=30)
        if response.status_code not in {200, 206}:
            raise ScraperError(f"Hitomi catalog request failed with HTTP {response.status_code}.")

        payload = response.content or b""
        if not payload:
            return [], 0

        gallery_ids = [
            int.from_bytes(payload[offset:offset + 4], byteorder="big", signed=True)
            for offset in range(0, len(payload) - (len(payload) % 4), 4)
        ]

        total_items = len(gallery_ids)
        content_range = str(response.headers.get("Content-Range") or "").strip()
        if "/" in content_range:
            try:
                total_items = int(content_range.rsplit("/", 1)[-1]) // 4
            except Exception:
                pass

        return gallery_ids, total_items

    def _fetch_entries_for_gallery_ids(self, gallery_ids: list[int]) -> list[CatalogSeries]:
        if not gallery_ids:
            return []

        entries_by_id: dict[int, CatalogSeries] = {}
        with ThreadPoolExecutor(max_workers=min(self.BLOCK_FETCH_WORKERS, len(gallery_ids))) as pool:
            futures = {
                pool.submit(self._fetch_gallery_entry, gallery_id): gallery_id
                for gallery_id in gallery_ids
            }
            for future in as_completed(futures):
                gallery_id = futures[future]
                try:
                    entry = future.result()
                except Exception as exc:
                    logger.warning(
                        "Hitomi discovery: gallery block parse failed for %s",
                        gallery_id,
                        exc_info=exc,
                    )
                    continue
                if entry is not None:
                    entries_by_id[gallery_id] = entry

        ordered_entries = []
        seen_urls: set[str] = set()
        for gallery_id in gallery_ids:
            entry = entries_by_id.get(gallery_id)
            if entry is None or entry.url in seen_urls:
                continue
            seen_urls.add(entry.url)
            ordered_entries.append(entry)
        return ordered_entries

    def _fetch_gallery_entry(self, gallery_id: int) -> CatalogSeries | None:
        url = f"{self.GALLERY_BLOCK_URL}/{int(gallery_id)}.html"
        try:
            response = requests.get(url, headers=self._request_headers(), timeout=30)
        except Exception as exc:
            logger.warning("Hitomi discovery: gallery block fetch failed for %s", url, exc_info=exc)
            return None
        if response.status_code != 200 or not response.text:
            logger.warning(
                "Hitomi discovery: gallery block %s returned HTTP %s",
                url,
                response.status_code,
            )
            return None
        return self._entry_from_gallery_block(response.text, gallery_id)

    def _entry_from_gallery_block(self, html: str, fallback_id: int) -> CatalogSeries | None:
        soup = BeautifulSoup(html, "html.parser")
        link = soup.select_one("a[href*='-'][href$='.html']")
        if link is None:
            return None

        href = str(link.get("href") or "").strip()
        normalized_url = urljoin(self.BASE, href).rstrip("/")
        title_link = soup.select_one("h1 a[href]") or link
        title = " ".join(title_link.get_text(" ", strip=True).split()).strip()
        if not title:
            return None

        series_id = self._extract_gallery_id(normalized_url) or str(fallback_id)
        author = self._extract_author(soup)
        gallery_type = self._table_value(soup, "Type")
        language = self._table_value(soup, "Language")
        series_name = self._table_value(soup, "Series")
        page_count = self._extract_page_count(soup)
        tags = self._extract_tags(soup)
        date_label = self._extract_date_label(soup)

        description_parts = []
        if gallery_type and gallery_type.casefold() != "n/a":
            description_parts.append(gallery_type)
        if language and language.casefold() != "n/a":
            description_parts.append(language)
        if series_name and series_name.casefold() != "n/a":
            description_parts.append(f"Series: {series_name}")
        if tags:
            description_parts.append("Tags: " + ", ".join(tags[:6]))

        return CatalogSeries(
            site=self.site_name,
            series_id=series_id,
            title=title,
            url=normalized_url,
            cover_url=self._extract_cover_url(soup),
            cover_headers=self._request_headers(),
            author=author,
            description=" | ".join(description_parts) or None,
            latest_chapter=(f"{page_count} page" if page_count == 1 else f"{page_count} pages") if page_count else (date_label or gallery_type or language or "Gallery"),
            total_chapters=None,
        )

    def _extract_cover_url(self, soup: BeautifulSoup) -> str | None:
        image = soup.select_one("img[data-src], img[src]")
        if image is None:
            return None
        for attr in ("data-src", "src"):
            raw = str(image.get(attr) or "").strip()
            value = self._normalize_asset_url(raw)
            if value:
                return value
        return None

    def _extract_author(self, soup: BeautifulSoup) -> str | None:
        authors = []
        for node in soup.select(".artist-list a"):
            text = " ".join(node.get_text(" ", strip=True).split()).strip()
            if text:
                authors.append(text)
        if not authors:
            return None
        return ", ".join(dict.fromkeys(authors))

    def _extract_tags(self, soup: BeautifulSoup) -> list[str]:
        tags = []
        for node in soup.select(".relatedtags a"):
            text = " ".join(node.get_text(" ", strip=True).split()).strip()
            if text:
                tags.append(text)
            if len(tags) >= 8:
                break
        return tags

    def _extract_date_label(self, soup: BeautifulSoup) -> str | None:
        node = soup.select_one(".manga-date")
        if node is None:
            return None
        text = " ".join(node.get_text(" ", strip=True).split()).strip()
        return text or None

    def _extract_page_count(self, soup: BeautifulSoup) -> int | None:
        for label in ("Pages", "Images", "Files", "Length"):
            value = self._table_value(soup, label)
            if not value:
                continue
            match = re.search(r"(\d+)", value)
            if match is not None:
                return int(match.group(1))
        return None
    def _table_value(self, soup: BeautifulSoup, label: str) -> str | None:
        for row in soup.select("table.dj-desc tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            left = " ".join(cells[0].get_text(" ", strip=True).split()).strip().casefold()
            if left != str(label or "").strip().casefold():
                continue
            right = " ".join(cells[1].get_text(" ", strip=True).split()).strip()
            return right or None
        return None

    def _extract_gallery_id(self, url: str) -> str | None:
        match = self.GALLERY_ID_RE.search(str(url or ""))
        if match is None:
            return None
        return match.group(1)

    def _normalize_asset_url(self, raw: str) -> str:
        value = str(raw or "").strip()
        if not value:
            return ""
        if value.startswith("//"):
            value = "https:" + value
        elif value.startswith("/"):
            value = urljoin(self.BASE, value)
        elif not value.startswith("http"):
            return ""
        return self._normalize_thumb_host(value)

    def _request_headers(self) -> dict[str, str]:
        return dict(self.HEADERS)

    def fetch_cover(self, url: str, headers: dict[str, str]) -> bytes | None:
        for candidate in self._cover_candidates(url):
            try:
                response = requests.get(candidate, headers=headers or self._request_headers(), timeout=20)
            except Exception:
                continue
            if response.status_code == 200 and response.content:
                return response.content
        return None

    def _cover_candidates(self, url: str) -> list[str]:
        normalized = self._normalize_thumb_host(str(url or "").strip())
        if not normalized:
            return []
        candidates = [normalized]
        if self.THUMB_HOST_PRIMARY in normalized:
            candidates.append(normalized.replace(self.THUMB_HOST_PRIMARY, self.THUMB_HOST_LEGACY, 1))
        elif self.THUMB_HOST_LEGACY in normalized:
            candidates.append(normalized.replace(self.THUMB_HOST_LEGACY, self.THUMB_HOST_PRIMARY, 1))
        return list(dict.fromkeys(candidates))

    def _normalize_thumb_host(self, url: str) -> str:
        value = str(url or "").strip()
        if not value:
            return ""
        if self.THUMB_HOST_LEGACY in value:
            return value.replace(self.THUMB_HOST_LEGACY, self.THUMB_HOST_PRIMARY, 1)
        return value


