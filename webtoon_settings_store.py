from db import get_connection

_instance = None


def get_instance() -> "WebtoonSettingsStore":
    global _instance
    if _instance is None:
        _instance = WebtoonSettingsStore()
    return _instance


class WebtoonSettingsStore:

    def get_hide_filler(self, webtoon_name: str) -> bool:
        conn = get_connection()
        row = conn.execute(
            "SELECT hide_filler FROM webtoon_settings WHERE webtoon_name = ?",
            (webtoon_name,)
        ).fetchone()
        return bool(row["hide_filler"]) if row else False

    def _ensure_row(self, conn, webtoon_name: str):
        """Ensure a settings row exists for this webtoon."""
        conn.execute(
            "INSERT OR IGNORE INTO webtoon_settings (webtoon_name) VALUES (?)",
            (webtoon_name,)
        )

    def set_hide_filler(self, webtoon_name: str, value: bool):
        conn = get_connection()
        self._ensure_row(conn, webtoon_name)
        conn.execute(
            "UPDATE webtoon_settings SET hide_filler = ? WHERE webtoon_name = ?",
            (int(value), webtoon_name)
        )
        conn.commit()

    def get_zoom_override(self, webtoon_name: str) -> float | None:
        """Return the saved zoom override for this webtoon, or None if not set."""
        conn = get_connection()
        row = conn.execute(
            "SELECT zoom_override FROM webtoon_settings WHERE webtoon_name = ?",
            (webtoon_name,)
        ).fetchone()
        if row is None or row["zoom_override"] is None:
            return None
        return float(row["zoom_override"])

    def set_zoom_override(self, webtoon_name: str, zoom: float):
        conn = get_connection()
        self._ensure_row(conn, webtoon_name)
        conn.execute(
            "UPDATE webtoon_settings SET zoom_override = ? WHERE webtoon_name = ?",
            (zoom, webtoon_name)
        )
        conn.commit()

    def clear_zoom_override(self, webtoon_name: str):
        conn = get_connection()
        conn.execute(
            "UPDATE webtoon_settings SET zoom_override = NULL WHERE webtoon_name = ?",
            (webtoon_name,)
        )
        conn.commit()

    def get_source_url(self, webtoon_name: str) -> str | None:
        conn = get_connection()
        row = conn.execute(
            "SELECT source_url FROM webtoon_settings WHERE webtoon_name = ?",
            (webtoon_name,)
        ).fetchone()
        if row is None or not row["source_url"]:
            return None
        return str(row["source_url"])

    def set_source_url(self, webtoon_name: str, source_url: str):
        conn = get_connection()
        self._ensure_row(conn, webtoon_name)
        conn.execute(
            "UPDATE webtoon_settings SET source_url = ? WHERE webtoon_name = ?",
            (source_url, webtoon_name)
        )
        conn.commit()
