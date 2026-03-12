import json
import threading
import time

from core.app_logging import get_logger
from core.app_paths import data_path


logger = get_logger(__name__)

_instance = None


def get_instance() -> "DownloadHistoryStore":
    global _instance
    if _instance is None:
        _instance = DownloadHistoryStore()
    return _instance


class DownloadHistoryStore:

    def __init__(self):
        self._path = data_path("download_history.json")
        self._lock = threading.Lock()
        self._max_entries = 200

    def list_entries(self) -> list[dict]:
        with self._lock:
            entries = self._read_entries()
        return sorted(entries, key=lambda entry: int(entry.get("updated_at") or 0), reverse=True)

    def upsert(self, kind: str, name: str, status: str, source_url: str = ""):
        name = (name or "").strip()
        if not kind or not name:
            return

        timestamp = int(time.time())
        with self._lock:
            entries = self._read_entries()
            current = self._find_entry(entries, kind, name)
            if current is None:
                current = {
                    "kind": kind,
                    "name": name,
                    "source_url": source_url or "",
                    "status": status,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                }
                entries.append(current)
            else:
                current["status"] = status
                current["updated_at"] = timestamp
                if source_url:
                    current["source_url"] = source_url

            self._write_entries(entries)

    def rename(self, kind: str, old_name: str, new_name: str, source_url: str = "", status: str | None = None):
        old_name = (old_name or "").strip()
        new_name = (new_name or "").strip()
        if not kind or not old_name or not new_name or old_name == new_name:
            return

        timestamp = int(time.time())
        with self._lock:
            entries = self._read_entries()
            current = self._find_entry(entries, kind, old_name)
            target = self._find_entry(entries, kind, new_name)

            if current is None and target is None:
                return

            if current is None:
                current = target
            elif target is not None and target is not current:
                entries.remove(target)

            current["name"] = new_name
            current["updated_at"] = timestamp
            if status:
                current["status"] = status
            if source_url:
                current["source_url"] = source_url

            self._write_entries(entries)

    def _find_entry(self, entries: list[dict], kind: str, name: str) -> dict | None:
        for entry in entries:
            if entry.get("kind") == kind and entry.get("name") == name:
                return entry
        return None

    def _read_entries(self) -> list[dict]:
        if not self._path.exists():
            return []

        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Failed to read download history from %s", self._path)
            return []

        if not isinstance(data, list):
            return []
        return [entry for entry in data if isinstance(entry, dict)]

    def _write_entries(self, entries: list[dict]):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        trimmed = sorted(
            entries,
            key=lambda entry: int(entry.get("updated_at") or 0),
            reverse=True,
        )[:self._max_entries]
        self._path.write_text(
            json.dumps(trimmed, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )
