import sqlite3
import threading
import time
from contextlib import suppress

from core.app_logging import get_logger
from core.app_paths import data_path


logger = get_logger(__name__)

DB_PATH = data_path("reader.db")
SQLITE_BUSY_TIMEOUT_MS = 5000
SCHEMA_VERSION = 7

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
    try:
        conn.execute("BEGIN")
        _migrate_schema(conn)
    except Exception:
        with suppress(Exception):
            conn.rollback()
        raise
    else:
        conn.commit()



def _migrate_schema(conn: sqlite3.Connection) -> None:
    current_version = _get_schema_version(conn)
    if current_version > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema version {current_version} is newer than supported version {SCHEMA_VERSION}."
        )

    if current_version == 0:
        if not _has_user_tables(conn):
            logger.info("Creating fresh SQLite schema at version %s", SCHEMA_VERSION)
            _create_latest_schema(conn)
            _set_schema_version(conn, SCHEMA_VERSION)
            return

        inferred_version = _infer_legacy_schema_version(conn)
        logger.info(
            "Legacy SQLite schema detected with user_version=0; inferred version %s",
            inferred_version,
        )
        current_version = inferred_version
        _set_schema_version(conn, current_version)

    for version in range(current_version + 1, SCHEMA_VERSION + 1):
        logger.info("Applying SQLite schema migration v%s -> v%s", version - 1, version)
        _apply_migration(conn, version)
        _set_schema_version(conn, version)



def _get_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    try:
        return int(row[0]) if row is not None else 0
    except (TypeError, ValueError, IndexError):
        return 0



def _set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(f"PRAGMA user_version = {int(version)}")



def _has_user_tables(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        """
        SELECT 1
          FROM sqlite_master
         WHERE type = 'table'
           AND name NOT LIKE 'sqlite_%'
         LIMIT 1
        """
    ).fetchone()
    return row is not None



def _has_table(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None



def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    if not _has_table(conn, table_name):
        return set()
    return {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }



def _infer_legacy_schema_version(conn: sqlite3.Connection) -> int:
    progress_columns = _table_columns(conn, "progress")
    webtoon_settings_columns = _table_columns(conn, "webtoon_settings")
    app_settings_columns = _table_columns(conn, "app_settings")
    download_history_columns = _table_columns(conn, "download_history")

    if {
        "source_url",
        "status",
        "last_error",
        "resume_payload",
        "created_at",
        "updated_at",
    }.issubset(download_history_columns) and {
        "update_mode",
        "auto_download_limit",
    }.issubset(webtoon_settings_columns):
        return 7

    if {
        "source_url",
        "status",
        "resume_payload",
        "created_at",
        "updated_at",
    }.issubset(download_history_columns):
        return 6

    if download_history_columns:
        return 5

    if "updated_at" in app_settings_columns:
        return 4

    if {
        "last_update_at",
        "latest_new_chapter",
        "remote_update_count",
    }.issubset(webtoon_settings_columns):
        return 3

    if progress_columns or webtoon_settings_columns or app_settings_columns:
        if "total_images" in progress_columns or {
            "hide_filler",
            "completed",
            "bookmarked",
            "zoom_override",
            "custom_thumbnail",
            "source_url",
            "source_site",
            "source_series_id",
            "source_title",
            "category",
            "bookmarked_chapters",
        }.intersection(webtoon_settings_columns):
            return 2
        return 1

    return 0



def _apply_migration(conn: sqlite3.Connection, version: int) -> None:
    migrations = {
        1: _migration_1_create_base_schema,
        2: _migration_2_add_progress_and_webtoon_settings_columns,
        3: _migration_3_add_webtoon_update_columns,
        4: _migration_4_add_app_settings_updated_at,
        5: _migration_5_create_download_history,
        6: _migration_6_expand_download_history,
        7: _migration_7_add_update_modes_and_download_errors,
    }
    migration = migrations.get(int(version))
    if migration is None:
        raise RuntimeError(f"No SQLite migration is defined for schema version {version}.")
    migration(conn)



def _create_latest_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
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
            remote_update_count INTEGER NOT NULL DEFAULT 0,
            update_mode         TEXT NOT NULL DEFAULT 'notify',
            auto_download_limit INTEGER NOT NULL DEFAULT 0
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
            last_error     TEXT NOT NULL DEFAULT '',
            resume_payload TEXT,
            created_at     INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
            updated_at     INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
            PRIMARY KEY (kind, name)
        );

        CREATE INDEX IF NOT EXISTS idx_download_history_updated_at
            ON download_history(updated_at DESC);
        """
    )



def _migration_1_create_base_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS progress (
            webtoon_name   TEXT NOT NULL,
            chapter        TEXT NOT NULL,
            scroll         REAL NOT NULL DEFAULT 0.0,
            updated_at     INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
            PRIMARY KEY (webtoon_name, chapter)
        );

        CREATE TABLE IF NOT EXISTS webtoon_settings (
            webtoon_name   TEXT PRIMARY KEY
        );

        CREATE TABLE IF NOT EXISTS app_settings (
            key            TEXT PRIMARY KEY,
            value          TEXT
        );
        """
    )



def _migration_2_add_progress_and_webtoon_settings_columns(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(conn, "progress", "total_images", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "webtoon_settings", "hide_filler", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "webtoon_settings", "completed", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "webtoon_settings", "bookmarked", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "webtoon_settings", "zoom_override", "REAL")
    _add_column_if_missing(conn, "webtoon_settings", "custom_thumbnail", "TEXT")
    _add_column_if_missing(conn, "webtoon_settings", "source_url", "TEXT")
    _add_column_if_missing(conn, "webtoon_settings", "source_site", "TEXT")
    _add_column_if_missing(conn, "webtoon_settings", "source_series_id", "TEXT")
    _add_column_if_missing(conn, "webtoon_settings", "source_title", "TEXT")
    _add_column_if_missing(conn, "webtoon_settings", "category", "TEXT")
    _add_column_if_missing(conn, "webtoon_settings", "bookmarked_chapters", "TEXT")



def _migration_3_add_webtoon_update_columns(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(conn, "webtoon_settings", "last_update_at", "INTEGER")
    _add_column_if_missing(conn, "webtoon_settings", "latest_new_chapter", "TEXT")
    _add_column_if_missing(conn, "webtoon_settings", "remote_update_count", "INTEGER NOT NULL DEFAULT 0")



def _migration_4_add_app_settings_updated_at(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(conn, "app_settings", "updated_at", "INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))")



def _migration_5_create_download_history(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS download_history (
            kind       TEXT NOT NULL,
            name       TEXT NOT NULL,
            source_url TEXT NOT NULL DEFAULT '',
            status     TEXT NOT NULL DEFAULT 'Ready',
            PRIMARY KEY (kind, name)
        );
        """
    )



def _migration_6_expand_download_history(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(conn, "download_history", "resume_payload", "TEXT")
    _add_column_if_missing(conn, "download_history", "created_at", "INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))")
    _add_column_if_missing(conn, "download_history", "updated_at", "INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_download_history_updated_at
            ON download_history(updated_at DESC)
        """
    )



def _migration_7_add_update_modes_and_download_errors(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(conn, "webtoon_settings", "update_mode", "TEXT NOT NULL DEFAULT 'notify'")
    _add_column_if_missing(conn, "webtoon_settings", "auto_download_limit", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "download_history", "last_error", "TEXT NOT NULL DEFAULT ''")



def _add_column_if_missing(conn: sqlite3.Connection, table_name: str, column_name: str, column_def: str) -> None:
    columns = _table_columns(conn, table_name)
    if column_name in columns:
        return

    logger.info("Adding missing column %s.%s", table_name, column_name)
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")
