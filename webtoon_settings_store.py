import io
import json
from pathlib import Path
import urllib.error
import urllib.request

from app_logging import get_logger
from db import get_connection


THUMBNAILS_DIR = Path("data/thumbnails")
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

        headers = {"User-Agent": "Mozilla/5.0 (WebtoonReader/1.0)"}
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


class WebtoonSettingsStore:

    def _ensure_row(self, conn, webtoon_name: str):
        conn.execute(
            "INSERT OR IGNORE INTO webtoon_settings (webtoon_name) VALUES (?)",
            (webtoon_name,)
        )

    def get_hide_filler(self, webtoon_name: str) -> bool:
        conn = get_connection()
        row = conn.execute(
            "SELECT hide_filler FROM webtoon_settings WHERE webtoon_name = ?",
            (webtoon_name,)
        ).fetchone()
        return bool(row["hide_filler"]) if row else False

    def get_completed(self, webtoon_name: str) -> bool:
        conn = get_connection()
        row = conn.execute(
            "SELECT completed FROM webtoon_settings WHERE webtoon_name = ?",
            (webtoon_name,)
        ).fetchone()
        return bool(row["completed"]) if row else False

    def set_hide_filler(self, webtoon_name: str, value: bool):
        logger.info("Setting hide_filler for %s to %s", webtoon_name, value)
        conn = get_connection()
        self._ensure_row(conn, webtoon_name)
        conn.execute(
            "UPDATE webtoon_settings SET hide_filler = ? WHERE webtoon_name = ?",
            (int(value), webtoon_name)
        )
        conn.commit()

    def set_completed(self, webtoon_name: str, value: bool):
        logger.info("Setting completed for %s to %s", webtoon_name, value)
        conn = get_connection()
        self._ensure_row(conn, webtoon_name)
        conn.execute(
            "UPDATE webtoon_settings SET completed = ? WHERE webtoon_name = ?",
            (int(value), webtoon_name)
        )
        conn.commit()

    def toggle_completed(self, webtoon_name: str) -> bool:
        completed = not self.get_completed(webtoon_name)
        self.set_completed(webtoon_name, completed)
        return completed

    def get_bookmarked_chapters(self, webtoon_name: str) -> set[str]:
        conn = get_connection()
        row = conn.execute(
            "SELECT bookmarked_chapters FROM webtoon_settings WHERE webtoon_name = ?",
            (webtoon_name,)
        ).fetchone()
        if row is None or not row["bookmarked_chapters"]:
            return set()

        try:
            data = json.loads(row["bookmarked_chapters"])
        except (TypeError, json.JSONDecodeError):
            return set()

        if not isinstance(data, list):
            return set()
        return {str(chapter) for chapter in data}

    def set_bookmarked_chapters(self, webtoon_name: str, chapters: set[str] | list[str]):
        logger.info("Saving %d bookmarked chapters for %s", len(set(chapters)), webtoon_name)
        conn = get_connection()
        self._ensure_row(conn, webtoon_name)
        payload = json.dumps(sorted({str(chapter) for chapter in chapters}))
        conn.execute(
            "UPDATE webtoon_settings SET bookmarked_chapters = ? WHERE webtoon_name = ?",
            (payload, webtoon_name)
        )
        conn.commit()

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
        conn = get_connection()
        row = conn.execute(
            "SELECT zoom_override FROM webtoon_settings WHERE webtoon_name = ?",
            (webtoon_name,)
        ).fetchone()
        if row is None or row["zoom_override"] is None:
            return None
        return float(row["zoom_override"])

    def set_zoom_override(self, webtoon_name: str, zoom: float):
        logger.info("Setting zoom override for %s to %.2f", webtoon_name, zoom)
        conn = get_connection()
        self._ensure_row(conn, webtoon_name)
        conn.execute(
            "UPDATE webtoon_settings SET zoom_override = ? WHERE webtoon_name = ?",
            (zoom, webtoon_name)
        )
        conn.commit()

    def clear_zoom_override(self, webtoon_name: str):
        logger.info("Clearing zoom override for %s", webtoon_name)
        conn = get_connection()
        conn.execute(
            "UPDATE webtoon_settings SET zoom_override = NULL WHERE webtoon_name = ?",
            (webtoon_name,)
        )
        conn.commit()

    def get_source_url(self, webtoon_name: str) -> str | None:
        conn = get_connection()
        row = conn.execute(
            "SELECT source_url FROM webtoon_settings WHERE webtoon_name = ?",
            (webtoon_name,)
        ).fetchone()
        if row is None or not row["source_url"]:
            return None
        return str(row["source_url"])

    def set_source_url(self, webtoon_name: str, source_url: str):
        logger.info("Saving source URL for %s", webtoon_name)
        conn = get_connection()
        self._ensure_row(conn, webtoon_name)
        conn.execute(
            "UPDATE webtoon_settings SET source_url = ? WHERE webtoon_name = ?",
            (source_url, webtoon_name)
        )
        conn.commit()

    def clear_source_url(self, webtoon_name: str):
        logger.info("Clearing source URL for %s", webtoon_name)
        conn = get_connection()
        conn.execute(
            "UPDATE webtoon_settings SET source_url = NULL WHERE webtoon_name = ?",
            (webtoon_name,)
        )
        conn.commit()

    def get_last_update_at(self, webtoon_name: str) -> int | None:
        conn = get_connection()
        row = conn.execute(
            "SELECT last_update_at FROM webtoon_settings WHERE webtoon_name = ?",
            (webtoon_name,)
        ).fetchone()
        if row is None or row["last_update_at"] is None:
            return None
        return int(row["last_update_at"])

    def set_last_update_at(self, webtoon_name: str, timestamp: int):
        logger.info("Setting last update timestamp for %s to %d", webtoon_name, timestamp)
        conn = get_connection()
        self._ensure_row(conn, webtoon_name)
        conn.execute(
            "UPDATE webtoon_settings SET last_update_at = ? WHERE webtoon_name = ?",
            (int(timestamp), webtoon_name)
        )
        conn.commit()

    def get_latest_new_chapter(self, webtoon_name: str) -> str | None:
        conn = get_connection()
        row = conn.execute(
            "SELECT latest_new_chapter FROM webtoon_settings WHERE webtoon_name = ?",
            (webtoon_name,)
        ).fetchone()
        if row is None or not row["latest_new_chapter"]:
            return None
        return str(row["latest_new_chapter"])

    def set_latest_new_chapter(self, webtoon_name: str, chapter: str):
        logger.info("Setting latest new chapter for %s to %s", webtoon_name, chapter)
        conn = get_connection()
        self._ensure_row(conn, webtoon_name)
        conn.execute(
            "UPDATE webtoon_settings SET latest_new_chapter = ? WHERE webtoon_name = ?",
            (str(chapter), webtoon_name)
        )
        conn.commit()

    def clear_latest_new_chapter(self, webtoon_name: str):
        logger.info("Clearing latest new chapter for %s", webtoon_name)
        conn = get_connection()
        conn.execute(
            "UPDATE webtoon_settings SET latest_new_chapter = NULL WHERE webtoon_name = ?",
            (webtoon_name,)
        )
        conn.commit()

    def get(self, webtoon_name: str) -> str | None:
        conn = get_connection()
        row = conn.execute(
            "SELECT custom_thumbnail FROM webtoon_settings WHERE webtoon_name = ?",
            (webtoon_name,)
        ).fetchone()
        return row["custom_thumbnail"] if row and row["custom_thumbnail"] else None

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

        conn = get_connection()
        conn.execute(
            "UPDATE webtoon_settings SET custom_thumbnail = NULL WHERE webtoon_name = ?",
            (webtoon_name,)
        )
        conn.commit()

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
            (old_name,)
        ).fetchone()
        if row is None:
            return

        conn.execute(
            """INSERT OR REPLACE INTO webtoon_settings
               (webtoon_name, hide_filler, completed, zoom_override, custom_thumbnail, source_url, bookmarked_chapters, last_update_at, latest_new_chapter)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_name,
                row["hide_filler"],
                row["completed"],
                row["zoom_override"],
                custom_path,
                row["source_url"],
                row["bookmarked_chapters"],
                row["last_update_at"],
                row["latest_new_chapter"],
            )
        )
        conn.execute(
            "DELETE FROM webtoon_settings WHERE webtoon_name = ?",
            (old_name,)
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
            (webtoon_name,)
        )
        conn.commit()

    def _persist_custom_thumbnail(self, webtoon_name: str, path: str):
        conn = get_connection()
        self._ensure_row(conn, webtoon_name)
        conn.execute(
            "UPDATE webtoon_settings SET custom_thumbnail = ? WHERE webtoon_name = ?",
            (path, webtoon_name)
        )
        conn.commit()
