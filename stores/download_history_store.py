import threading
import time

from core.app_logging import get_logger
from stores.db import get_connection


logger = get_logger(__name__)

_instance = None


def get_instance() -> "DownloadHistoryStore":
    global _instance
    if _instance is None:
        _instance = DownloadHistoryStore()
    return _instance


class DownloadHistoryStore:

    def __init__(self):
        self._lock = threading.Lock()
        self._max_entries = 200

    def list_entries(self) -> list[dict]:
        with self._lock:
            rows = get_connection().execute(
                """
                SELECT kind, name, source_url, status, created_at, updated_at
                FROM download_history
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (self._max_entries,),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert(self, kind: str, name: str, status: str, source_url: str = ""):
        name = (name or "").strip()
        if not kind or not name:
            return

        timestamp = int(time.time())
        with self._lock:
            conn = get_connection()
            existing = conn.execute(
                """
                SELECT created_at, source_url
                FROM download_history
                WHERE kind = ? AND name = ?
                """,
                (kind, name),
            ).fetchone()
            created_at = timestamp if existing is None else int(existing["created_at"] or timestamp)
            next_source_url = source_url or (existing["source_url"] if existing is not None else "") or ""
            conn.execute(
                """
                INSERT OR REPLACE INTO download_history
                (kind, name, source_url, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (kind, name, next_source_url, status, created_at, timestamp),
            )
            self._trim_entries(conn)
            conn.commit()

    def rename(self, kind: str, old_name: str, new_name: str, source_url: str = "", status: str | None = None):
        old_name = (old_name or "").strip()
        new_name = (new_name or "").strip()
        if not kind or not old_name or not new_name or old_name == new_name:
            return

        timestamp = int(time.time())
        with self._lock:
            conn = get_connection()
            current = conn.execute(
                """
                SELECT kind, name, source_url, status, created_at, updated_at
                FROM download_history
                WHERE kind = ? AND name = ?
                """,
                (kind, old_name),
            ).fetchone()
            target = conn.execute(
                """
                SELECT kind, name, source_url, status, created_at, updated_at
                FROM download_history
                WHERE kind = ? AND name = ?
                """,
                (kind, new_name),
            ).fetchone()

            if current is None and target is None:
                return

            if current is None:
                source_row = target
            else:
                source_row = current

            next_status = status or source_row["status"] or "Ready"
            next_source_url = source_url or source_row["source_url"] or ""
            created_at = int(source_row["created_at"] or timestamp)

            conn.execute(
                "DELETE FROM download_history WHERE kind = ? AND name = ?",
                (kind, old_name),
            )
            if target is not None:
                conn.execute(
                    "DELETE FROM download_history WHERE kind = ? AND name = ?",
                    (kind, new_name),
                )

            conn.execute(
                """
                INSERT INTO download_history
                (kind, name, source_url, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (kind, new_name, next_source_url, next_status, created_at, timestamp),
            )
            self._trim_entries(conn)
            conn.commit()

    def _trim_entries(self, conn):
        conn.execute(
            """
            DELETE FROM download_history
            WHERE (kind, name) IN (
                SELECT kind, name
                FROM download_history
                ORDER BY updated_at DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (self._max_entries,),
        )
