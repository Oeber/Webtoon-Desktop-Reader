from db import get_connection

_instance = None


def get_instance() -> "ProgressStore":
    global _instance
    if _instance is None:
        _instance = ProgressStore()
    return _instance


class ProgressStore:

    def get(self, webtoon_name: str) -> dict | None:
        """
        Return {"chapter": str, "scroll": float} for the given webtoon,
        or None if no progress has been saved.
        """
        conn = get_connection()
        row  = conn.execute(
            "SELECT chapter, scroll FROM progress WHERE webtoon_name = ?",
            (webtoon_name,)
        ).fetchone()
        if row is None:
            return None
        return {"chapter": row["chapter"], "scroll": row["scroll"]}

    def save(self, webtoon_name: str, chapter: str, scroll: float):
        """Persist progress, overwriting any previous entry."""
        conn = get_connection()
        conn.execute(
            """INSERT INTO progress (webtoon_name, chapter, scroll, updated_at)
               VALUES (?, ?, ?, strftime('%s', 'now'))
               ON CONFLICT(webtoon_name) DO UPDATE SET
                   chapter    = excluded.chapter,
                   scroll     = excluded.scroll,
                   updated_at = excluded.updated_at""",
            (webtoon_name, chapter, scroll)
        )
        conn.commit()

    def clear(self, webtoon_name: str):
        """Delete saved progress for a webtoon."""
        conn = get_connection()
        conn.execute(
            "DELETE FROM progress WHERE webtoon_name = ?",
            (webtoon_name,)
        )
        conn.commit()