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

    def set_hide_filler(self, webtoon_name: str, value: bool):
        conn = get_connection()
        conn.execute(
            """INSERT INTO webtoon_settings (webtoon_name, hide_filler)
               VALUES (?, ?)
               ON CONFLICT(webtoon_name) DO UPDATE SET
                   hide_filler = excluded.hide_filler""",
            (webtoon_name, int(value))
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
        conn.execute(
            """INSERT INTO webtoon_settings (webtoon_name, zoom_override)
               VALUES (?, ?)
               ON CONFLICT(webtoon_name) DO UPDATE SET
                   zoom_override = excluded.zoom_override""",
            (webtoon_name, zoom)
        )
        conn.commit()

    def clear_zoom_override(self, webtoon_name: str):
        conn = get_connection()
        conn.execute(
            """UPDATE webtoon_settings SET zoom_override = NULL
               WHERE webtoon_name = ?""",
            (webtoon_name,)
        )
        conn.commit()