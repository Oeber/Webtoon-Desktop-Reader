import threading
import time

from stores.db import get_connection
from core.app_logging import get_logger
from stores.webtoon_settings_store import get_instance as get_webtoon_settings


logger = get_logger(__name__)
settings_store = get_webtoon_settings()

_instance = None


def get_instance() -> "ProgressStore":
    global _instance
    if _instance is None:
        _instance = ProgressStore()
    return _instance


class ProgressStore:

    def __init__(self):
        self._pending_lock = threading.Lock()
        self._pending_condition = threading.Condition(self._pending_lock)
        self._pending_entries: dict[tuple[str, str], tuple[float, int, int]] = {}
        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            name="progress-store-writer",
            daemon=True,
        )
        self._writer_thread.start()

    def get(self, webtoon_name: str) -> dict | None:
        """Most recent progress (for Last read label + Continue button)."""
        conn = get_connection()
        row = conn.execute(
            """SELECT chapter, scroll, total_images 
               FROM progress 
               WHERE webtoon_name = ? 
               ORDER BY updated_at DESC, chapter DESC
               LIMIT 1""",
            (webtoon_name,)
        ).fetchone()
        if row is None:
            return None
        return {"chapter": row["chapter"], "scroll": row["scroll"], "total_images": row["total_images"]}

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

    def save(self, webtoon_name: str, chapter: str, scroll: float, total_images: int = 0, *, immediate: bool = True):
        """Save per-chapter progress + total image count."""
        logger.info(
            "Saving progress webtoon=%s chapter=%s scroll=%.3f total_images=%d immediate=%s",
            webtoon_name,
            chapter,
            scroll,
            total_images,
            immediate,
        )
        updated_at = int(time.time() * 1000)
        if immediate:
            self._discard_pending(webtoon_name, chapter)
            self._save_entries([(webtoon_name, chapter, scroll, total_images, updated_at)])
            return
        with self._pending_condition:
            self._pending_entries[(webtoon_name, chapter)] = (scroll, total_images, updated_at)
            self._pending_condition.notify()

    def save_many(self, webtoon_name: str, entries: list[tuple[str, float, int]]):
        if not entries:
            return
        logger.info("Saving progress for %d chapters in %s", len(entries), webtoon_name)
        updated_at_base = int(time.time() * 1000)
        payload = [
            (webtoon_name, chapter, scroll, total_images, updated_at_base + index)
            for index, (chapter, scroll, total_images) in enumerate(entries)
        ]
        with self._pending_condition:
            for _webtoon_name, chapter, _scroll, _total_images, _updated_at in payload:
                self._pending_entries.pop((_webtoon_name, chapter), None)
        self._save_entries(payload)

    def clear_chapter(self, webtoon_name: str, chapter: str):
        logger.info("Clearing progress webtoon=%s chapter=%s", webtoon_name, chapter)
        self._discard_pending(webtoon_name, chapter)
        conn = get_connection()
        conn.execute(
            "DELETE FROM progress WHERE webtoon_name = ? AND chapter = ?",
            (webtoon_name, chapter)
        )
        conn.commit()

    def clear_chapters(self, webtoon_name: str, chapters: list[str]):
        if not chapters:
            return
        logger.info("Clearing progress for %d chapters in %s", len(chapters), webtoon_name)
        with self._pending_condition:
            for chapter in chapters:
                self._pending_entries.pop((webtoon_name, chapter), None)
        conn = get_connection()
        conn.executemany(
            "DELETE FROM progress WHERE webtoon_name = ? AND chapter = ?",
            [(webtoon_name, chapter) for chapter in chapters]
        )
        conn.commit()

    def clear(self, webtoon_name: str):
        """Delete ALL progress for a webtoon."""
        logger.info("Clearing progress for %s", webtoon_name)
        with self._pending_condition:
            self._pending_entries = {
                key: value for key, value in self._pending_entries.items()
                if key[0] != webtoon_name
            }
        conn = get_connection()
        conn.execute(
            "DELETE FROM progress WHERE webtoon_name = ?",
            (webtoon_name,)
        )
        conn.commit()

    def clear_many(self, webtoon_names: list[str]):
        if not webtoon_names:
            return
        logger.info("Clearing progress for %d webtoons", len(webtoon_names))
        blocked = set(webtoon_names)
        with self._pending_condition:
            self._pending_entries = {
                key: value for key, value in self._pending_entries.items()
                if key[0] not in blocked
            }
        conn = get_connection()
        conn.executemany(
            "DELETE FROM progress WHERE webtoon_name = ?",
            [(webtoon_name,) for webtoon_name in webtoon_names]
        )
        conn.commit()

    def rename_webtoon(self, old_name: str, new_name: str):
        logger.info("Renaming progress rows from %s to %s", old_name, new_name)
        with self._pending_condition:
            renamed: dict[tuple[str, str], tuple[float, int, int]] = {}
            for (webtoon_name, chapter), value in self._pending_entries.items():
                target_name = new_name if webtoon_name == old_name else webtoon_name
                renamed[(target_name, chapter)] = value
            self._pending_entries = renamed
        conn = get_connection()
        conn.execute(
            "UPDATE progress SET webtoon_name = ? WHERE webtoon_name = ?",
            (new_name, old_name)
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

        with self._pending_condition:
            pending_key = (webtoon_name, chapter)
            pending_value = self._pending_entries.get(pending_key)
            if pending_value is not None:
                scroll, total_images, updated_at = pending_value
                if target_total <= 0:
                    self._pending_entries.pop(pending_key, None)
                else:
                    self._pending_entries[pending_key] = (
                        self._remap_packed_position(scroll, total_images, mapping, page_count, deleted, target_total),
                        target_total,
                        updated_at,
                    )

        conn = get_connection()
        row = conn.execute(
            "SELECT scroll, total_images, updated_at FROM progress WHERE webtoon_name = ? AND chapter = ?",
            (webtoon_name, chapter),
        ).fetchone()
        if row is None:
            return
        if target_total <= 0:
            conn.execute(
                "DELETE FROM progress WHERE webtoon_name = ? AND chapter = ?",
                (webtoon_name, chapter),
            )
            conn.commit()
            return

        total_images = int(row["total_images"] or page_count or 0)
        conn.execute(
            """
            UPDATE progress
            SET scroll = ?, total_images = ?, updated_at = ?
            WHERE webtoon_name = ? AND chapter = ?
            """,
            (
                self._remap_packed_position(row["scroll"], total_images, mapping, page_count, deleted, target_total),
                target_total,
                int(row["updated_at"] or 0),
                webtoon_name,
                chapter,
            ),
        )
        conn.commit()

    def _discard_pending(self, webtoon_name: str, chapter: str) -> None:
        with self._pending_condition:
            self._pending_entries.pop((webtoon_name, chapter), None)

    def _writer_loop(self) -> None:
        while True:
            with self._pending_condition:
                while not self._pending_entries:
                    self._pending_condition.wait()
                pending = self._pending_entries
                self._pending_entries = {}
            payload = [
                (webtoon_name, chapter, scroll, total_images, updated_at)
                for (webtoon_name, chapter), (scroll, total_images, updated_at) in pending.items()
            ]
            try:
                self._save_entries(payload)
            except Exception:
                logger.exception("Failed to write queued progress entries")

    def _save_entries(self, payload: list[tuple[str, str, float, int, int]]) -> None:
        if not payload:
            return
        conn = get_connection()
        conn.executemany(
            """INSERT INTO progress (webtoon_name, chapter, scroll, total_images, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(webtoon_name, chapter) DO UPDATE SET
                   scroll       = excluded.scroll,
                   total_images = excluded.total_images,
                   updated_at   = excluded.updated_at""",
            payload
        )
        conn.commit()
        self._clear_latest_new_chapters(payload)

    def _clear_latest_new_chapters(self, payload: list[tuple[str, str, float, int, int]]) -> None:
        touched_webtoons: dict[str, set[str]] = {}
        for webtoon_name, chapter, scroll, total_images, _updated_at in payload:
            if scroll <= 0.0 and total_images <= 0:
                continue
            touched_webtoons.setdefault(webtoon_name, set()).add(chapter)
        for webtoon_name, chapters in touched_webtoons.items():
            latest_new_chapter = settings_store.get_latest_new_chapter(webtoon_name)
            if latest_new_chapter and latest_new_chapter in chapters:
                settings_store.clear_latest_new_chapter(webtoon_name)

    @staticmethod
    def _remap_packed_position(
        scroll: float,
        total_images: int,
        page_index_map: dict[int, int],
        page_count: int,
        deleted_old_indexes: set[int],
        new_page_count: int,
    ) -> float:
        total = max(0, int(total_images or page_count or 0))
        packed = max(0.0, float(scroll or 0.0))
        if total <= 0 or new_page_count <= 0:
            return packed
        if packed >= float(total):
            return float(new_page_count)
        old_index = max(0, min(total - 1, int(packed)))
        frac = max(0.0, min(1.0, packed - int(packed)))
        new_index = ProgressStore._resolve_target_index(
            old_index,
            page_index_map,
            deleted_old_indexes,
            new_page_count,
        )
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
