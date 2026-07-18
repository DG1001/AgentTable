"""Context builders shared by agents and the moderator loop.

Keeps prompt-context assembly (task description, user availability summaries,
recent room messages, running summary) in one place so person-, admin- and
search-agents all see consistent, disciplined context (spec §5.1 context rules).
"""
from __future__ import annotations

import json

from app import repo
from app.scheduling import Slot


def task_context_text(task) -> str:
    """One-paragraph German description of the active task for a system prompt."""
    if task is None:
        return "Aktuell läuft keine Terminfindung."
    p = json.loads(task["params_json"])
    desc = p.get("description") or "Gemeinsames Treffen"
    return (
        f"Terminfindung „{desc}“ — Zeitraum {p.get('range_start')} bis {p.get('range_end')} "
        f"({p.get('granularity', 'evening')}). Status: {task['status']}."
    )


def user_availability_summary(user_id: int, db=None) -> str:
    """Bulleted German summary of a user's slots + notes for the room prompt."""
    rows = repo.list_availability(user_id, db=db)
    if not rows:
        return "Noch keine Verfügbarkeiten hinterlegt."
    pref_label = {"preferred": "⭐ Wunsch", "yes": "geht", "maybe": "zur Not"}
    lines = []
    for r in rows:
        label = Slot(r["slot_start"], r["slot_end"]).label()
        note = f" — {r['note']}" if r["note"] else ""
        lines.append(f"- {label}: {pref_label.get(r['preference'], r['preference'])}{note}")
    return "\n".join(lines)


def user_memory_text(user_id: int, db=None) -> str:
    """Bulleted long-term memory about the user (or a hint that there's none yet)."""
    rows = repo.list_memories(user_id, db=db)
    if not rows:
        return "(noch nichts gemerkt)"
    return "\n".join(f"- {r['content']}" for r in rows)


def recent_room_text(group_id: int, k: int = 10, db=None) -> str:
    """Last ``k`` room messages rendered as 'Name: text' lines."""
    rows = repo.list_room_messages(group_id, limit=k, db=db)
    if not rows:
        return "(noch keine Nachrichten)"
    out = []
    for r in rows:
        if r["kind"] == "system":
            out.append(f"[System] {r['content']}")
        else:
            agent = repo.get_agent(r["agent_id"], db=db) if r["agent_id"] else None
            name = agent["name"] if agent else "?"
            out.append(f"{name}: {r['content']}")
    return "\n".join(out)


def running_summary(task_id: int, db=None) -> str:
    """The moderator's rolling summary stored on the admin agent state."""
    task = repo.get_task(task_id, db=db)
    if not task:
        return ""
    admin = repo.get_agent_by_kind(task["group_id"], "admin", db=db)
    if not admin:
        return ""
    return repo.get_agent_state(admin["id"], f"summary:{task_id}", "", db=db)
