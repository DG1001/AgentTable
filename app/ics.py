"""iCalendar (RFC 5545) export for a decided meeting — pure, no I/O.

Turns a decided ``task`` into a single-VEVENT .ics string. Slot times are naive
Europe/Berlin wall-clock (see ``app/scheduling.py``); we convert them to UTC via
``zoneinfo`` and emit ``...Z`` values, which every calendar client accepts without
needing a VTIMEZONE block.
"""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import settings

_UTC = ZoneInfo("UTC")


def _esc(text: str) -> str:
    """Escape a text value per RFC 5545 (backslash, comma, semicolon, newline)."""
    return (text.replace("\\", "\\\\").replace(",", "\\,")
            .replace(";", "\\;").replace("\n", "\\n"))


def _to_utc(iso_local: str) -> str:
    """Naive Europe/Berlin ISO string -> 'YYYYMMDDTHHMMSSZ' in UTC."""
    dt = datetime.fromisoformat(iso_local).replace(tzinfo=ZoneInfo(settings.timezone))
    return dt.astimezone(_UTC).strftime("%Y%m%dT%H%M%SZ")


def build_ics(task_row, group_row, member_names: list[str]) -> str:
    """Build the .ics text for a decided task. Assumes ``task_row['result_json']``
    holds ``{slot:{start,end,label}, location?, summary?}``."""
    result = json.loads(task_row["result_json"]) if task_row["result_json"] else {}
    params = json.loads(task_row["params_json"]) if task_row["params_json"] else {}
    slot = result.get("slot", {})
    title = params.get("description") or "Treffen"
    location = result.get("location") or ""
    summary = result.get("summary") or ""
    people = ", ".join(member_names)
    desc_parts = [p for p in (summary, f"Mit: {people}" if people else "",
                              f"Gruppe: {group_row['name']}") if p]

    uid = f"{task_row['id']}-{task_row['share_token'] or 'x'}@agenttable"
    now = datetime.now(_UTC).strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//AgentTable//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{now}",
        f"DTSTART:{_to_utc(slot['start'])}",
        f"DTEND:{_to_utc(slot['end'])}",
        f"SUMMARY:{_esc(title)}",
    ]
    if location:
        lines.append(f"LOCATION:{_esc(location)}")
    if desc_parts:
        lines.append(f"DESCRIPTION:{_esc(' — '.join(desc_parts))}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"
