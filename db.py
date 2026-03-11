"""
db.py
Owns the single SQLite connection for the app.
All other stores import `get_connection()` from here.

Database file: data/reader.db
Schema:
    thumbnails  – custom thumbnail overrides  (replaces data/thumbnails.json)
    progress    – reading progress per webtoon (replaces data/progress.json)

On first run this module also migrates any existing JSON files into the
database so users don't lose their data, then renames them to .bak.
"""

import json
import os
import sqlite3

DB_PATH           = "data/reader.db"
THUMBNAILS_JSON   = "data/thumbnails.json"
PROGRESS_JSON     = "data/progress.json"

_connection: sqlite3.Connection | None = None


def get_connection() -> sqlite3.Connection:
    global _connection
    if _connection is None:
        _connection = _init_db()
    return _connection


# ---------------------------------------------------------------------------
#  Internal
# ---------------------------------------------------------------------------

def _init_db() -> sqlite3.Connection:
    os.makedirs("data", exist_ok=True)

    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    # Keep writes fast while still being crash-safe
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    _create_schema(conn)
    _migrate_json(conn)

    return conn


def _create_schema(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS thumbnails (
            webtoon_name  TEXT PRIMARY KEY,
            path          TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS progress (
            webtoon_name  TEXT PRIMARY KEY,
            chapter       TEXT NOT NULL,
            scroll        REAL NOT NULL DEFAULT 0.0,
            updated_at    INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
        );
    """)
    conn.commit()


def _migrate_json(conn: sqlite3.Connection):
    """
    One-time migration: if legacy JSON files exist and the tables are empty,
    import their data then rename the files to .bak so migration won't repeat.
    """
    _migrate_thumbnails_json(conn)
    _migrate_progress_json(conn)


def _migrate_thumbnails_json(conn: sqlite3.Connection):
    if not os.path.exists(THUMBNAILS_JSON):
        return

    row_count = conn.execute("SELECT COUNT(*) FROM thumbnails").fetchone()[0]
    if row_count > 0:
        # Data already present — just remove the old file
        _backup_json(THUMBNAILS_JSON)
        return

    try:
        with open(THUMBNAILS_JSON, "r", encoding="utf-8") as f:
            data: dict = json.load(f)
        conn.executemany(
            "INSERT OR IGNORE INTO thumbnails (webtoon_name, path) VALUES (?, ?)",
            data.items()
        )
        conn.commit()
        print(f"[db] Migrated {len(data)} thumbnail overrides from JSON.")
    except (json.JSONDecodeError, OSError) as e:
        print(f"[db] Could not migrate thumbnails.json: {e}")

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
            (name, entry["chapter"], entry.get("scroll", 0.0))
            for name, entry in data.items()
            if "chapter" in entry
        ]
        conn.executemany(
            """INSERT OR IGNORE INTO progress (webtoon_name, chapter, scroll)
               VALUES (?, ?, ?)""",
            rows
        )
        conn.commit()
        print(f"[db] Migrated {len(rows)} progress entries from JSON.")
    except (json.JSONDecodeError, OSError) as e:
        print(f"[db] Could not migrate progress.json: {e}")

    _backup_json(PROGRESS_JSON)


def _backup_json(path: str):
    bak = path + ".bak"
    try:
        os.rename(path, bak)
    except OSError:
        pass