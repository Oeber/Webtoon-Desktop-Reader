from db import get_connection


class ThumbnailStore:

    def get(self, webtoon_name: str) -> str | None:
        """Return custom thumbnail path if set, else None."""
        conn = get_connection()
        row  = conn.execute(
            "SELECT path FROM thumbnails WHERE webtoon_name = ?",
            (webtoon_name,)
        ).fetchone()
        return row["path"] if row else None

    def set(self, webtoon_name: str, image_path: str):
        """Set a custom thumbnail path and persist."""
        conn = get_connection()
        conn.execute(
            """INSERT INTO thumbnails (webtoon_name, path)
               VALUES (?, ?)
               ON CONFLICT(webtoon_name) DO UPDATE SET path = excluded.path""",
            (webtoon_name, image_path)
        )
        conn.commit()

    def clear(self, webtoon_name: str):
        """Remove custom thumbnail override."""
        conn = get_connection()
        conn.execute(
            "DELETE FROM thumbnails WHERE webtoon_name = ?",
            (webtoon_name,)
        )
        conn.commit()