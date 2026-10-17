"""Small SQLite persistence layer with explicit transactions."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Generator, Iterable, Optional, Sequence


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    media_type TEXT NOT NULL CHECK(media_type IN ('image','gif','video')),
    mime_type TEXT NOT NULL,
    width INTEGER,
    height INTEGER,
    duration_ms INTEGER,
    frame_rate REAL,
    file_size_bytes INTEGER NOT NULL,
    thumbnail_path TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS text_panels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    content TEXT NOT NULL,
    font_family TEXT NOT NULL DEFAULT 'default',
    font_size INTEGER NOT NULL DEFAULT 12,
    bold INTEGER NOT NULL DEFAULT 0,
    italic INTEGER NOT NULL DEFAULT 0,
    alignment TEXT NOT NULL DEFAULT 'center',
    color_mode TEXT NOT NULL DEFAULT 'solid',
    color_value TEXT NOT NULL DEFAULT '#ffffff',
    border_style TEXT NOT NULL DEFAULT 'none',
    border_color TEXT NOT NULL DEFAULT '#ffffff',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS playlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    is_default INTEGER NOT NULL DEFAULT 0,
    is_enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS playlist_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    item_type TEXT NOT NULL CHECK(item_type IN ('media','text_panel')),
    media_id INTEGER REFERENCES media(id) ON DELETE RESTRICT,
    text_panel_id INTEGER REFERENCES text_panels(id) ON DELETE RESTRICT,
    duration_ms INTEGER NOT NULL DEFAULT 5000,
    repeat_count INTEGER NOT NULL DEFAULT 1,
    transition_type TEXT NOT NULL DEFAULT 'none',
    transition_duration_ms INTEGER NOT NULL DEFAULT 500,
    fit_mode TEXT NOT NULL DEFAULT 'fit',
    CHECK (
      (item_type = 'media' AND media_id IS NOT NULL AND text_panel_id IS NULL) OR
      (item_type = 'text_panel' AND text_panel_id IS NOT NULL AND media_id IS NULL)
    ),
    UNIQUE(playlist_id, position)
);
CREATE INDEX IF NOT EXISTS idx_playlist_items_playlist ON playlist_items(playlist_id, position);
"""

# Additive column migrations, applied in order to databases created by older
# releases. SQLite only allows ADD COLUMN, so every entry must have a default.
COLUMN_MIGRATIONS = (
    ("playlists", "play_mode", "TEXT NOT NULL DEFAULT 'loop'"),
    ("playlists", "resume_position", "INTEGER NOT NULL DEFAULT 0"),
    ("playlist_items", "play_by", "TEXT NOT NULL DEFAULT 'time'"),
    ("text_panels", "effect", "TEXT NOT NULL DEFAULT 'none'"),
    ("text_panels", "outline", "INTEGER NOT NULL DEFAULT 1"),
    ("text_panels", "outline_color", "TEXT NOT NULL DEFAULT '#000000'"),
    ("text_panels", "accent_color", "TEXT NOT NULL DEFAULT '#ffd400'"),
    ("text_panels", "background_color", "TEXT NOT NULL DEFAULT '#000000'"),
    ("text_panels", "motion_speed", "TEXT NOT NULL DEFAULT 'normal'"),
    (
        "text_panels",
        "background_media_id",
        "INTEGER DEFAULT NULL REFERENCES media(id) ON DELETE RESTRICT",
    ),
)


class Database:
    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            for table, column, definition in COLUMN_MIGRATIONS:
                existing = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
                if column not in existing:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def fetch_one(
        self, query: str, parameters: Sequence[object] = ()
    ) -> Optional[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(query, parameters).fetchone()

    def fetch_all(
        self, query: str, parameters: Sequence[object] = ()
    ) -> Iterable[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(query, parameters).fetchall()

