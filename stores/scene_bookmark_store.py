import time
from pathlib import Path

from core.app_logging import get_logger
from stores.db import get_connection


logger = get_logger(__name__)

_instance = None


def get_instance() -> "SceneBookmarkStore":
    global _instance
    if _instance is None:
        _instance = SceneBookmarkStore()
    return _instance


class SceneBookmarkStore:
    def list_for_chapter(self, webtoon_name: str, chapter: str) -> list[dict]:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT id, webtoon_name, chapter, packed, image_index, note, thumbnail_path, created_at, updated_at
            FROM scene_bookmarks
            WHERE webtoon_name = ? AND chapter = ?
            ORDER BY updated_at DESC, id DESC
            """,
            (webtoon_name, chapter),
        ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def list_for_webtoon(self, webtoon_name: str) -> list[dict]:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT id, webtoon_name, chapter, packed, image_index, note, thumbnail_path, created_at, updated_at
            FROM scene_bookmarks
            WHERE webtoon_name = ?
            ORDER BY updated_at DESC, id DESC
            """,
            (webtoon_name,),
        ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def counts_for_webtoon(self, webtoon_name: str) -> dict[str, int]:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT chapter, COUNT(*) AS count
            FROM scene_bookmarks
            WHERE webtoon_name = ?
            GROUP BY chapter
            """,
            (webtoon_name,),
        ).fetchall()
        return {str(row["chapter"]): int(row["count"] or 0) for row in rows}

    def save(
        self,
        webtoon_name: str,
        chapter: str,
        packed: float,
        image_index: int,
        note: str = "",
        *,
        thumbnail_path: str = "",
    ) -> int:
        packed = max(0.0, float(packed))
        image_index = max(0, int(image_index))
        note = str(note or "").strip()
        thumbnail_path = str(thumbnail_path or "").strip()
        now = int(time.time() * 1000)
        conn = get_connection()
        cursor = conn.execute(
            """
            INSERT INTO scene_bookmarks (webtoon_name, chapter, packed, image_index, note, thumbnail_path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (webtoon_name, chapter, packed, image_index, note, thumbnail_path, now, now),
        )
        conn.commit()
        return int(cursor.lastrowid)

    def delete(self, bookmark_id: int):
        conn = get_connection()
        row = conn.execute(
            "SELECT thumbnail_path FROM scene_bookmarks WHERE id = ?",
            (int(bookmark_id),),
        ).fetchone()
        self._delete_thumbnail_path(row["thumbnail_path"] if row else "")
        conn.execute("DELETE FROM scene_bookmarks WHERE id = ?", (int(bookmark_id),))
        conn.commit()

    def clear_chapter(self, webtoon_name: str, chapter: str):
        conn = get_connection()
        self._delete_thumbnail_rows(
            conn.execute(
                "SELECT thumbnail_path FROM scene_bookmarks WHERE webtoon_name = ? AND chapter = ?",
                (webtoon_name, chapter),
            ).fetchall()
        )
        conn.execute(
            "DELETE FROM scene_bookmarks WHERE webtoon_name = ? AND chapter = ?",
            (webtoon_name, chapter),
        )
        conn.commit()

    def clear_chapters(self, webtoon_name: str, chapters: list[str]):
        if not chapters:
            return
        conn = get_connection()
        for chapter in chapters:
            self._delete_thumbnail_rows(
                conn.execute(
                    "SELECT thumbnail_path FROM scene_bookmarks WHERE webtoon_name = ? AND chapter = ?",
                    (webtoon_name, chapter),
                ).fetchall()
            )
        conn.executemany(
            "DELETE FROM scene_bookmarks WHERE webtoon_name = ? AND chapter = ?",
            [(webtoon_name, chapter) for chapter in chapters],
        )
        conn.commit()

    def clear(self, webtoon_name: str):
        conn = get_connection()
        self._delete_thumbnail_rows(
            conn.execute(
                "SELECT thumbnail_path FROM scene_bookmarks WHERE webtoon_name = ?",
                (webtoon_name,),
            ).fetchall()
        )
        conn.execute("DELETE FROM scene_bookmarks WHERE webtoon_name = ?", (webtoon_name,))
        conn.commit()

    def clear_many(self, webtoon_names: list[str]):
        if not webtoon_names:
            return
        conn = get_connection()
        for webtoon_name in webtoon_names:
            self._delete_thumbnail_rows(
                conn.execute(
                    "SELECT thumbnail_path FROM scene_bookmarks WHERE webtoon_name = ?",
                    (webtoon_name,),
                ).fetchall()
            )
        conn.executemany(
            "DELETE FROM scene_bookmarks WHERE webtoon_name = ?",
            [(webtoon_name,) for webtoon_name in webtoon_names],
        )
        conn.commit()

    def rename_webtoon(self, old_name: str, new_name: str):
        conn = get_connection()
        conn.execute(
            "UPDATE scene_bookmarks SET webtoon_name = ? WHERE webtoon_name = ?",
            (new_name, old_name),
        )
        conn.commit()

    def apply_chapter_page_changes(
        self,
        webtoon_name: str,
        chapter: str,
        page_index_map: dict[int, int],
        *,
        page_count: int,
        deleted_old_indexes: set[int] | None = None,
        new_page_count: int | None = None,
    ) -> None:
        mapping = {
            max(0, int(old_index)): max(0, int(new_index))
            for old_index, new_index in (page_index_map or {}).items()
        }
        deleted = {max(0, int(value)) for value in (deleted_old_indexes or set())}
        target_total = max(0, int(new_page_count if new_page_count is not None else page_count))
        if (not mapping and not deleted) or page_count <= 0:
            return

        conn = get_connection()
        rows = conn.execute(
            "SELECT id, packed, image_index, thumbnail_path FROM scene_bookmarks WHERE webtoon_name = ? AND chapter = ?",
            (webtoon_name, chapter),
        ).fetchall()
        if not rows:
            return

        payload = []
        delete_ids = []
        for row in rows:
            image_index = max(1, int(row["image_index"] or 1))
            old_index = max(0, min(page_count - 1, image_index - 1))
            if old_index in deleted:
                delete_ids.append((int(row["id"]),))
                self._delete_thumbnail_path(row["thumbnail_path"] if row else "")
                continue
            new_index = self._resolve_target_index(old_index, mapping, deleted, target_total)
            packed = self._remap_packed_position(row["packed"], mapping, page_count, deleted, target_total)
            payload.append((packed, new_index + 1, int(row["id"])))

        if delete_ids:
            conn.executemany("DELETE FROM scene_bookmarks WHERE id = ?", delete_ids)
        if payload:
            conn.executemany(
                "UPDATE scene_bookmarks SET packed = ?, image_index = ? WHERE id = ?",
                payload,
            )
        conn.commit()

    @staticmethod
    def _remap_packed_position(
        packed: float,
        page_index_map: dict[int, int],
        page_count: int,
        deleted_old_indexes: set[int],
        new_page_count: int,
    ) -> float:
        total = max(0, int(page_count or 0))
        current = max(0.0, float(packed or 0.0))
        if total <= 0 or new_page_count <= 0:
            return current
        if current >= float(total):
            return float(new_page_count)
        old_index = max(0, min(total - 1, int(current)))
        frac = max(0.0, min(1.0, current - int(current)))
        new_index = SceneBookmarkStore._resolve_target_index(old_index, page_index_map, deleted_old_indexes, new_page_count)
        return float(new_index) + frac

    @staticmethod
    def _resolve_target_index(
        old_index: int,
        page_index_map: dict[int, int],
        deleted_old_indexes: set[int],
        new_page_count: int,
    ) -> int:
        if old_index in page_index_map:
            return max(0, min(new_page_count - 1, int(page_index_map[old_index])))
        if old_index not in deleted_old_indexes:
            return max(0, min(new_page_count - 1, old_index))
        for next_old in range(old_index + 1, old_index + new_page_count + len(deleted_old_indexes) + 2):
            if next_old in page_index_map:
                return max(0, min(new_page_count - 1, int(page_index_map[next_old])))
        for prev_old in range(old_index - 1, -1, -1):
            if prev_old in page_index_map:
                return max(0, min(new_page_count - 1, int(page_index_map[prev_old])))
        return 0

    @staticmethod
    def _delete_thumbnail_rows(rows) -> None:
        for row in rows:
            SceneBookmarkStore._delete_thumbnail_path(row["thumbnail_path"] if row else "")

    @staticmethod
    def _delete_thumbnail_path(path_str: str) -> None:
        path_str = str(path_str or "").strip()
        if not path_str:
            return
        path = Path(path_str)
        if not path.exists():
            return
        try:
            path.unlink()
        except OSError:
            logger.warning("Could not delete scene bookmark thumbnail %s", path, exc_info=True)

    @staticmethod
    def _row_to_dict(row) -> dict:
        return {
            "id": int(row["id"]),
            "webtoon_name": str(row["webtoon_name"]),
            "chapter": str(row["chapter"]),
            "packed": float(row["packed"] or 0.0),
            "image_index": int(row["image_index"] or 0),
            "note": str(row["note"] or ""),
            "thumbnail_path": str(row["thumbnail_path"] or ""),
            "created_at": int(row["created_at"] or 0),
            "updated_at": int(row["updated_at"] or 0),
        }
