import sqlite3
import threading
import time
from contextlib import suppress

from core.app_logging import get_logger
from core.app_paths import data_path


logger = get_logger(__name__)

DB_PATH = data_path("reader.db")
SQLITE_BUSY_TIMEOUT_MS = 5000

_thread_state = threading.local()
_init_lock = threading.Lock()
_db_initialized = False


def get_connection() -> sqlite3.Connection:
    _ensure_initialized()
    conn = getattr(_thread_state, "connection", None)
    if conn is not None:
        return conn

    started_at = time.perf_counter()
    logger.info("Opening SQLite connection at %s for thread %s", DB_PATH, threading.current_thread().name)
    conn = _create_connection()
    _thread_state.connection = conn
    logger.info(
        "SQLite connection ready in %.1f ms for thread %s",
        (time.perf_counter() - started_at) * 1000.0,
        threading.current_thread().name,
    )
    return conn


def prewarm_connection() -> None:
    if getattr(_thread_state, "connection", None) is not None:
        return
    get_connection()


def prewarm_connection_async():
    def _worker():
        try:
            _ensure_initialized()
        except Exception:
            logger.exception("Failed to prewarm SQLite schema")

    threading.Thread(
        target=_worker,
        name="sqlite-prewarm",
        daemon=True,
    ).start()


# --------------------------------------------------------------------------- #
#  Internal
# --------------------------------------------------------------------------- #

def _ensure_initialized():
    global _db_initialized
    if _db_initialized:
        return

    with _init_lock:
        if _db_initialized:
            return

        started_at = time.perf_counter()
        logger.info("Initializing SQLite schema at %s", DB_PATH)
        conn = _create_connection()
        try:
            _create_schema(conn)
            logger.info(
                "SQLite initialization finished in %.1f ms",
                (time.perf_counter() - started_at) * 1000.0,
            )
            _db_initialized = True
        finally:
            with suppress(Exception):
                conn.close()


def _create_connection() -> sqlite3.Connection:
    data_path().mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")

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
            source_site         TEXT,
            source_series_id    TEXT,
            source_title        TEXT,
            category            TEXT,
            bookmarked_chapters TEXT,
            last_update_at      INTEGER,
            latest_new_chapter  TEXT,
            remote_update_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS app_settings (
            key            TEXT PRIMARY KEY,
            value          TEXT,
            updated_at     INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
        );

        CREATE TABLE IF NOT EXISTS download_history (
            kind           TEXT NOT NULL,
            name           TEXT NOT NULL,
            source_url     TEXT NOT NULL DEFAULT '',
            status         TEXT NOT NULL DEFAULT 'Ready',
            resume_payload TEXT,
            created_at     INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
            updated_at     INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
            PRIMARY KEY (kind, name)
        );

        CREATE INDEX IF NOT EXISTS idx_download_history_updated_at
            ON download_history(updated_at DESC);
    """)
    _ensure_column(conn, "webtoon_settings", "hide_filler", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "webtoon_settings", "completed", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "webtoon_settings", "bookmarked", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "webtoon_settings", "zoom_override", "REAL")
    _ensure_column(conn, "webtoon_settings", "custom_thumbnail", "TEXT")
    _ensure_column(conn, "webtoon_settings", "source_url", "TEXT")
    _ensure_column(conn, "webtoon_settings", "source_site", "TEXT")
    _ensure_column(conn, "webtoon_settings", "source_series_id", "TEXT")
    _ensure_column(conn, "webtoon_settings", "source_title", "TEXT")
    _ensure_column(conn, "webtoon_settings", "category", "TEXT")
    _ensure_column(conn, "webtoon_settings", "bookmarked_chapters", "TEXT")
    _ensure_column(conn, "webtoon_settings", "last_update_at", "INTEGER")
    _ensure_column(conn, "webtoon_settings", "latest_new_chapter", "TEXT")
    _ensure_column(conn, "webtoon_settings", "remote_update_count", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "app_settings", "updated_at", "INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))")
    _ensure_column(conn, "download_history", "source_url", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "download_history", "status", "TEXT NOT NULL DEFAULT 'Ready'")
    _ensure_column(conn, "download_history", "resume_payload", "TEXT")
    _ensure_column(conn, "download_history", "created_at", "INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))")
    _ensure_column(conn, "download_history", "updated_at", "INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))")
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

