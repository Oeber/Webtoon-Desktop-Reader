import json
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
                SELECT kind, name, source_url, status, last_error, resume_payload, created_at, updated_at
                FROM download_history
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (self._max_entries,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_entry(self, kind: str, name: str) -> dict | None:
        normalized_kind = str(kind or "").strip()
        normalized_name = str(name or "").strip()
        if not normalized_kind or not normalized_name:
            return None
        with self._lock:
            row = get_connection().execute(
                """
                SELECT kind, name, source_url, status, last_error, resume_payload, created_at, updated_at
                FROM download_history
                WHERE kind = ? AND name = ?
                """,
                (normalized_kind, normalized_name),
            ).fetchone()
        return dict(row) if row is not None else None

    def upsert(self, kind: str, name: str, status: str, source_url: str = "", last_error: str = ""):
        name = (name or "").strip()
        if not kind or not name:
            return

        timestamp = int(time.time())
        with self._lock:
            conn = get_connection()
            existing = conn.execute(
                """
                SELECT created_at, source_url, resume_payload, last_error
                FROM download_history
                WHERE kind = ? AND name = ?
                """,
                (kind, name),
            ).fetchone()
            created_at = timestamp if existing is None else int(existing["created_at"] or timestamp)
            next_source_url = source_url or (existing["source_url"] if existing is not None else "") or ""
            next_resume_payload = existing["resume_payload"] if existing is not None else None
            next_last_error = str(last_error or "").strip()
            if not next_last_error and existing is not None:
                next_last_error = str(existing["last_error"] or "")
            if status in {"Completed", "Queued", "Downloading", "Ready"}:
                next_last_error = ""
            conn.execute(
                """
                INSERT OR REPLACE INTO download_history
                (kind, name, source_url, status, last_error, resume_payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (kind, name, next_source_url, status, next_last_error, next_resume_payload, created_at, timestamp),
            )
            self._trim_entries(conn)
            conn.commit()

    def set_resume_payload(self, kind: str, name: str, payload: dict | None, source_url: str = ""):
        name = (name or "").strip()
        if not kind or not name:
            return

        timestamp = int(time.time())
        payload_text = None
        if payload:
            try:
                payload_text = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
            except Exception:
                logger.exception("Failed to serialize resume payload for %s/%s", kind, name)
                payload_text = None

        with self._lock:
            conn = get_connection()
            existing = conn.execute(
                """
                SELECT created_at, source_url, status, last_error
                FROM download_history
                WHERE kind = ? AND name = ?
                """,
                (kind, name),
            ).fetchone()
            created_at = timestamp if existing is None else int(existing["created_at"] or timestamp)
            next_source_url = source_url or (existing["source_url"] if existing is not None else "") or ""
            next_status = existing["status"] if existing is not None else "Ready"
            next_last_error = str(existing["last_error"] or "") if existing is not None else ""
            conn.execute(
                """
                INSERT OR REPLACE INTO download_history
                (kind, name, source_url, status, last_error, resume_payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (kind, name, next_source_url, next_status, next_last_error, payload_text, created_at, timestamp),
            )
            self._trim_entries(conn)
            conn.commit()

    def clear_resume_payload(self, kind: str, name: str):
        name = (name or "").strip()
        if not kind or not name:
            return

        with self._lock:
            conn = get_connection()
            conn.execute(
                """
                UPDATE download_history
                SET resume_payload = NULL
                WHERE kind = ? AND name = ?
                """,
                (kind, name),
            )
            conn.commit()

    def list_resumable_entries(self, kind: str) -> list[dict]:
        if not kind:
            return []

        with self._lock:
            rows = get_connection().execute(
                """
                SELECT kind, name, source_url, status, last_error, resume_payload, created_at, updated_at
                FROM download_history
                WHERE kind = ?
                  AND COALESCE(TRIM(resume_payload), '') <> ''
                ORDER BY created_at ASC, updated_at ASC
                """,
                (kind,),
            ).fetchall()

        entries = []
        for row in rows:
            payload = row["resume_payload"]
            try:
                parsed_payload = json.loads(payload) if payload else {}
            except (TypeError, ValueError):
                logger.warning("Ignoring invalid resume payload for %s/%s", row["kind"], row["name"])
                parsed_payload = {}
            entry = dict(row)
            entry["resume_payload"] = parsed_payload
            entries.append(entry)
        return entries

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
                SELECT kind, name, source_url, status, last_error, resume_payload, created_at, updated_at
                FROM download_history
                WHERE kind = ? AND name = ?
                """,
                (kind, old_name),
            ).fetchone()
            target = conn.execute(
                """
                SELECT kind, name, source_url, status, last_error, resume_payload, created_at, updated_at
                FROM download_history
                WHERE kind = ? AND name = ?
                """,
                (kind, new_name),
            ).fetchone()

            if current is None and target is None:
                return

            source_row = current if current is not None else target
            next_status = status or source_row["status"] or "Ready"
            next_source_url = source_url or source_row["source_url"] or ""
            next_resume_payload = source_row["resume_payload"]
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
                (kind, name, source_url, status, last_error, resume_payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (kind, new_name, next_source_url, next_status, "", next_resume_payload, created_at, timestamp),
            )
            self._trim_entries(conn)
            conn.commit()

    def set_error(self, kind: str, name: str, error: str, source_url: str = ""):
        self.upsert(kind, name, "Failed", source_url=source_url, last_error=error)

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
