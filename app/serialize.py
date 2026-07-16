"""Serialise DB rows into JSON-safe dicts for the API and WebSocket payloads."""
from __future__ import annotations

import json

from app import repo

# stable per-agent colours for the room UI (assigned by agent id parity/kind)
_PALETTE = ["#e07a5f", "#3d8bdb", "#81b29a", "#c39bd3", "#f2cc8f", "#e29578", "#6c9a8b"]
_SYSTEM_COLORS = {"admin": "#2d3142", "search": "#5b8266"}


def agent_color(agent) -> str:
    if agent is None:
        return "#888888"
    if agent["kind"] in _SYSTEM_COLORS:
        return _SYSTEM_COLORS[agent["kind"]]
    return _PALETTE[(agent["id"]) % len(_PALETTE)]


def room_message(row, db=None) -> dict:
    agent = repo.get_agent(row["agent_id"], db=db) if row["agent_id"] else None
    return {
        "id": row["id"],
        "kind": row["kind"],
        "content": row["content"],
        "created_at": row["created_at"],
        "agent_id": row["agent_id"],
        "agent_name": agent["name"] if agent else ("System" if row["kind"] == "system" else "?"),
        "agent_kind": agent["kind"] if agent else "system",
        "color": agent_color(agent),
    }


def private_message(row) -> dict:
    return {
        "id": row["id"],
        "role": row["role"],
        "content": row["content"],
        "created_at": row["created_at"],
    }


def task_public(task, db=None) -> dict | None:
    if task is None:
        return None
    return {
        "id": task["id"],
        "type": task["type"],
        "status": task["status"],
        "iteration": task["iteration"],
        "params": json.loads(task["params_json"]),
        "result": json.loads(task["result_json"]) if task["result_json"] else None,
        "created_at": task["created_at"],
        "decided_at": task["decided_at"],
    }


def agent_public(agent) -> dict:
    return {
        "id": agent["id"],
        "kind": agent["kind"],
        "name": agent["name"],
        "color": agent_color(agent),
        "user_id": agent["user_id"],
    }
