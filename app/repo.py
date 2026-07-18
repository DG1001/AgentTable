"""Repository functions — all SQL lives here, callers stay declarative.

Rows are returned as ``sqlite3.Row`` (dict-like). Timestamps are stored as UTC
ISO strings by SQLite's ``datetime('now')``; slot datetimes are naive ISO
strings in Europe/Berlin wall-clock (MVP simplification, spec §4).
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from typing import Any

from app.db import Database, get_db


def _db(db: Database | None) -> Database:
    return db or get_db()


# --- groups ---------------------------------------------------------------
def create_group(name: str, db: Database | None = None) -> int:
    return _db(db).insert('INSERT INTO "group" (name) VALUES (?)', (name,))


def get_group(group_id: int, db: Database | None = None) -> sqlite3.Row | None:
    return _db(db).query_one('SELECT * FROM "group" WHERE id = ?', (group_id,))


def list_groups(db: Database | None = None) -> list[sqlite3.Row]:
    return _db(db).query('SELECT * FROM "group" ORDER BY id')


# --- users ----------------------------------------------------------------
def create_user(group_id: int, display_name: str, db: Database | None = None) -> tuple[int, str]:
    token = secrets.token_urlsafe(24)
    uid = _db(db).insert(
        "INSERT INTO user (group_id, token, display_name) VALUES (?, ?, ?)",
        (group_id, token, display_name),
    )
    return uid, token


def get_user(user_id: int, db: Database | None = None) -> sqlite3.Row | None:
    return _db(db).query_one("SELECT * FROM user WHERE id = ?", (user_id,))


def get_user_by_token(token: str, db: Database | None = None) -> sqlite3.Row | None:
    return _db(db).query_one("SELECT * FROM user WHERE token = ?", (token,))


def list_users(group_id: int, db: Database | None = None) -> list[sqlite3.Row]:
    return _db(db).query("SELECT * FROM user WHERE group_id = ? ORDER BY id", (group_id,))


def set_persona(user_id: int, persona: str, db: Database | None = None) -> None:
    _db(db).execute("UPDATE user SET persona = ? WHERE id = ?", (persona, user_id))
    # keep the person-agent's persona in sync
    _db(db).execute(
        "UPDATE agent SET persona = ? WHERE user_id = ? AND kind = 'person'",
        (persona, user_id),
    )


def touch_user(user_id: int, db: Database | None = None) -> None:
    _db(db).execute("UPDATE user SET last_seen_at = datetime('now') WHERE id = ?", (user_id,))


# --- long-term per-user memory -------------------------------------------
def add_memory(user_id: int, content: str, cap: int = 50, db: Database | None = None) -> bool:
    """Store a durable fact about the user (deduped, capped). Returns True if added."""
    content = (content or "").strip()
    if not content:
        return False
    d = _db(db)
    existing = d.query(
        "SELECT lower(content) AS c FROM user_memory WHERE user_id = ?", (user_id,)
    )
    if content.lower() in {r["c"] for r in existing}:
        return False  # exact dedupe
    d.execute("INSERT INTO user_memory (user_id, content) VALUES (?, ?)", (user_id, content))
    # cap: drop the oldest beyond `cap`
    d.execute(
        "DELETE FROM user_memory WHERE user_id = ? AND id NOT IN "
        "(SELECT id FROM user_memory WHERE user_id = ? ORDER BY id DESC LIMIT ?)",
        (user_id, user_id, cap),
    )
    return True


def list_memories(user_id: int, limit: int | None = None, db: Database | None = None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM user_memory WHERE user_id = ? ORDER BY id"
    params: list = [user_id]
    if limit is not None:
        sql = "SELECT * FROM user_memory WHERE user_id = ? ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = _db(db).query(sql, params)
        return list(reversed(rows))
    return _db(db).query(sql, params)


def delete_memory(memory_id: int, db: Database | None = None) -> None:
    _db(db).execute("DELETE FROM user_memory WHERE id = ?", (memory_id,))


# --- agents ---------------------------------------------------------------
def create_agent(
    group_id: int,
    kind: str,
    name: str,
    user_id: int | None = None,
    persona: str | None = None,
    db: Database | None = None,
) -> int:
    return _db(db).insert(
        "INSERT INTO agent (group_id, kind, user_id, name, persona) VALUES (?, ?, ?, ?, ?)",
        (group_id, kind, user_id, name, persona),
    )


def get_agent(agent_id: int, db: Database | None = None) -> sqlite3.Row | None:
    return _db(db).query_one("SELECT * FROM agent WHERE id = ?", (agent_id,))


def get_person_agent(user_id: int, db: Database | None = None) -> sqlite3.Row | None:
    return _db(db).query_one(
        "SELECT * FROM agent WHERE user_id = ? AND kind = 'person'", (user_id,)
    )


def get_agent_by_kind(group_id: int, kind: str, db: Database | None = None) -> sqlite3.Row | None:
    return _db(db).query_one(
        "SELECT * FROM agent WHERE group_id = ? AND kind = ? LIMIT 1", (group_id, kind)
    )


def get_agent_by_name(group_id: int, name: str, db: Database | None = None) -> sqlite3.Row | None:
    return _db(db).query_one(
        "SELECT * FROM agent WHERE group_id = ? AND name = ? LIMIT 1", (group_id, name)
    )


def list_agents(group_id: int, db: Database | None = None) -> list[sqlite3.Row]:
    return _db(db).query("SELECT * FROM agent WHERE group_id = ? ORDER BY id", (group_id,))


def list_person_agents(group_id: int, db: Database | None = None) -> list[sqlite3.Row]:
    return _db(db).query(
        "SELECT * FROM agent WHERE group_id = ? AND kind = 'person' ORDER BY id", (group_id,)
    )


# --- agent_state (key/value json) ----------------------------------------
def set_agent_state(agent_id: int, key: str, value: Any, db: Database | None = None) -> None:
    _db(db).execute(
        "INSERT INTO agent_state (agent_id, key, value_json) VALUES (?, ?, ?) "
        "ON CONFLICT(agent_id, key) DO UPDATE SET value_json = excluded.value_json",
        (agent_id, key, json.dumps(value)),
    )


def get_agent_state(agent_id: int, key: str, default: Any = None, db: Database | None = None) -> Any:
    row = _db(db).query_one(
        "SELECT value_json FROM agent_state WHERE agent_id = ? AND key = ?", (agent_id, key)
    )
    return json.loads(row["value_json"]) if row else default


# --- private messages -----------------------------------------------------
def add_private_message(user_id: int, role: str, content: str, db: Database | None = None) -> int:
    return _db(db).insert(
        "INSERT INTO private_message (user_id, role, content) VALUES (?, ?, ?)",
        (user_id, role, content),
    )


def list_private_messages(
    user_id: int, limit: int | None = None, since: int | None = None, db: Database | None = None
) -> list[sqlite3.Row]:
    if since is not None:
        return _db(db).query(
            "SELECT * FROM private_message WHERE user_id = ? AND id > ? ORDER BY id",
            (user_id, since),
        )
    if limit is None:
        return _db(db).query(
            "SELECT * FROM private_message WHERE user_id = ? ORDER BY id", (user_id,)
        )
    rows = _db(db).query(
        "SELECT * FROM private_message WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    )
    return list(reversed(rows))


# --- room messages --------------------------------------------------------
def add_room_message(
    group_id: int, kind: str, content: str, agent_id: int | None = None, db: Database | None = None
) -> int:
    return _db(db).insert(
        "INSERT INTO room_message (group_id, agent_id, kind, content) VALUES (?, ?, ?, ?)",
        (group_id, agent_id, kind, content),
    )


def get_room_message(message_id: int, db: Database | None = None) -> sqlite3.Row | None:
    return _db(db).query_one("SELECT * FROM room_message WHERE id = ?", (message_id,))


def list_room_messages(
    group_id: int, limit: int | None = None, since: int | None = None, db: Database | None = None
) -> list[sqlite3.Row]:
    if since is not None:
        return _db(db).query(
            "SELECT * FROM room_message WHERE group_id = ? AND id > ? ORDER BY id",
            (group_id, since),
        )
    if limit is None:
        return _db(db).query(
            "SELECT * FROM room_message WHERE group_id = ? ORDER BY id", (group_id,)
        )
    rows = _db(db).query(
        "SELECT * FROM room_message WHERE group_id = ? ORDER BY id DESC LIMIT ?",
        (group_id, limit),
    )
    return list(reversed(rows))


# --- availability ---------------------------------------------------------
def replace_availability(
    user_id: int, slots: list[dict], db: Database | None = None
) -> None:
    """Replace ALL slots of a user (spec: set_availability replaces the range)."""
    d = _db(db)
    d.execute("DELETE FROM availability WHERE user_id = ?", (user_id,))
    for s in slots:
        d.execute(
            "INSERT INTO availability (user_id, slot_start, slot_end, preference, note) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, s["start"], s["end"], s["preference"], s.get("note")),
        )


def list_availability(user_id: int, db: Database | None = None) -> list[sqlite3.Row]:
    return _db(db).query(
        "SELECT * FROM availability WHERE user_id = ? ORDER BY slot_start", (user_id,)
    )


def list_group_availability(group_id: int, db: Database | None = None) -> list[sqlite3.Row]:
    return _db(db).query(
        "SELECT a.* FROM availability a JOIN user u ON a.user_id = u.id "
        "WHERE u.group_id = ? ORDER BY a.slot_start",
        (group_id,),
    )


# --- tasks ----------------------------------------------------------------
def create_task(
    group_id: int, params: dict, db: Database | None = None, type_: str = "schedule_meeting"
) -> int:
    return _db(db).insert(
        "INSERT INTO task (group_id, type, status, params_json) VALUES (?, ?, 'collecting', ?)",
        (group_id, type_, json.dumps(params)),
    )


def get_task(task_id: int, db: Database | None = None) -> sqlite3.Row | None:
    return _db(db).query_one("SELECT * FROM task WHERE id = ?", (task_id,))


def set_task_share_token(task_id: int, token: str, db: Database | None = None) -> None:
    _db(db).execute("UPDATE task SET share_token = ? WHERE id = ?", (token, task_id))


def get_task_by_share_token(token: str, db: Database | None = None) -> sqlite3.Row | None:
    if not token:
        return None
    return _db(db).query_one("SELECT * FROM task WHERE share_token = ?", (token,))


def ensure_share_token(task_id: int, db: Database | None = None) -> str | None:
    """Return the task's share token, generating one lazily for decided tasks that
    predate the feature. Returns None if the task isn't decided."""
    task = get_task(task_id, db=db)
    if task is None or task["status"] != "decided":
        return None
    if task["share_token"]:
        return task["share_token"]
    token = secrets.token_urlsafe(12)
    set_task_share_token(task_id, token, db=db)
    return token


def get_active_task(group_id: int, db: Database | None = None) -> sqlite3.Row | None:
    return _db(db).query_one(
        "SELECT * FROM task WHERE group_id = ? AND status IN ('collecting','negotiating') "
        "ORDER BY id DESC LIMIT 1",
        (group_id,),
    )


def latest_task(group_id: int, db: Database | None = None) -> sqlite3.Row | None:
    return _db(db).query_one(
        "SELECT * FROM task WHERE group_id = ? ORDER BY id DESC LIMIT 1", (group_id,)
    )


def update_task_status(
    task_id: int, status: str, result: dict | None = None, db: Database | None = None
) -> None:
    d = _db(db)
    if status == "decided":
        d.execute(
            "UPDATE task SET status = ?, result_json = ?, decided_at = datetime('now') WHERE id = ?",
            (status, json.dumps(result) if result is not None else None, task_id),
        )
    else:
        d.execute(
            "UPDATE task SET status = ?, result_json = COALESCE(?, result_json) WHERE id = ?",
            (status, json.dumps(result) if result is not None else None, task_id),
        )


def update_task_result(task_id: int, result: dict, db: Database | None = None) -> None:
    """Overwrite result_json without touching status/decided_at (e.g. venue edit)."""
    _db(db).execute("UPDATE task SET result_json = ? WHERE id = ?", (json.dumps(result), task_id))


def bump_task_iteration(task_id: int, db: Database | None = None) -> int:
    d = _db(db)
    d.execute("UPDATE task SET iteration = iteration + 1 WHERE id = ?", (task_id,))
    row = d.query_one("SELECT iteration FROM task WHERE id = ?", (task_id,))
    return int(row["iteration"])


# --- llm usage / budget ---------------------------------------------------
def log_usage(
    role: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    group_id: int | None = None,
    task_id: int | None = None,
    db: Database | None = None,
) -> None:
    _db(db).execute(
        "INSERT INTO llm_usage (group_id, task_id, role, model, prompt_tokens, completion_tokens) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (group_id, task_id, role, model, prompt_tokens, completion_tokens),
    )


def count_task_calls(task_id: int, db: Database | None = None) -> int:
    row = _db(db).query_one("SELECT COUNT(*) AS c FROM llm_usage WHERE task_id = ?", (task_id,))
    return int(row["c"]) if row else 0
