import time

from core.app_logging import get_logger
from stores.db import get_connection


logger = get_logger(__name__)

_instance = None


def get_instance() -> "TrackedTitlesStore":
    global _instance
    if _instance is None:
        _instance = TrackedTitlesStore()
    return _instance


class TrackedTitlesStore:
    def upsert_title(
        self,
        *,
        track_id: str,
        site_name: str,
        series_id: str,
        title: str,
        source_url: str = "",
        content_type: str = "webtoon",
        cover_url: str = "",
        status: str = "tracked",
        cache_status: str = "none",
        local_webtoon_name: str = "",
        last_read_chapter_key: str = "",
    ) -> None:
        now = int(time.time() * 1000)
        conn = get_connection()
        existing = conn.execute(
            "SELECT created_at, status, cache_status, local_webtoon_name, last_read_chapter_key FROM tracked_titles WHERE track_id = ?",
            (str(track_id or "").strip(),),
        ).fetchone()
        created_at = int(existing["created_at"] or now) if existing is not None else now
        normalized_status = str(status or "tracked").strip() or "tracked"
        normalized_cache_status = str(cache_status or "none").strip() or "none"
        normalized_local_name = str(local_webtoon_name or "").strip()
        normalized_last_read = str(last_read_chapter_key or "").strip()
        if existing is not None:
            existing_status = str(existing["status"] or "").strip()
            existing_cache_status = str(existing["cache_status"] or "").strip()
            existing_local_name = str(existing["local_webtoon_name"] or "").strip()
            existing_last_read = str(existing["last_read_chapter_key"] or "").strip()
            if existing_status in {"library", "mixed"} and normalized_status == "tracked":
                normalized_status = existing_status
            if existing_local_name and not normalized_local_name:
                normalized_local_name = existing_local_name
            if existing_cache_status == "cached" and normalized_cache_status == "none":
                normalized_cache_status = existing_cache_status
            if existing_last_read and not normalized_last_read:
                normalized_last_read = existing_last_read
        conn.execute(
            """
            INSERT OR REPLACE INTO tracked_titles (
                track_id,
                site_name,
                series_id,
                title,
                source_url,
                content_type,
                cover_url,
                status,
                cache_status,
                local_webtoon_name,
                last_read_chapter_key,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(track_id or "").strip(),
                str(site_name or "").strip(),
                str(series_id or "").strip(),
                str(title or "").strip(),
                str(source_url or "").strip(),
                str(content_type or "webtoon").strip() or "webtoon",
                str(cover_url or "").strip(),
                normalized_status,
                normalized_cache_status,
                normalized_local_name,
                normalized_last_read,
                created_at,
                now,
            ),
        )
        conn.commit()

    def get(self, track_id: str) -> dict | None:
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM tracked_titles WHERE track_id = ?",
            (str(track_id or "").strip(),),
        ).fetchone()
        return dict(row) if row is not None else None

    def list_titles(self) -> list[dict]:
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM tracked_titles ORDER BY updated_at DESC, title COLLATE NOCASE ASC"
        ).fetchall()
        return [dict(row) for row in rows]

    def list_library_titles(self) -> list[dict]:
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM tracked_titles WHERE status IN ('library', 'mixed') ORDER BY updated_at DESC, title COLLATE NOCASE ASC"
        ).fetchall()
        return [dict(row) for row in rows]

    def find_matching_title(self, *, site_name: str = "", series_id: str = "", source_url: str = "") -> dict | None:
        normalized_site = str(site_name or "").strip()
        normalized_series_id = str(series_id or "").strip()
        normalized_source_url = str(source_url or "").strip()
        conn = get_connection()
        row = None
        if normalized_site and normalized_series_id:
            row = conn.execute(
                "SELECT * FROM tracked_titles WHERE site_name = ? AND series_id = ? LIMIT 1",
                (normalized_site, normalized_series_id),
            ).fetchone()
        if row is None and normalized_source_url:
            row = conn.execute(
                "SELECT * FROM tracked_titles WHERE source_url = ? ORDER BY updated_at DESC LIMIT 1",
                (normalized_source_url,),
            ).fetchone()
        return dict(row) if row is not None else None

    def update_last_read(self, track_id: str, chapter_key: str, *, cache_status: str | None = None) -> None:
        now = int(time.time() * 1000)
        conn = get_connection()
        if cache_status is None:
            conn.execute(
                """
                UPDATE tracked_titles
                SET last_read_chapter_key = ?, updated_at = ?
                WHERE track_id = ?
                """,
                (str(chapter_key or "").strip(), now, str(track_id or "").strip()),
            )
        else:
            conn.execute(
                """
                UPDATE tracked_titles
                SET last_read_chapter_key = ?, cache_status = ?, updated_at = ?
                WHERE track_id = ?
                """,
                (str(chapter_key or "").strip(), str(cache_status or "").strip(), now, str(track_id or "").strip()),
            )
        conn.commit()

    def bind_local_title(self, track_id: str, local_webtoon_name: str) -> None:
        now = int(time.time() * 1000)
        conn = get_connection()
        conn.execute(
            """
            UPDATE tracked_titles
            SET local_webtoon_name = ?, status = 'mixed', updated_at = ?
            WHERE track_id = ?
            """,
            (str(local_webtoon_name or "").strip(), now, str(track_id or "").strip()),
        )
        conn.commit()

    def add_to_library(self, track_id: str) -> None:
        now = int(time.time() * 1000)
        conn = get_connection()
        conn.execute(
            """
            UPDATE tracked_titles
            SET status = CASE
                WHEN COALESCE(TRIM(local_webtoon_name), '') <> '' THEN 'mixed'
                ELSE 'library'
            END,
                updated_at = ?
            WHERE track_id = ?
            """,
            (now, str(track_id or "").strip()),
        )
        conn.commit()

    def remove_from_library(self, track_id: str) -> None:
        now = int(time.time() * 1000)
        conn = get_connection()
        conn.execute(
            """
            UPDATE tracked_titles
            SET status = CASE
                WHEN COALESCE(TRIM(local_webtoon_name), '') <> '' THEN 'mixed'
                ELSE 'tracked'
            END,
                updated_at = ?
            WHERE track_id = ?
            """,
            (now, str(track_id or "").strip()),
        )
        conn.commit()

    def delete(self, track_id: str) -> None:
        conn = get_connection()
        conn.execute(
            "DELETE FROM tracked_titles WHERE track_id = ?",
            (str(track_id or "").strip(),),
        )
        conn.commit()
