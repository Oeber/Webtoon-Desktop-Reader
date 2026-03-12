import json
import os
import sqlite3

from app_logging import get_logger


logger = get_logger(__name__)

DB_PATH           = "data/reader.db"
THUMBNAILS_JSON   = "data/thumbnails.json"
PROGRESS_JSON     = "data/progress.json"

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
    os.makedirs("data", exist_ok=True)

    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    _create_schema(conn)
    _migrate_json(conn)
    _migrate_columns(conn)
    _migrate_thumbnails_table(conn)
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
            webtoon_name      TEXT PRIMARY KEY,
            hide_filler       INTEGER NOT NULL DEFAULT 0,
            zoom_override     REAL,
            custom_thumbnail  TEXT,
            source_url        TEXT,
            bookmarked_chapters TEXT,
            last_update_at    INTEGER
        );
    """)
    conn.commit()


def _migrate_columns(conn: sqlite3.Connection):
    """Add columns introduced after the initial schema, if missing."""
    existing = {
        row[1]
        for row in conn.execute("PRAGMA table_info(webtoon_settings)").fetchall()
    }
    added = False
    for col, definition in [
        ("zoom_override",    "REAL"),
        ("custom_thumbnail", "TEXT"),
        ("source_url",       "TEXT"),
        ("bookmarked_chapters", "TEXT"),
        ("last_update_at",   "INTEGER"),
    ]:
        if col not in existing:
            conn.execute(f"ALTER TABLE webtoon_settings ADD COLUMN {col} {definition}")
            added = True
    if added:
        conn.commit()
        logger.info("Applied webtoon_settings column migrations")


def _migrate_thumbnails_table(conn: sqlite3.Connection):
    """Move legacy thumbnails table data into webtoon_settings, then drop it."""
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "thumbnails" not in tables:
        return

    rows = conn.execute("SELECT webtoon_name, path FROM thumbnails").fetchall()
    for row in rows:
        # Ensure a settings row exists, then set the thumbnail
        conn.execute(
            "INSERT OR IGNORE INTO webtoon_settings (webtoon_name) VALUES (?)",
            (row["webtoon_name"],)
        )
        conn.execute(
            "UPDATE webtoon_settings SET custom_thumbnail = ? WHERE webtoon_name = ?",
            (row["path"], row["webtoon_name"])
        )
    conn.execute("DROP TABLE thumbnails")
    conn.commit()
    logger.info("Migrated thumbnails table into webtoon_settings and dropped legacy table")


def _migrate_json(conn: sqlite3.Connection):
    _migrate_thumbnails_json(conn)
    _migrate_progress_json(conn)


def _migrate_thumbnails_json(conn: sqlite3.Connection):
    """Migrate thumbnails.json — works whether the legacy table still exists or not."""
    if not os.path.exists(THUMBNAILS_JSON):
        return

    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    if "thumbnails" in tables:
        # Legacy path: migrate into thumbnails table first (will be moved later)
        row_count = conn.execute("SELECT COUNT(*) FROM thumbnails").fetchone()[0]
        if row_count > 0:
            _backup_json(THUMBNAILS_JSON)
            return
        insert_sql = "INSERT OR IGNORE INTO thumbnails (webtoon_name, path) VALUES (?, ?)"
    else:
        # New path: migrate directly into webtoon_settings
        row_count = conn.execute(
            "SELECT COUNT(*) FROM webtoon_settings WHERE custom_thumbnail IS NOT NULL"
        ).fetchone()[0]
        if row_count > 0:
            _backup_json(THUMBNAILS_JSON)
            return

    try:
        with open(THUMBNAILS_JSON, "r", encoding="utf-8") as f:
            data: dict = json.load(f)

        if "thumbnails" in tables:
            conn.executemany(
                "INSERT OR IGNORE INTO thumbnails (webtoon_name, path) VALUES (?, ?)",
                data.items()
            )
        else:
            for name, path in data.items():
                conn.execute(
                    "INSERT OR IGNORE INTO webtoon_settings (webtoon_name) VALUES (?)",
                    (name,)
                )
                conn.execute(
                    "UPDATE webtoon_settings SET custom_thumbnail = ? WHERE webtoon_name = ?",
                    (path, name)
                )

        conn.commit()
        logger.info("Migrated %d thumbnail overrides from JSON", len(data))
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Could not migrate thumbnails.json", exc_info=e)

    _backup_json(THUMBNAILS_JSON)


def _migrate_progress_json(conn: sqlite3.Connection):
    if not os.path.exists(PROGRESS_JSON):
        return

    row_count = conn.execute("SELECT COUNT(*) FROM progress").fetchone()[0]
    if row_count > 0:
        _backup_json(PROGRESS_JSON)
        return

    try:
        with open(PROGRESS_JSON, "r", encoding="utf-8") as f:
            data: dict = json.load(f)
        rows = [
            (name, entry["chapter"], entry.get("scroll", 0.0), 0)
            for name, entry in data.items()
            if "chapter" in entry
        ]
        conn.executemany(
            """INSERT OR IGNORE INTO progress (webtoon_name, chapter, scroll, total_images)
               VALUES (?, ?, ?, ?)""",
            rows
        )
        conn.commit()
        logger.info("Migrated %d progress entries from JSON", len(rows))
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Could not migrate progress.json", exc_info=e)

    _backup_json(PROGRESS_JSON)


def _backup_json(path: str):
    bak = path + ".bak"
    try:
        os.rename(path, bak)
    except OSError:
        pass
