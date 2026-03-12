import sqlite3

from core.app_logging import get_logger
from core.app_paths import data_path


logger = get_logger(__name__)

DB_PATH = data_path("reader.db")

_connection: sqlite3.Connection | None = None


def get_connection() -> sqlite3.Connection:
    global _connection
    if _connection is None:
        logger.info("Opening SQLite connection at %s", DB_PATH)
        _connection = _init_db()
    return _connection


# --------------------------------------------------------------------------- #
#  Internal
# --------------------------------------------------------------------------- #

def _init_db() -> sqlite3.Connection:
    data_path().mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    _create_schema(conn)
    logger.info("Database ready")

    return conn


def _create_schema(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS progress (
            webtoon_name   TEXT NOT NULL,
            chapter        TEXT NOT NULL,
            scroll         REAL NOT NULL DEFAULT 0.0,
            total_images   INTEGER NOT NULL DEFAULT 0,
            updated_at     INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
            PRIMARY KEY (webtoon_name, chapter)
        );

        CREATE TABLE IF NOT EXISTS webtoon_settings (
            webtoon_name        TEXT PRIMARY KEY,
            hide_filler         INTEGER NOT NULL DEFAULT 0,
            completed           INTEGER NOT NULL DEFAULT 0,
            bookmarked          INTEGER NOT NULL DEFAULT 0,
            zoom_override       REAL,
            custom_thumbnail    TEXT,
            source_url          TEXT,
            category            TEXT,
            bookmarked_chapters TEXT,
            last_update_at      INTEGER,
            latest_new_chapter  TEXT
        );

        CREATE TABLE IF NOT EXISTS app_settings (
            key            TEXT PRIMARY KEY,
            value          TEXT,
            updated_at     INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
        );
    """)
    _ensure_column(conn, "webtoon_settings", "hide_filler", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "webtoon_settings", "completed", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "webtoon_settings", "bookmarked", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "webtoon_settings", "zoom_override", "REAL")
    _ensure_column(conn, "webtoon_settings", "custom_thumbnail", "TEXT")
    _ensure_column(conn, "webtoon_settings", "source_url", "TEXT")
    _ensure_column(conn, "webtoon_settings", "category", "TEXT")
    _ensure_column(conn, "webtoon_settings", "bookmarked_chapters", "TEXT")
    _ensure_column(conn, "webtoon_settings", "last_update_at", "INTEGER")
    _ensure_column(conn, "webtoon_settings", "latest_new_chapter", "TEXT")
    _ensure_column(conn, "app_settings", "updated_at", "INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))")
    conn.commit()


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, column_def: str):
    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name in columns:
        return

    logger.info("Adding missing column %s.%s", table_name, column_name)
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")
