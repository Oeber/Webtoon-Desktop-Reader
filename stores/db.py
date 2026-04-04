import sqlite3
import threading
import time
from contextlib import suppress

from core.app_logging import get_logger
from core.app_paths import data_path


logger = get_logger(__name__)

DB_PATH = data_path("reader.db")
SQLITE_BUSY_TIMEOUT_MS = 5000
SCHEMA_VERSION = 17

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
        "content_type",
    }.issubset(webtoon_settings_columns):
        return 9

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
        8: _migration_8_create_scene_bookmarks,
        9: _migration_9_add_content_type_to_webtoon_settings,
        10: _migration_10_add_manga_reader_settings,
        11: _migration_11_add_text_reader_settings,
        12: _migration_12_create_notifications,
        13: _migration_13_add_source_config_to_webtoon_settings,
        14: _migration_14_remove_notifications,
        15: _migration_15_add_chapter_keys,
        16: _migration_16_create_tracked_titles,
        17: _migration_17_add_chapter_refs,
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
            chapter_key    TEXT,
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
            source_config       TEXT,
            category            TEXT,
            bookmarked_chapters TEXT,
            last_update_at      INTEGER,
            latest_new_chapter  TEXT,
            remote_update_count INTEGER NOT NULL DEFAULT 0,
            update_mode         TEXT NOT NULL DEFAULT 'notify',
            auto_download_limit INTEGER NOT NULL DEFAULT 0,
            content_type        TEXT,
            manga_view_mode     TEXT,
            manga_fit_mode      TEXT,
            text_font_size      INTEGER,
            text_page_color     TEXT,
            text_color          TEXT
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

        CREATE INDEX IF NOT EXISTS idx_progress_chapter_key
            ON progress(chapter_key);

        CREATE TABLE IF NOT EXISTS scene_bookmarks (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            webtoon_name   TEXT NOT NULL,
            chapter        TEXT NOT NULL,
            chapter_key    TEXT,
            packed         REAL NOT NULL DEFAULT 0.0,
            image_index    INTEGER NOT NULL DEFAULT 0,
            note           TEXT NOT NULL DEFAULT '',
            thumbnail_path TEXT NOT NULL DEFAULT '',
            created_at     INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
            updated_at     INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
        );

        CREATE INDEX IF NOT EXISTS idx_scene_bookmarks_lookup
            ON scene_bookmarks(webtoon_name, chapter, updated_at DESC, id DESC);

        CREATE INDEX IF NOT EXISTS idx_scene_bookmarks_chapter_key
            ON scene_bookmarks(chapter_key, updated_at DESC, id DESC);

        CREATE TABLE IF NOT EXISTS tracked_titles (
            track_id               TEXT PRIMARY KEY,
            site_name              TEXT NOT NULL,
            series_id              TEXT NOT NULL,
            title                  TEXT NOT NULL,
            source_url             TEXT NOT NULL DEFAULT '',
            source_config          TEXT NOT NULL DEFAULT '{}',
            content_type           TEXT NOT NULL DEFAULT 'webtoon',
            cover_url              TEXT NOT NULL DEFAULT '',
            cover_headers          TEXT NOT NULL DEFAULT '{}',
            status                 TEXT NOT NULL DEFAULT 'tracked',
            cache_status           TEXT NOT NULL DEFAULT 'none',
            local_webtoon_name     TEXT NOT NULL DEFAULT '',
            last_read_chapter_key  TEXT NOT NULL DEFAULT '',
            last_checked_at        INTEGER,
            created_at             INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
            updated_at             INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_tracked_titles_site_series
            ON tracked_titles(site_name, series_id);

        CREATE INDEX IF NOT EXISTS idx_tracked_titles_updated_at
            ON tracked_titles(updated_at DESC);

        CREATE TABLE IF NOT EXISTS chapter_refs (
            chapter_key        TEXT PRIMARY KEY,
            owner_kind         TEXT NOT NULL,
            owner_id           TEXT NOT NULL,
            site_name          TEXT NOT NULL DEFAULT '',
            series_id          TEXT NOT NULL DEFAULT '',
            remote_chapter_id  TEXT NOT NULL DEFAULT '',
            remote_url         TEXT NOT NULL DEFAULT '',
            local_chapter_name TEXT NOT NULL DEFAULT '',
            chapter_title      TEXT NOT NULL DEFAULT '',
            chapter_number     REAL,
            cache_path         TEXT NOT NULL DEFAULT '',
            cache_state        TEXT NOT NULL DEFAULT 'none',
            created_at         INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
            updated_at         INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_chapter_refs_remote_identity
            ON chapter_refs(site_name, series_id, remote_chapter_id);

        CREATE INDEX IF NOT EXISTS idx_chapter_refs_owner
            ON chapter_refs(owner_kind, owner_id);

        """
    )



def _migration_1_create_base_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS progress (
            webtoon_name   TEXT NOT NULL,
            chapter        TEXT NOT NULL,
            chapter_key    TEXT,
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
    _add_column_if_missing(conn, "webtoon_settings", "source_config", "TEXT")
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


def _migration_8_create_scene_bookmarks(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS scene_bookmarks (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            webtoon_name   TEXT NOT NULL,
            chapter        TEXT NOT NULL,
            chapter_key    TEXT,
            packed         REAL NOT NULL DEFAULT 0.0,
            image_index    INTEGER NOT NULL DEFAULT 0,
            note           TEXT NOT NULL DEFAULT '',
            thumbnail_path TEXT NOT NULL DEFAULT '',
            created_at     INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
            updated_at     INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
        );

        CREATE INDEX IF NOT EXISTS idx_scene_bookmarks_lookup
            ON scene_bookmarks(webtoon_name, chapter, updated_at DESC, id DESC);
        """
    )



def _migration_9_add_content_type_to_webtoon_settings(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(conn, "webtoon_settings", "content_type", "TEXT")


def _migration_10_add_manga_reader_settings(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(conn, "webtoon_settings", "manga_view_mode", "TEXT")
    _add_column_if_missing(conn, "webtoon_settings", "manga_fit_mode", "TEXT")


def _migration_11_add_text_reader_settings(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(conn, "webtoon_settings", "text_font_size", "INTEGER")
    _add_column_if_missing(conn, "webtoon_settings", "text_page_color", "TEXT")
    _add_column_if_missing(conn, "webtoon_settings", "text_color", "TEXT")


def _migration_12_create_notifications(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        """
    )


def _add_column_if_missing(conn: sqlite3.Connection, table_name: str, column_name: str, column_def: str) -> None:
    columns = _table_columns(conn, table_name)
    if column_name in columns:
        return

    logger.info("Adding missing column %s.%s", table_name, column_name)
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")


def _migration_13_add_source_config_to_webtoon_settings(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(conn, "webtoon_settings", "source_config", "TEXT")


def _migration_14_remove_notifications(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS notifications")


def _migration_15_add_chapter_keys(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(conn, 'progress', 'chapter_key', 'TEXT')
    _add_column_if_missing(conn, 'scene_bookmarks', 'chapter_key', 'TEXT')
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_progress_chapter_key
            ON progress(chapter_key)
        """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_scene_bookmarks_chapter_key
            ON scene_bookmarks(chapter_key, updated_at DESC, id DESC)
        """)
    conn.execute("""
        UPDATE progress
        SET chapter_key = 'local::' || REPLACE(COALESCE(webtoon_name, ''), ':', '%3A') || '::' || REPLACE(COALESCE(chapter, ''), ':', '%3A')
        WHERE COALESCE(TRIM(chapter_key), '') = ''
        """)
    conn.execute("""
        UPDATE scene_bookmarks
        SET chapter_key = 'local::' || REPLACE(COALESCE(webtoon_name, ''), ':', '%3A') || '::' || REPLACE(COALESCE(chapter, ''), ':', '%3A')
        WHERE COALESCE(TRIM(chapter_key), '') = ''
        """)




def _migration_16_create_tracked_titles(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tracked_titles (
            track_id               TEXT PRIMARY KEY,
            site_name              TEXT NOT NULL,
            series_id              TEXT NOT NULL,
            title                  TEXT NOT NULL,
            source_url             TEXT NOT NULL DEFAULT '',
            source_config          TEXT NOT NULL DEFAULT '{}',
            content_type           TEXT NOT NULL DEFAULT 'webtoon',
            cover_url              TEXT NOT NULL DEFAULT '',
            cover_headers          TEXT NOT NULL DEFAULT '{}',
            status                 TEXT NOT NULL DEFAULT 'tracked',
            cache_status           TEXT NOT NULL DEFAULT 'none',
            local_webtoon_name     TEXT NOT NULL DEFAULT '',
            last_read_chapter_key  TEXT NOT NULL DEFAULT '',
            last_checked_at        INTEGER,
            created_at             INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
            updated_at             INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_tracked_titles_site_series
            ON tracked_titles(site_name, series_id);

        CREATE INDEX IF NOT EXISTS idx_tracked_titles_updated_at
            ON tracked_titles(updated_at DESC);

        CREATE TABLE IF NOT EXISTS chapter_refs (
            chapter_key        TEXT PRIMARY KEY,
            owner_kind         TEXT NOT NULL,
            owner_id           TEXT NOT NULL,
            site_name          TEXT NOT NULL DEFAULT '',
            series_id          TEXT NOT NULL DEFAULT '',
            remote_chapter_id  TEXT NOT NULL DEFAULT '',
            remote_url         TEXT NOT NULL DEFAULT '',
            local_chapter_name TEXT NOT NULL DEFAULT '',
            chapter_title      TEXT NOT NULL DEFAULT '',
            chapter_number     REAL,
            cache_path         TEXT NOT NULL DEFAULT '',
            cache_state        TEXT NOT NULL DEFAULT 'none',
            created_at         INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
            updated_at         INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_chapter_refs_remote_identity
            ON chapter_refs(site_name, series_id, remote_chapter_id);

        CREATE INDEX IF NOT EXISTS idx_chapter_refs_owner
            ON chapter_refs(owner_kind, owner_id);
        """
    )





def _migration_17_add_chapter_refs(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(conn, "tracked_titles", "source_config", "TEXT NOT NULL DEFAULT '{}'" )
    _add_column_if_missing(conn, "tracked_titles", "cover_headers", "TEXT NOT NULL DEFAULT '{}'" )
    _add_column_if_missing(conn, "tracked_titles", "last_checked_at", "INTEGER")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS chapter_refs (
            chapter_key        TEXT PRIMARY KEY,
            owner_kind         TEXT NOT NULL,
            owner_id           TEXT NOT NULL,
            site_name          TEXT NOT NULL DEFAULT '',
            series_id          TEXT NOT NULL DEFAULT '',
            remote_chapter_id  TEXT NOT NULL DEFAULT '',
            remote_url         TEXT NOT NULL DEFAULT '',
            local_chapter_name TEXT NOT NULL DEFAULT '',
            chapter_title      TEXT NOT NULL DEFAULT '',
            chapter_number     REAL,
            cache_path         TEXT NOT NULL DEFAULT '',
            cache_state        TEXT NOT NULL DEFAULT 'none',
            created_at         INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
            updated_at         INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_chapter_refs_remote_identity
            ON chapter_refs(site_name, series_id, remote_chapter_id);

        CREATE INDEX IF NOT EXISTS idx_chapter_refs_owner
            ON chapter_refs(owner_kind, owner_id);
        """
    )



