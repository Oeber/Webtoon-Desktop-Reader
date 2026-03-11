import io
from pathlib import Path
import urllib.error
import urllib.request

from db import get_connection


THUMBNAILS_DIR = Path("data/thumbnails")

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
        print(f"[WebtoonSettingsStore] Failed to copy local image '{src}': {e}")
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
        print(f"[WebtoonSettingsStore] Network error downloading '{url}': {e}")
        return False
    except Exception as e:
        print(f"[WebtoonSettingsStore] Failed to download/convert image from '{url}': {e}")
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

    def set_hide_filler(self, webtoon_name: str, value: bool):
        conn = get_connection()
        self._ensure_row(conn, webtoon_name)
        conn.execute(
            "UPDATE webtoon_settings SET hide_filler = ? WHERE webtoon_name = ?",
            (int(value), webtoon_name)
        )
        conn.commit()

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
        conn = get_connection()
        self._ensure_row(conn, webtoon_name)
        conn.execute(
            "UPDATE webtoon_settings SET zoom_override = ? WHERE webtoon_name = ?",
            (zoom, webtoon_name)
        )
        conn.commit()

    def clear_zoom_override(self, webtoon_name: str):
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
        conn = get_connection()
        self._ensure_row(conn, webtoon_name)
        conn.execute(
            "UPDATE webtoon_settings SET source_url = ? WHERE webtoon_name = ?",
            (source_url, webtoon_name)
        )
        conn.commit()

    def clear_source_url(self, webtoon_name: str):
        conn = get_connection()
        conn.execute(
            "UPDATE webtoon_settings SET source_url = NULL WHERE webtoon_name = ?",
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
        _ensure_thumbnails_dir()
        dest = _custom_thumb_path(webtoon_name)

        if not _copy_local_image(image_path, dest):
            internal_path = image_path
        else:
            internal_path = str(dest)

        self._persist_custom_thumbnail(webtoon_name, internal_path)
        return internal_path

    def set_from_url(self, webtoon_name: str, url: str) -> tuple[bool, str]:
        _ensure_thumbnails_dir()
        dest = _custom_thumb_path(webtoon_name)

        if _download_url_image(url, dest):
            self._persist_custom_thumbnail(webtoon_name, str(dest))
            return True, str(dest)
        return False, "Could not download or decode the image. Check the URL and try again."

    def clear(self, webtoon_name: str):
        dest = _custom_thumb_path(webtoon_name)
        if dest.exists():
            try:
                dest.unlink()
            except OSError as e:
                print(f"[WebtoonSettingsStore] Could not delete cached thumbnail: {e}")

        conn = get_connection()
        conn.execute(
            "UPDATE webtoon_settings SET custom_thumbnail = NULL WHERE webtoon_name = ?",
            (webtoon_name,)
        )
        conn.commit()

    def rename_webtoon(self, old_name: str, new_name: str):
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
               (webtoon_name, hide_filler, zoom_override, custom_thumbnail, source_url)
               VALUES (?, ?, ?, ?, ?)""",
            (
                new_name,
                row["hide_filler"],
                row["zoom_override"],
                custom_path,
                row["source_url"],
            )
        )
        conn.execute(
            "DELETE FROM webtoon_settings WHERE webtoon_name = ?",
            (old_name,)
        )
        conn.commit()

    def delete_webtoon(self, webtoon_name: str):
        self.clear(webtoon_name)

        auto_thumb = _auto_thumb_path(webtoon_name)
        if auto_thumb.exists():
            try:
                auto_thumb.unlink()
            except OSError as e:
                print(f"[WebtoonSettingsStore] Could not delete auto thumbnail: {e}")

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
