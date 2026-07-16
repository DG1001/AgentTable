"""SQLite persistence layer.

Thin wrapper around ``sqlite3`` (no ORM — spec §3 asks to keep it simple but
migration-capable). A single shared connection in WAL mode is guarded by a
re-entrant lock; concurrency needs are tiny (a handful of groups, one process).

Schema versioning is a plain ``schema_version`` table plus an ordered list of
migration steps, so the DB upgrades itself on startup.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

# --- migrations ------------------------------------------------------------
# Each entry is (version, sql). Applied in order for versions above the current
# schema_version. Never edit a shipped migration — append a new one.
MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE "group" (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE user (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id      INTEGER NOT NULL REFERENCES "group"(id),
            token         TEXT UNIQUE NOT NULL,
            display_name  TEXT NOT NULL,
            persona       TEXT,
            created_at    TEXT NOT NULL DEFAULT (datetime('now')),
            last_seen_at  TEXT
        );

        CREATE TABLE agent (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id  INTEGER NOT NULL REFERENCES "group"(id),
            kind      TEXT NOT NULL CHECK (kind IN ('person','admin','search')),
            user_id   INTEGER REFERENCES user(id),
            name      TEXT NOT NULL,
            persona   TEXT
        );

        CREATE TABLE private_message (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES user(id),
            role        TEXT NOT NULL CHECK (role IN ('user','agent','system')),
            content     TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE room_message (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id    INTEGER NOT NULL REFERENCES "group"(id),
            agent_id    INTEGER REFERENCES agent(id),
            kind        TEXT NOT NULL CHECK (kind IN ('agent','system')),
            content     TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE availability (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES user(id),
            slot_start  TEXT NOT NULL,
            slot_end    TEXT NOT NULL,
            preference  TEXT NOT NULL CHECK (preference IN ('yes','maybe','preferred')),
            note        TEXT,
            updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE agent_state (
            agent_id    INTEGER NOT NULL REFERENCES agent(id),
            key         TEXT NOT NULL,
            value_json  TEXT NOT NULL,
            PRIMARY KEY (agent_id, key)
        );

        CREATE TABLE task (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id     INTEGER NOT NULL REFERENCES "group"(id),
            type         TEXT NOT NULL CHECK (type IN ('schedule_meeting')),
            status       TEXT NOT NULL CHECK (status IN ('collecting','negotiating','decided','failed')),
            params_json  TEXT NOT NULL DEFAULT '{}',
            result_json  TEXT,
            iteration    INTEGER NOT NULL DEFAULT 0,
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            decided_at   TEXT
        );

        CREATE TABLE llm_usage (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id       INTEGER,
            task_id        INTEGER,
            role           TEXT NOT NULL,
            model          TEXT NOT NULL,
            prompt_tokens  INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            created_at     TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX idx_private_message_user ON private_message(user_id, id);
        CREATE INDEX idx_room_message_group ON room_message(group_id, id);
        CREATE INDEX idx_availability_user ON availability(user_id);
        CREATE INDEX idx_agent_group ON agent(group_id);
        """,
    ),
]


class Database:
    """Shared SQLite handle with dict-row access and schema migration."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
            )
            row = self._conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            current = row["v"] or 0
            for version, sql in MIGRATIONS:
                if version > current:
                    self._conn.executescript(sql)
                    self._conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
            self._conn.commit()

    # --- low-level helpers -------------------------------------------------
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchone()

    def insert(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return int(cur.lastrowid)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


_db: Database | None = None


def get_db() -> Database:
    """Return the process-wide database, creating it on first use."""
    global _db
    if _db is None:
        from app.config import settings

        _db = Database(settings.db_path)
    return _db


def set_db(db: Database) -> None:
    """Override the global DB (used by tests with an isolated file)."""
    global _db
    _db = db
