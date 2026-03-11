from db import get_connection

_instance = None


def get_instance() -> "ProgressStore":
    global _instance
    if _instance is None:
        _instance = ProgressStore()
    return _instance


class ProgressStore:

    def get(self, webtoon_name: str) -> dict | None:
        """Most recent progress (for Last read label + Continue button)."""
        conn = get_connection()
        row = conn.execute(
            """SELECT chapter, scroll 
               FROM progress 
               WHERE webtoon_name = ? 
               ORDER BY updated_at DESC 
               LIMIT 1""",
            (webtoon_name,)
        ).fetchone()
        if row is None:
            return None
        return {"chapter": row["chapter"], "scroll": row["scroll"]}

    def get_for_chapter(self, webtoon_name: str, chapter: str) -> float:
        """Scroll for a specific chapter (used in viewer prompt)."""
        conn = get_connection()
        row = conn.execute(
            "SELECT scroll FROM progress WHERE webtoon_name = ? AND chapter = ?",
            (webtoon_name, chapter)
        ).fetchone()
        return row["scroll"] if row else 0.0

    def get_progress_map(self, webtoon_name: str) -> dict[str, tuple[float, int]]:
        """All progress data for the webtoon (for completed indicators)."""
        conn = get_connection()
        rows = conn.execute(
            """SELECT chapter, scroll, total_images 
               FROM progress 
               WHERE webtoon_name = ?""",
            (webtoon_name,)
        ).fetchall()
        return {row["chapter"]: (row["scroll"], row["total_images"]) for row in rows}

    def save(self, webtoon_name: str, chapter: str, scroll: float, total_images: int = 0):
        """Save per-chapter progress + total image count."""
        conn = get_connection()
        conn.execute(
            """INSERT INTO progress (webtoon_name, chapter, scroll, total_images, updated_at)
               VALUES (?, ?, ?, ?, strftime('%s', 'now'))
               ON CONFLICT(webtoon_name, chapter) DO UPDATE SET
                   scroll       = excluded.scroll,
                   total_images = excluded.total_images,
                   updated_at   = excluded.updated_at""",
            (webtoon_name, chapter, scroll, total_images)
        )
        conn.commit()

    def clear(self, webtoon_name: str):
        """Delete ALL progress for a webtoon."""
        conn = get_connection()
        conn.execute(
            "DELETE FROM progress WHERE webtoon_name = ?",
            (webtoon_name,)
        )
        conn.commit()