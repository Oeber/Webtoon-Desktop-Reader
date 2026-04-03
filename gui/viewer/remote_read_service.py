from __future__ import annotations

import json
import os
import tempfile
from html import escape
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

from core.app_logging import get_logger
from core.chapter_identity import build_remote_chapter_key
from core.http_client import create_session, get
from core.remote_cache import (
    cached_chapter_metadata_path,
    cached_chapter_root,
    tracked_title_cache_root,
    tracked_title_metadata_path,
    write_json_atomic,
)
from bs4 import BeautifulSoup
from scrapers.base import ScraperError
from stores.tracked_titles_store import get_instance as get_tracked_titles_store


logger = get_logger(__name__)


class RemoteReadService:
    def __init__(self, progress_store):
        self.progress_store = progress_store
        self.tracked_titles_store = get_tracked_titles_store()

    def prepare_chapter(self, scraper, series, chapter):
        site_name = str(getattr(scraper, "site_name", "") or getattr(series, "site", "") or "unknown").strip()
        series_id = str(getattr(series, "series_id", "") or getattr(series, "url", "") or getattr(series, "title", "") or "series").strip()
        chapter_id = str(getattr(chapter, "id", "") or "").strip()
        chapter_title = str(getattr(chapter, "title", "") or getattr(chapter, "url", "") or chapter_id or "Chapter").strip()
        chapter_key = build_remote_chapter_key(site_name, series_id, chapter_id, getattr(chapter, "url", ""))
        track_id = self._track_id(site_name, series_id)
        chapter_folder_name = self._chapter_folder_name(chapter_key)
        chapter_root = cached_chapter_root(track_id, chapter_key)
        metadata_path = cached_chapter_metadata_path(track_id, chapter_key)

        content_type = str(getattr(series, "content_type", "webtoon") or "webtoon").strip() or "webtoon"
        image_files = self._cached_image_files(chapter_root)
        has_text_payload = self._has_cached_text_payload(chapter_root)
        if content_type == "webnovel":
            if not has_text_payload:
                content = self._scraper_get_chapter_content(scraper, chapter.url)
                self._cache_text_content(track_id, chapter_key, series, chapter, content)
                has_text_payload = self._has_cached_text_payload(chapter_root)
            if not has_text_payload:
                raise ScraperError(f"No readable text found for: {chapter.url}")
        else:
            if not image_files:
                pages = list(getattr(chapter, "pages", []) or [])
                if not pages:
                    pages = self._scraper_get_chapter_pages(scraper, chapter.url)
                if not pages:
                    raise ScraperError(f"No readable pages found for: {chapter.url}")
                self._cache_pages(scraper, track_id, chapter_key, pages)
                image_files = self._cached_image_files(chapter_root)
            if not image_files:
                raise ScraperError(f"No cached images were prepared for: {chapter.url}")

        write_json_atomic(
            tracked_title_metadata_path(track_id),
            {
                "track_id": track_id,
                "site_name": site_name,
                "series_id": series_id,
                "title": str(getattr(series, "title", "") or "").strip(),
                "source_url": str(getattr(series, "url", "") or "").strip(),
                "content_type": content_type,
                "cover_url": str(getattr(series, "cover_url", "") or "").strip(),
            },
        )

        self.tracked_titles_store.upsert_title(
            track_id=track_id,
            site_name=site_name,
            series_id=series_id,
            title=str(getattr(series, "title", "") or "").strip(),
            source_url=str(getattr(series, "url", "") or "").strip(),
            content_type=content_type,
            cover_url=str(getattr(series, "cover_url", "") or "").strip(),
            status="tracked",
            cache_status="cached",
            last_read_chapter_key=chapter_key,
        )
        metadata_payload = {
            "chapter_key": chapter_key,
            "site_name": site_name,
            "series_id": series_id,
            "remote_chapter_id": chapter_id,
            "remote_url": str(getattr(chapter, "url", "") or "").strip(),
            "chapter_title": chapter_title,
            "chapter_number": getattr(chapter, "number", None),
            "content_type": content_type,
            "page_count": len(image_files),
        }
        if content_type == "webnovel" and metadata_path.is_file():
            try:
                existing_payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            except Exception:
                existing_payload = {}
            if isinstance(existing_payload, dict):
                existing_payload.update(metadata_payload)
                metadata_payload = existing_payload
        write_json_atomic(metadata_path, metadata_payload)

        progress = self.progress_store.get_by_chapter_key(chapter_key) or {}
        start_scroll = float(progress.get("scroll") or 0.0)
        cache_series_root = tracked_title_cache_root(track_id) / "chapters"
        fake_name = str(getattr(series, "title", "") or chapter_title or "Remote Title").strip()
        viewer_title = f"{fake_name} [Remote]"
        webtoon = SimpleNamespace(
            name=viewer_title,
            path=str(cache_series_root),
            chapters=[chapter_folder_name],
            chapter_keys={chapter_folder_name: chapter_key},
            chapter_display_names={chapter_folder_name: chapter_title},
            thumbnail=str(getattr(series, "cover_url", "") or ""),
            content_type=content_type,
            is_remote=True,
            remote_track_id=track_id,
            remote_series_url=str(getattr(series, "url", "") or "").strip(),
            remote_chapter_url=str(getattr(chapter, "url", "") or "").strip(),
        )
        return webtoon, 0, start_scroll

    def _cache_pages(self, scraper, track_id: str, chapter_key: str, pages: list) -> None:
        chapter_root = cached_chapter_root(track_id, chapter_key)
        parent = chapter_root.parent
        temp_root = Path(tempfile.mkdtemp(prefix="remote-read-", dir=str(parent)))
        session = create_session(site_name=str(getattr(scraper, "site_name", "") or ""))
        try:
            for page_number, page in enumerate(pages, start=1):
                image_url = str(getattr(page, "image_url", "") or "").strip()
                if not image_url:
                    continue
                extension = self._image_extension(image_url)
                dest_path = temp_root / f"{page_number:03d}{extension}"
                downloaded = False
                try:
                    downloaded = bool(scraper.download_asset(image_url, str(dest_path)))
                except Exception:
                    logger.debug("Scraper download_asset failed for %s", image_url, exc_info=True)
                    downloaded = False
                if not downloaded:
                    headers = {}
                    try:
                        headers = dict(scraper.get_request_headers(image_url) or {})
                    except Exception:
                        logger.debug("Request header build failed for %s", image_url, exc_info=True)
                    response = get(image_url, session=session, headers=headers, timeout=30, stream=False, log_label="remote-read")
                    if getattr(response, "status_code", 0) != 200:
                        raise ScraperError(f"Could not fetch chapter page: {image_url}")
                    dest_path.write_bytes(response.content)
            metadata_file = temp_root / "chapter.json"
            if not any(path.is_file() and path.name != metadata_file.name for path in temp_root.iterdir()):
                raise ScraperError("No chapter images were cached for remote reading.")
            if chapter_root.exists():
                for existing in chapter_root.iterdir():
                    if existing.is_file():
                        existing.unlink(missing_ok=True)
                    elif existing.is_dir():
                        import shutil
                        shutil.rmtree(existing, ignore_errors=True)
            else:
                chapter_root.mkdir(parents=True, exist_ok=True)
            for item in temp_root.iterdir():
                item.replace(chapter_root / item.name)
        finally:
            session.close()
            if temp_root.exists():
                import shutil
                shutil.rmtree(temp_root, ignore_errors=True)

    def _cached_image_files(self, chapter_root: Path) -> list[Path]:
        if not chapter_root.exists():
            return []
        return sorted(
            [path for path in chapter_root.iterdir() if path.is_file() and path.name.lower() != "chapter.json"],
            key=lambda item: item.name.casefold(),
        )

    @staticmethod
    def _has_cached_text_payload(chapter_root: Path) -> bool:
        html_path = chapter_root / "chapter.html"
        txt_path = chapter_root / "chapter.txt"
        if html_path.is_file() or txt_path.is_file():
            return True
        json_path = chapter_root / "chapter.json"
        if not json_path.is_file():
            return False
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        if not isinstance(payload, dict):
            return False
        return bool(str(payload.get("html") or "").strip() or str(payload.get("text") or "").strip())

    def _cache_text_content(self, track_id: str, chapter_key: str, series, chapter, content) -> None:
        chapter_root = cached_chapter_root(track_id, chapter_key)
        parent = chapter_root.parent
        temp_root = Path(tempfile.mkdtemp(prefix="remote-read-", dir=str(parent)))
        try:
            html_body = str(getattr(content, "html", "") or "").strip() if content is not None else ""
            text_body = str(getattr(content, "text", "") or "").strip() if content is not None else ""
            title = str(getattr(content, "title", "") or getattr(chapter, "title", "") or getattr(chapter, "url", "") or "Chapter").strip()
            if not html_body and not text_body:
                raise ScraperError(f"No readable text found for: {getattr(chapter, 'url', '')}")
            if not html_body:
                paragraphs = [
                    f"<p>{escape(line)}</p>"
                    for line in text_body.splitlines()
                    if line.strip()
                ]
                html_body = "\n".join(paragraphs)
            if not text_body:
                text_body = BeautifulSoup(html_body, "html.parser").get_text("\n", strip=True)
            payload = {
                "format": "webnovel_chapter_v1",
                "series_title": str(getattr(series, "title", "") or "").strip(),
                "chapter_title": title,
                "source_url": str(getattr(chapter, "url", "") or "").strip(),
                "html": html_body,
                "text": text_body.rstrip(),
            }
            (temp_root / "chapter.json").write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
            if chapter_root.exists():
                for existing in chapter_root.iterdir():
                    if existing.is_file():
                        existing.unlink(missing_ok=True)
                    elif existing.is_dir():
                        import shutil
                        shutil.rmtree(existing, ignore_errors=True)
            else:
                chapter_root.mkdir(parents=True, exist_ok=True)
            for item in temp_root.iterdir():
                item.replace(chapter_root / item.name)
        finally:
            if temp_root.exists():
                import shutil
                shutil.rmtree(temp_root, ignore_errors=True)

    def _scraper_get_chapter_pages(self, scraper, chapter_url: str):
        session = create_session(site_name=str(getattr(scraper, "site_name", "") or ""))
        try:
            try:
                return scraper.get_chapter_pages(chapter_url, session=session)
            except TypeError:
                return scraper.get_chapter_pages(chapter_url)
        finally:
            session.close()

    def _scraper_get_chapter_content(self, scraper, chapter_url: str):
        getter = getattr(scraper, "get_chapter_content", None)
        if not callable(getter):
            raise ScraperError(f"This source does not expose readable text for: {chapter_url}")
        return getter(chapter_url)

    @staticmethod
    def _track_id(site_name: str, series_id: str) -> str:
        import hashlib

        payload = f"{site_name.strip().casefold()}::{series_id.strip()}"
        return hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()

    @staticmethod
    def _chapter_folder_name(chapter_key: str) -> str:
        import hashlib

        return hashlib.sha1(str(chapter_key or "").encode("utf-8", errors="ignore")).hexdigest()

    @staticmethod
    def _image_extension(image_url: str) -> str:
        suffix = Path(urlparse(str(image_url or "")).path).suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif"}:
            return suffix
        return ".jpg"
