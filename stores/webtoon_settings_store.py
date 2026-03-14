import io
import json
from pathlib import Path
import urllib.error
import urllib.request
from urllib.parse import urlparse

from core.app_logging import get_logger
from core.app_paths import data_path
from core.site_session import load_site_user_agent, site_base_url, site_cookie_header, site_name_for_url
from stores.db import get_connection


THUMBNAILS_DIR = data_path("thumbnails")
logger = get_logger(__name__)

_instance = None


def get_instance() -> "WebtoonSettingsStore":
    global _instance
    if _instance is None:
        _instance = WebtoonSettingsStore()
    return _instance


def _ensure_thumbnails_dir():
    THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)


def _sanitize_name(webtoon_name: str) -> str:
    keep = set(" ._-()[]abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    return "".join(c if c in keep else "_" for c in webtoon_name)


def _custom_thumb_path(webtoon_name: str) -> Path:
    return THUMBNAILS_DIR / f"{_sanitize_name(webtoon_name)}_custom.jpg"


def _auto_thumb_path(webtoon_name: str) -> Path:
    return THUMBNAILS_DIR / f"{webtoon_name}.jpg"


def _copy_local_image(src: str, dest: Path) -> bool:
    try:
        from PIL import Image

        with Image.open(src) as img:
            img.convert("RGB").save(dest, "JPEG", quality=92)
        return True
    except Exception as e:
        logger.error("Failed to copy local image '%s'", src, exc_info=e)
        return False


def _download_url_image(url: str, dest: Path) -> bool:
    try:
        from PIL import Image

        headers = _thumbnail_request_headers(url)
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            data = response.read()

        with Image.open(io.BytesIO(data)) as img:
            img.convert("RGB").save(dest, "JPEG", quality=92)
        return True
    except urllib.error.URLError as e:
        logger.error("Network error downloading thumbnail from '%s'", url, exc_info=e)
        return False
    except Exception as e:
        logger.error("Failed to download or convert image from '%s'", url, exc_info=e)
        return False




def _thumbnail_request_headers(url: str) -> dict[str, str]:
    headers = {"User-Agent": "Mozilla/5.0 (WebtoonReader/1.0)"}
    site_name = site_name_for_url(url)
    if not site_name:
        return headers

    headers["User-Agent"] = load_site_user_agent(site_name, headers["User-Agent"])
    referer = site_base_url(site_name)
    if referer:
        headers["Referer"] = referer
    cookie_header = site_cookie_header(site_name)
    if cookie_header:
        headers["Cookie"] = cookie_header
    return headers

class WebtoonSettingsStore:

    def _ensure_row(self, conn, webtoon_name: str):
        conn.execute(
            "INSERT OR IGNORE INTO webtoon_settings (webtoon_name) VALUES (?)",
            (webtoon_name,),
        )

    def _get_scalar(self, webtoon_name: str, column: str, *, default=None, coerce=None):
        conn = get_connection()
        row = conn.execute(
            f"SELECT {column} FROM webtoon_settings WHERE webtoon_name = ?",
            (webtoon_name,),
        ).fetchone()
        if row is None:
            return default
        value = row[column]
        if value is None or value == "":
            return default
        return coerce(value) if coerce is not None else value

    def _set_scalar(self, webtoon_name: str, column: str, value, *, log_message: str | None = None):
        if log_message:
            logger.info(log_message, webtoon_name, value)
        conn = get_connection()
        self._ensure_row(conn, webtoon_name)
        conn.execute(
            f"UPDATE webtoon_settings SET {column} = ? WHERE webtoon_name = ?",
            (value, webtoon_name),
        )
        conn.commit()

    def _clear_scalar(self, webtoon_name: str, column: str, *, log_message: str | None = None):
        if log_message:
            logger.info(log_message, webtoon_name)
        conn = get_connection()
        conn.execute(
            f"UPDATE webtoon_settings SET {column} = NULL WHERE webtoon_name = ?",
            (webtoon_name,),
        )
        conn.commit()

    def get_hide_filler(self, webtoon_name: str) -> bool:
        return bool(self._get_scalar(webtoon_name, "hide_filler", default=0))

    def get_completed(self, webtoon_name: str) -> bool:
        return bool(self._get_scalar(webtoon_name, "completed", default=0))

    def get_bookmarked(self, webtoon_name: str) -> bool:
        return bool(self._get_scalar(webtoon_name, "bookmarked", default=0))

    def set_hide_filler(self, webtoon_name: str, value: bool):
        self._set_scalar(webtoon_name, "hide_filler", int(value), log_message="Setting hide_filler for %s to %s")

    def set_completed(self, webtoon_name: str, value: bool):
        self._set_scalar(webtoon_name, "completed", int(value), log_message="Setting completed for %s to %s")

    def set_bookmarked(self, webtoon_name: str, value: bool):
        self._set_scalar(webtoon_name, "bookmarked", int(value), log_message="Setting bookmarked for %s to %s")

    def toggle_completed(self, webtoon_name: str) -> bool:
        completed = not self.get_completed(webtoon_name)
        self.set_completed(webtoon_name, completed)
        return completed

    def toggle_bookmarked(self, webtoon_name: str) -> bool:
        bookmarked = not self.get_bookmarked(webtoon_name)
        self.set_bookmarked(webtoon_name, bookmarked)
        return bookmarked

    def get_bookmarked_chapters(self, webtoon_name: str) -> set[str]:
        payload = self._get_scalar(webtoon_name, "bookmarked_chapters")
        if not payload:
            return set()

        try:
            data = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            return set()

        if not isinstance(data, list):
            return set()
        return {str(chapter) for chapter in data}

    def set_bookmarked_chapters(self, webtoon_name: str, chapters: set[str] | list[str]):
        logger.info("Saving %d bookmarked chapters for %s", len(set(chapters)), webtoon_name)
        payload = json.dumps(sorted({str(chapter) for chapter in chapters}))
        self._set_scalar(webtoon_name, "bookmarked_chapters", payload)

    def toggle_bookmarked_chapter(self, webtoon_name: str, chapter: str) -> bool:
        bookmarks = self.get_bookmarked_chapters(webtoon_name)
        if chapter in bookmarks:
            bookmarks.remove(chapter)
            is_bookmarked = False
        else:
            bookmarks.add(chapter)
            is_bookmarked = True

        self.set_bookmarked_chapters(webtoon_name, bookmarks)
        return is_bookmarked

    def get_zoom_override(self, webtoon_name: str) -> float | None:
        return self._get_scalar(webtoon_name, "zoom_override", default=None, coerce=float)

    def set_zoom_override(self, webtoon_name: str, zoom: float):
        self._set_scalar(webtoon_name, "zoom_override", zoom, log_message="Setting zoom override for %s to %s")

    def clear_zoom_override(self, webtoon_name: str):
        self._clear_scalar(webtoon_name, "zoom_override", log_message="Clearing zoom override for %s")

    def get_source_url(self, webtoon_name: str) -> str | None:
        return self._get_scalar(webtoon_name, "source_url", default=None, coerce=str)

    def set_source_url(self, webtoon_name: str, source_url: str):
        self._set_scalar(webtoon_name, "source_url", source_url, log_message="Saving source URL for %s: %s")

    def clear_source_url(self, webtoon_name: str):
        self._clear_scalar(webtoon_name, "source_url", log_message="Clearing source URL for %s")

    def get_source_site(self, webtoon_name: str) -> str | None:
        return self._get_scalar(webtoon_name, "source_site", default=None, coerce=str)

    def set_source_site(self, webtoon_name: str, source_site: str):
        self._set_scalar(webtoon_name, "source_site", source_site, log_message="Saving source site for %s: %s")

    def get_source_series_id(self, webtoon_name: str) -> str | None:
        return self._get_scalar(webtoon_name, "source_series_id", default=None, coerce=str)

    def set_source_series_id(self, webtoon_name: str, source_series_id: str):
        self._set_scalar(
            webtoon_name,
            "source_series_id",
            source_series_id,
            log_message="Saving source series id for %s: %s",
        )

    def get_source_title(self, webtoon_name: str) -> str | None:
        return self._get_scalar(webtoon_name, "source_title", default=None, coerce=str)

    def set_source_title(self, webtoon_name: str, source_title: str):
        self._set_scalar(webtoon_name, "source_title", source_title, log_message="Saving source title for %s: %s")

    def save_source_metadata(
        self,
        webtoon_name: str,
        *,
        source_url: str | None = None,
        source_site: str | None = None,
        source_series_id: str | None = None,
        source_title: str | None = None,
    ):
        conn = get_connection()
        self._ensure_row(conn, webtoon_name)
        conn.execute(
            """
            UPDATE webtoon_settings
            SET source_url = COALESCE(?, source_url),
                source_site = COALESCE(?, source_site),
                source_series_id = COALESCE(?, source_series_id),
                source_title = COALESCE(?, source_title)
            WHERE webtoon_name = ?
            """,
            (source_url, source_site, source_series_id, source_title, webtoon_name),
        )
        conn.commit()

    def get_category(self, webtoon_name: str) -> str | None:
        return self._get_scalar(webtoon_name, "category", default=None, coerce=str)

    def set_category(self, webtoon_name: str, category: str):
        normalized = str(category).strip()
        self._set_scalar(webtoon_name, "category", normalized, log_message="Setting category for %s to %s")

    def clear_category(self, webtoon_name: str):
        self._clear_scalar(webtoon_name, "category", log_message="Clearing category for %s")

    def get_last_update_at(self, webtoon_name: str) -> int | None:
        return self._get_scalar(webtoon_name, "last_update_at", default=None, coerce=int)

    def set_last_update_at(self, webtoon_name: str, timestamp: int):
        self._set_scalar(webtoon_name, "last_update_at", int(timestamp), log_message="Setting last update timestamp for %s to %s")

    def get_latest_new_chapter(self, webtoon_name: str) -> str | None:
        return self._get_scalar(webtoon_name, "latest_new_chapter", default=None, coerce=str)

    def set_latest_new_chapter(self, webtoon_name: str, chapter: str):
        self._set_scalar(webtoon_name, "latest_new_chapter", str(chapter), log_message="Setting latest new chapter for %s to %s")

    def clear_latest_new_chapter(self, webtoon_name: str):
        self._clear_scalar(webtoon_name, "latest_new_chapter", log_message="Clearing latest new chapter for %s")

    def get(self, webtoon_name: str) -> str | None:
        return self._get_scalar(webtoon_name, "custom_thumbnail", default=None)

    def set(self, webtoon_name: str, image_path: str) -> str:
        logger.info("Saving custom thumbnail for %s from %s", webtoon_name, image_path)
        _ensure_thumbnails_dir()
        dest = _custom_thumb_path(webtoon_name)

        if not _copy_local_image(image_path, dest):
            internal_path = image_path
        else:
            internal_path = str(dest)

        self._persist_custom_thumbnail(webtoon_name, internal_path)
        return internal_path

    def set_from_url(self, webtoon_name: str, url: str) -> tuple[bool, str]:
        logger.info("Downloading custom thumbnail for %s from %s", webtoon_name, url)
        _ensure_thumbnails_dir()
        dest = _custom_thumb_path(webtoon_name)

        if _download_url_image(url, dest):
            self._persist_custom_thumbnail(webtoon_name, str(dest))
            return True, str(dest)
        return False, "Could not download or decode the image. Check the URL and try again."

    def clear(self, webtoon_name: str):
        logger.info("Clearing custom thumbnail for %s", webtoon_name)
        dest = _custom_thumb_path(webtoon_name)
        if dest.exists():
            try:
                dest.unlink()
            except OSError as e:
                logger.warning("Could not delete cached thumbnail for %s", webtoon_name, exc_info=e)

        self._clear_scalar(webtoon_name, "custom_thumbnail")

    def rename_webtoon(self, old_name: str, new_name: str):
        logger.info("Renaming settings from %s to %s", old_name, new_name)
        old_custom = _custom_thumb_path(old_name)
        new_custom = _custom_thumb_path(new_name)
        old_auto = _auto_thumb_path(old_name)
        new_auto = _auto_thumb_path(new_name)

        custom_path = self.get(old_name)
        if old_custom.exists():
            _ensure_thumbnails_dir()
            if new_custom.exists():
                new_custom.unlink()
            old_custom.rename(new_custom)
            custom_path = str(new_custom)

        if old_auto.exists():
            _ensure_thumbnails_dir()
            if new_auto.exists():
                new_auto.unlink()
            old_auto.rename(new_auto)

        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM webtoon_settings WHERE webtoon_name = ?",
            (old_name,),
        ).fetchone()
        if row is None:
            return

        conn.execute(
            """INSERT OR REPLACE INTO webtoon_settings
               (webtoon_name, hide_filler, completed, bookmarked, zoom_override, custom_thumbnail, source_url, source_site, source_series_id, source_title, category, bookmarked_chapters, last_update_at, latest_new_chapter)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_name,
                row["hide_filler"],
                row["completed"],
                row["bookmarked"],
                row["zoom_override"],
                custom_path,
                row["source_url"],
                row["source_site"],
                row["source_series_id"],
                row["source_title"],
                row["category"],
                row["bookmarked_chapters"],
                row["last_update_at"],
                row["latest_new_chapter"],
            ),
        )
        conn.execute(
            "DELETE FROM webtoon_settings WHERE webtoon_name = ?",
            (old_name,),
        )
        conn.commit()

    def delete_webtoon(self, webtoon_name: str):
        logger.info("Deleting settings for %s", webtoon_name)
        self.clear(webtoon_name)

        auto_thumb = _auto_thumb_path(webtoon_name)
        if auto_thumb.exists():
            try:
                auto_thumb.unlink()
            except OSError as e:
                logger.warning("Could not delete auto thumbnail for %s", webtoon_name, exc_info=e)

        conn = get_connection()
        conn.execute(
            "DELETE FROM webtoon_settings WHERE webtoon_name = ?",
            (webtoon_name,),
        )
        conn.commit()

    def _persist_custom_thumbnail(self, webtoon_name: str, path: str):
        self._set_scalar(webtoon_name, "custom_thumbnail", path)
