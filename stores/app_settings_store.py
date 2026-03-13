from stores.db import get_connection


_instance = None


def get_instance() -> "AppSettingsStore":
    global _instance
    if _instance is None:
        _instance = AppSettingsStore()
    return _instance


class AppSettingsStore:

    def get(self, key: str, default=None):
        key = self._normalize_key(key)
        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return default
        try:
            raw_value = row["value"]
        except (TypeError, KeyError, IndexError):
            raw_value = row[0] if row else default
        return self._coerce_value(raw_value, default)

    def set(self, key: str, value):
        key = self._normalize_key(key)
        conn = get_connection()
        conn.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES (?, ?, strftime('%s', 'now'))
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value,
                   updated_at = excluded.updated_at""",
            (key, self._serialize_value(value)),
        )
        conn.commit()

    def _normalize_key(self, key) -> str:
        if key is None:
            return ""
        return str(key)

    def _serialize_value(self, value) -> str:
        if isinstance(value, bool):
            return "1" if value else "0"
        return str(value)

    def _coerce_value(self, raw: str, default):
        if default is None:
            return raw
        if isinstance(default, bool):
            return str(raw).strip().lower() in {"1", "true", "yes", "on"}
        if isinstance(default, int) and not isinstance(default, bool):
            try:
                return int(raw)
            except (TypeError, ValueError):
                return default
        if isinstance(default, float):
            try:
                return float(raw)
            except (TypeError, ValueError):
                return default
        return str(raw)
