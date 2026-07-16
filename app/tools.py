"""Person-agent tools (spec §6) — function-calling surface + strict validation.

The agent may only touch availability through these tools; it never writes free
text into the schedule. All validation is deterministic Python: on a bad call we
return a German error string as the tool result so the agent can correct itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from app import repo
from app.scheduling import _EVENING, _HALFDAY, PREF_WEIGHT

# OpenAI-compatible tool/function schemas (descriptions in German for the model).
TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "set_availability",
            "description": (
                "Speichert die Verfügbarkeiten des Users für den Terminzeitraum. "
                "Ersetzt ALLE bisher gespeicherten Slots. Nur Slots innerhalb des "
                "vorgegebenen Zeitraums verwenden."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "slots": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "start": {"type": "string", "description": "ISO-Startzeit YYYY-MM-DDTHH:MM"},
                                "end": {"type": "string", "description": "ISO-Endzeit YYYY-MM-DDTHH:MM"},
                                "preference": {
                                    "type": "string",
                                    "enum": ["yes", "maybe", "preferred"],
                                },
                            },
                            "required": ["start", "end", "preference"],
                        },
                    }
                },
                "required": ["slots"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_note",
            "description": (
                "Hält eine weiche Präferenz des Users als Freitext fest "
                "(z. B. 'lieber nicht Freitag', 'vegetarisches Restaurant')."
            ),
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mark_ready",
            "description": "Signalisiert, dass der User mit der Verfügbarkeitsangabe fertig ist.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


@dataclass
class ToolResult:
    ok: bool
    message: str  # returned to the agent as the tool result
    triggered_ready: bool = False


def _parse_dt(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _snap(start: datetime, granularity: str) -> tuple[str, str]:
    """Snap a free-form slot onto the canonical grid so that every agent's
    'Tuesday evening' resolves to the identical (start, end) key — otherwise
    slightly different end times would silently break the intersection."""
    templates = _HALFDAY if granularity == "halfday" else _EVENING
    if granularity == "halfday":
        start_t, end_t = templates[0] if start.hour < 13 else templates[1]
    else:
        start_t, end_t = templates[0]
    s = datetime.combine(start.date(), start_t)
    e = datetime.combine(start.date(), end_t)
    return s.isoformat(timespec="minutes"), e.isoformat(timespec="minutes")


def _validate_slots(
    slots: list[dict], range_start: date, range_end: date, granularity: str = "evening"
) -> tuple[list[dict], list[str]]:
    """Return (clean_slots, errors). Snaps each slot to the canonical grid, then
    merges duplicates keeping the strongest preference; drops out-of-range /
    malformed entries with an error note."""
    errors: list[str] = []
    merged: dict[tuple[str, str], str] = {}
    for i, raw in enumerate(slots):
        start = _parse_dt(raw.get("start", ""))
        end = _parse_dt(raw.get("end", ""))
        pref = raw.get("preference")
        if start is None or end is None:
            errors.append(f"Slot {i + 1}: Start/Ende nicht als ISO-Datum lesbar.")
            continue
        if end <= start:
            errors.append(f"Slot {i + 1}: Ende liegt nicht nach dem Start.")
            continue
        if not (range_start <= start.date() <= range_end):
            errors.append(f"Slot {i + 1}: {start.date()} liegt außerhalb des Zeitraums "
                          f"{range_start}–{range_end}.")
            continue
        if pref not in PREF_WEIGHT:
            errors.append(f"Slot {i + 1}: Präferenz '{pref}' ungültig (yes/maybe/preferred).")
            continue
        key = _snap(start, granularity)
        prev = merged.get(key)
        if prev is None or PREF_WEIGHT[pref] > PREF_WEIGHT[prev]:
            merged[key] = pref
    clean = [{"start": s, "end": e, "preference": p} for (s, e), p in sorted(merged.items())]
    return clean, errors


def apply_tool_call(
    user_id: int, name: str, args: dict, task_params: dict, db=None
) -> ToolResult:
    """Validate and apply one person-agent tool call. ``task_params`` carries
    ``range_start`` / ``range_end`` (ISO dates)."""
    if name == "set_availability":
        try:
            rs = date.fromisoformat(task_params["range_start"])
            re_ = date.fromisoformat(task_params["range_end"])
        except (KeyError, ValueError):
            return ToolResult(False, "Interner Fehler: Terminzeitraum unbekannt.")
        slots = args.get("slots") or []
        if not isinstance(slots, list) or not slots:
            return ToolResult(False, "Keine Slots übergeben. Bitte mindestens einen Termin angeben.")
        clean, errors = _validate_slots(slots, rs, re_, task_params.get("granularity", "evening"))
        if not clean:
            return ToolResult(False, "Kein gültiger Slot dabei: " + " ".join(errors))
        repo.replace_availability(user_id, clean, db=db)
        msg = f"{len(clean)} Verfügbarkeit(en) gespeichert."
        if errors:
            msg += " Hinweis: " + " ".join(errors)
        return ToolResult(True, msg)

    if name == "add_note":
        text = (args.get("text") or "").strip()
        if not text:
            return ToolResult(False, "Leere Notiz.")
        rows = repo.list_availability(user_id, db=db)
        # attach the note to the user's first slot, or store as a preference note row
        repo.add_private_message(user_id, "system", f"[Notiz gespeichert] {text}", db=db)
        # persist onto availability so it reaches the room summary
        if rows:
            db_ = db
            repo._db(db_).execute(
                "UPDATE availability SET note = ? WHERE id = ?", (text, rows[0]["id"])
            )
        return ToolResult(True, f"Notiz gespeichert: {text}")

    if name == "mark_ready":
        return ToolResult(True, "Als bereit markiert.", triggered_ready=True)

    return ToolResult(False, f"Unbekanntes Tool: {name}")
