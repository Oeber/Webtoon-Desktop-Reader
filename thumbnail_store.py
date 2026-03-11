import os
import shutil
import urllib.request
import urllib.error
from pathlib import Path

from db import get_connection

THUMBNAILS_DIR = Path("data/thumbnails")


def _ensure_thumbnails_dir():
    THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)


def _sanitize_name(webtoon_name: str) -> str:
    """Strip characters that are unsafe in filenames."""
    keep = set(" ._-()[]abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    return "".join(c if c in keep else "_" for c in webtoon_name)


def _custom_thumb_path(webtoon_name: str) -> Path:
    return THUMBNAILS_DIR / f"{_sanitize_name(webtoon_name)}_custom.jpg"


def _copy_local_image(src: str, dest: Path) -> bool:
    """
    Copy a local image file into dest, converting to JPEG via Pillow.
    Returns True on success.
    """
    try:
        from PIL import Image
        with Image.open(src) as img:
            rgb = img.convert("RGB")
            rgb.save(dest, "JPEG", quality=92)
        return True
    except Exception as e:
        print(f"[ThumbnailStore] Failed to copy local image '{src}': {e}")
        return False


def _download_url_image(url: str, dest: Path) -> bool:
    """
    Download an image from a URL into dest, converting to JPEG via Pillow.
    Returns True on success.
    """
    try:
        from PIL import Image
        import io

        headers = {"User-Agent": "Mozilla/5.0 (WebtoonReader/1.0)"}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            data = response.read()

        with Image.open(io.BytesIO(data)) as img:
            rgb = img.convert("RGB")
            rgb.save(dest, "JPEG", quality=92)
        return True
    except urllib.error.URLError as e:
        print(f"[ThumbnailStore] Network error downloading '{url}': {e}")
        return False
    except Exception as e:
        print(f"[ThumbnailStore] Failed to download/convert image from '{url}': {e}")
        return False


class ThumbnailStore:

    def get(self, webtoon_name: str) -> str | None:
        """Return custom thumbnail path if set, else None."""
        conn = get_connection()
        row = conn.execute(
            "SELECT path FROM thumbnails WHERE webtoon_name = ?",
            (webtoon_name,)
        ).fetchone()
        return row["path"] if row else None

    def set(self, webtoon_name: str, image_path: str):
        """
        Set a custom thumbnail from a local file path.
        The image is copied into data/thumbnails/ as a JPEG.
        Stores the internal copy path in the DB.
        """
        _ensure_thumbnails_dir()
        dest = _custom_thumb_path(webtoon_name)

        if not _copy_local_image(image_path, dest):
            # Fallback: store the original path as-is (old behaviour)
            internal_path = image_path
        else:
            internal_path = str(dest)

        self._persist(webtoon_name, internal_path)
        return internal_path

    def set_from_url(self, webtoon_name: str, url: str) -> tuple[bool, str]:
        """
        Download a thumbnail from a URL and store it internally.
        Returns (success: bool, path_or_error: str).
        On success, path_or_error is the saved file path.
        On failure, path_or_error is a human-readable error message.
        """
        _ensure_thumbnails_dir()
        dest = _custom_thumb_path(webtoon_name)

        if _download_url_image(url, dest):
            self._persist(webtoon_name, str(dest))
            return True, str(dest)
        else:
            return False, "Could not download or decode the image. Check the URL and try again."

    def clear(self, webtoon_name: str):
        """Remove custom thumbnail override (and delete the cached file if present)."""
        dest = _custom_thumb_path(webtoon_name)
        if dest.exists():
            try:
                dest.unlink()
            except OSError as e:
                print(f"[ThumbnailStore] Could not delete cached thumbnail: {e}")

        conn = get_connection()
        conn.execute(
            "DELETE FROM thumbnails WHERE webtoon_name = ?",
            (webtoon_name,)
        )
        conn.commit()

    # ------------------------------------------------------------------ #
    #  Internal                                                            #
    # ------------------------------------------------------------------ #

    def _persist(self, webtoon_name: str, path: str):
        conn = get_connection()
        conn.execute(
            """INSERT INTO thumbnails (webtoon_name, path)
               VALUES (?, ?)
               ON CONFLICT(webtoon_name) DO UPDATE SET path = excluded.path""",
            (webtoon_name, path)
        )
        conn.commit()