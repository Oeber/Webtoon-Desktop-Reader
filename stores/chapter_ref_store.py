from __future__ import annotations

import time

from core.app_logging import get_logger
from core.chapter_identity import build_remote_chapter_key
from stores.db import get_connection


logger = get_logger(__name__)

_instance = None


def get_instance() -> "ChapterRefStore":
    global _instance
    if _instance is None:
        _instance = ChapterRefStore()
    return _instance


class ChapterRefStore:
    def upsert_ref(
        self,
        *,
        chapter_key: str,
        owner_kind: str,
        owner_id: str,
        site_name: str = "",
        series_id: str = "",
        remote_chapter_id: str = "",
        remote_url: str = "",
        local_chapter_name: str = "",
        chapter_title: str = "",
        chapter_number: float | None = None,
        cache_path: str = "",
        cache_state: str = "none",
    ) -> None:
        normalized_key = str(chapter_key or "").strip()
        if not normalized_key:
            return
        now = int(time.time() * 1000)
        conn = get_connection()
        existing = conn.execute(
            "SELECT created_at FROM chapter_refs WHERE chapter_key = ?",
            (normalized_key,),
        ).fetchone()
        created_at = int(existing["created_at"] or now) if existing is not None else now
        conn.execute(
            """
            INSERT OR REPLACE INTO chapter_refs (
                chapter_key,
                owner_kind,
                owner_id,
                site_name,
                series_id,
                remote_chapter_id,
                remote_url,
                local_chapter_name,
                chapter_title,
                chapter_number,
                cache_path,
                cache_state,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized_key,
                str(owner_kind or "").strip() or "tracked",
                str(owner_id or "").strip(),
                str(site_name or "").strip(),
                str(series_id or "").strip(),
                str(remote_chapter_id or "").strip(),
                str(remote_url or "").strip(),
                str(local_chapter_name or "").strip(),
                str(chapter_title or "").strip(),
                chapter_number,
                str(cache_path or "").strip(),
                str(cache_state or "none").strip() or "none",
                created_at,
                now,
            ),
        )
        conn.commit()

    def upsert_tracked_remote(
        self,
        *,
        track_id: str,
        site_name: str,
        series_id: str,
        chapter,
        cache_path: str = "",
        cache_state: str = "none",
    ) -> str:
        chapter_key = build_remote_chapter_key(
            site_name,
            series_id,
            str(getattr(chapter, "id", "") or "").strip(),
            str(getattr(chapter, "url", "") or "").strip(),
        )
        self.upsert_ref(
            chapter_key=chapter_key,
            owner_kind="tracked",
            owner_id=str(track_id or "").strip(),
            site_name=site_name,
            series_id=series_id,
            remote_chapter_id=str(getattr(chapter, "id", "") or "").strip(),
            remote_url=str(getattr(chapter, "url", "") or "").strip(),
            local_chapter_name="",
            chapter_title=str(getattr(chapter, "title", "") or "").strip(),
            chapter_number=getattr(chapter, "number", None),
            cache_path=cache_path,
            cache_state=cache_state,
        )
        return chapter_key

    def get(self, chapter_key: str) -> dict | None:
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM chapter_refs WHERE chapter_key = ?",
            (str(chapter_key or "").strip(),),
        ).fetchone()
        return dict(row) if row is not None else None

    def list_for_owner(self, owner_kind: str, owner_id: str) -> list[dict]:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT *
            FROM chapter_refs
            WHERE owner_kind = ? AND owner_id = ?
            ORDER BY updated_at DESC, chapter_title COLLATE NOCASE ASC
            """,
            (str(owner_kind or "").strip(), str(owner_id or "").strip()),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_cached_for_owner(self, owner_kind: str, owner_id: str) -> list[dict]:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT *
            FROM chapter_refs
            WHERE owner_kind = ? AND owner_id = ? AND cache_state = 'cached'
            ORDER BY updated_at DESC, chapter_title COLLATE NOCASE ASC
            """,
            (str(owner_kind or "").strip(), str(owner_id or "").strip()),
        ).fetchall()
        return [dict(row) for row in rows]

    def delete_for_owner(self, owner_kind: str, owner_id: str) -> int:
        conn = get_connection()
        cursor = conn.execute(
            "DELETE FROM chapter_refs WHERE owner_kind = ? AND owner_id = ?",
            (str(owner_kind or "").strip(), str(owner_id or "").strip()),
        )
        conn.commit()
        return int(getattr(cursor, 'rowcount', 0) or 0)

    def clear_cache_for_owner(self, owner_kind: str, owner_id: str) -> None:
        now = int(time.time() * 1000)
        conn = get_connection()
        conn.execute(
            """
            UPDATE chapter_refs
            SET cache_state = 'none', cache_path = '', updated_at = ?
            WHERE owner_kind = ? AND owner_id = ?
            """,
            (now, str(owner_kind or "").strip(), str(owner_id or "").strip()),
        )
        conn.commit()

    def bind_local_chapter(self, chapter_key: str, local_webtoon_name: str, local_chapter_name: str) -> None:
        now = int(time.time() * 1000)
        conn = get_connection()
        conn.execute(
            """
            UPDATE chapter_refs
            SET owner_kind = 'local',
                owner_id = ?,
                local_chapter_name = ?,
                updated_at = ?
            WHERE chapter_key = ?
            """,
            (
                str(local_webtoon_name or "").strip(),
                str(local_chapter_name or "").strip(),
                now,
                str(chapter_key or "").strip(),
            ),
        )
        conn.commit()

    def bind_series_to_local(self, track_id: str, local_webtoon_name: str, series, *, local_name_builder) -> dict[str, str]:
        rows = self.list_for_owner("tracked", track_id)
        if not rows:
            return {}

        by_id = {}
        by_url = {}
        by_number_title = {}
        ordered_rows = list(rows)
        for row in rows:
            remote_id = str(row.get("remote_chapter_id") or "").strip()
            remote_url = str(row.get("remote_url") or "").strip()
            key = self._number_title_key(row.get("chapter_number"), str(row.get("chapter_title") or ""))
            if remote_id:
                by_id[remote_id] = row
            if remote_url:
                by_url[remote_url] = row
            if key is not None:
                by_number_title[key] = row

        chapters = list(getattr(series, "chapters", []) or [])
        allow_order_fallback = len(ordered_rows) == len(chapters) and len(chapters) > 0
        chapter_map: dict[str, str] = {}
        matched_keys: set[str] = set()
        for index, chapter in enumerate(chapters):
            local_name = str(local_name_builder(chapter) or "").strip()
            if not local_name:
                continue
            match = None
            remote_id = str(getattr(chapter, "id", "") or "").strip()
            remote_url = str(getattr(chapter, "url", "") or "").strip()
            if remote_id:
                match = by_id.get(remote_id)
            if match is None and remote_url:
                match = by_url.get(remote_url)
            if match is None:
                match = by_number_title.get(
                    self._number_title_key(getattr(chapter, "number", None), str(getattr(chapter, "title", "") or ""))
                )
            if match is None and allow_order_fallback:
                match = ordered_rows[index]
            if match is None:
                continue
            chapter_key = str(match.get("chapter_key") or "").strip()
            if not chapter_key or chapter_key in matched_keys:
                continue
            matched_keys.add(chapter_key)
            chapter_map[chapter_key] = local_name
            self.bind_local_chapter(chapter_key, local_webtoon_name, local_name)
        return chapter_map

    @staticmethod
    def _number_title_key(number_value, title: str) -> tuple[float | None, str] | None:
        normalized_title = " ".join(str(title or "").casefold().split()).strip()
        numeric = None
        if number_value is not None:
            try:
                numeric = float(number_value)
            except (TypeError, ValueError):
                numeric = None
        if numeric is None and not normalized_title:
            return None
        return numeric, normalized_title
