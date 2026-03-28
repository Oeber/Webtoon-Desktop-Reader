import json
import threading
import time

from core.app_logging import get_logger
from stores.db import get_connection


logger = get_logger(__name__)

_instance = None


def get_instance() -> "NotificationStore":
    global _instance
    if _instance is None:
        _instance = NotificationStore()
    return _instance


class NotificationStore:

    def __init__(self):
        self._lock = threading.Lock()
        self._max_entries = 300

    def add(
        self,
        *,
        kind: str,
        title: str,
        message: str,
        severity: str = "info",
        webtoon_name: str = "",
        source_url: str = "",
        site_name: str = "",
        action_payload: dict | None = None,
    ) -> int:
        kind = str(kind or "").strip()
        title = str(title or "").strip()
        message = str(message or "").strip()
        if not kind or not title:
            return 0

        payload_text = ""
        if action_payload:
            try:
                payload_text = json.dumps(action_payload, ensure_ascii=True, separators=(",", ":"))
            except Exception:
                logger.exception("Failed to serialize notification action payload for %s", kind)
                payload_text = ""

        timestamp = int(time.time())
        with self._lock:
            conn = get_connection()
            cursor = conn.execute(
                """
                INSERT INTO notifications
                (kind, title, message, severity, webtoon_name, source_url, site_name, action_payload, is_read, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    kind,
                    title,
                    message,
                    str(severity or "info").strip() or "info",
                    str(webtoon_name or "").strip(),
                    str(source_url or "").strip(),
                    str(site_name or "").strip(),
                    payload_text,
                    timestamp,
                ),
            )
            self._trim_entries(conn)
            conn.commit()
            return int(cursor.lastrowid or 0)

    def list_entries(self, *, limit: int = 120) -> list[dict]:
        with self._lock:
            rows = get_connection().execute(
                """
                SELECT id, kind, title, message, severity, webtoon_name, source_url, site_name, action_payload, is_read, created_at
                FROM notifications
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        entries = []
        for row in rows:
            item = dict(row)
            payload = str(item.get("action_payload") or "").strip()
            if payload:
                try:
                    item["action_payload"] = json.loads(payload)
                except Exception:
                    item["action_payload"] = {}
            else:
                item["action_payload"] = {}
            item["is_read"] = bool(item.get("is_read", 0))
            entries.append(item)
        return entries

    def get_entry(self, notification_id: int) -> dict | None:
        with self._lock:
            row = get_connection().execute(
                """
                SELECT id, kind, title, message, severity, webtoon_name, source_url, site_name, action_payload, is_read, created_at
                FROM notifications
                WHERE id = ?
                """,
                (int(notification_id),),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        payload = str(item.get("action_payload") or "").strip()
        if payload:
            try:
                item["action_payload"] = json.loads(payload)
            except Exception:
                item["action_payload"] = {}
        else:
            item["action_payload"] = {}
        item["is_read"] = bool(item.get("is_read", 0))
        return item

    def unread_count(self) -> int:
        with self._lock:
            row = get_connection().execute(
                "SELECT COUNT(*) AS unread_count FROM notifications WHERE is_read = 0"
            ).fetchone()
        return int(row["unread_count"] if row is not None else 0)

    def mark_read(self, notification_id: int):
        with self._lock:
            conn = get_connection()
            conn.execute(
                "UPDATE notifications SET is_read = 1 WHERE id = ?",
                (int(notification_id),),
            )
            conn.commit()

    def mark_unread(self, notification_id: int):
        with self._lock:
            conn = get_connection()
            conn.execute(
                "UPDATE notifications SET is_read = 0 WHERE id = ?",
                (int(notification_id),),
            )
            conn.commit()

    def mark_all_read(self):
        with self._lock:
            conn = get_connection()
            conn.execute("UPDATE notifications SET is_read = 1 WHERE is_read = 0")
            conn.commit()

    def clear_read(self):
        with self._lock:
            conn = get_connection()
            conn.execute("DELETE FROM notifications WHERE is_read = 1")
            self._trim_entries(conn)
            conn.commit()

    def _trim_entries(self, conn):
        conn.execute(
            """
            DELETE FROM notifications
            WHERE id IN (
                SELECT id
                FROM notifications
                ORDER BY created_at DESC, id DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (self._max_entries,),
        )
