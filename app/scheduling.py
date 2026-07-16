"""Deterministic scheduling engine (spec §6, step 5) — the hard core.

Pure functions only: no DB, no LLM, no clock. Given raw availability records
and the set of participants, it computes candidate meeting slots and ranks them.
Agents never do slot arithmetic — they only ever talk *about* the output here.

Slots are discrete (evening / half-day granularity) and identified by their
``(start, end)`` ISO string pair, so intersection is exact set matching.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

# preference strength -> weight, drives the secondary ranking
PREF_WEIGHT = {"preferred": 3, "yes": 2, "maybe": 1}

# slot time templates per granularity (Europe/Berlin wall-clock, tz-naive MVP)
_EVENING = [(time(18, 0), time(22, 0))]
_HALFDAY = [(time(9, 0), time(13, 0)), (time(14, 0), time(22, 0))]


@dataclass(frozen=True)
class Slot:
    start: str  # ISO "YYYY-MM-DDTHH:MM"
    end: str

    def label(self) -> str:
        """Human label in German, e.g. 'Sa 19.07. abends'."""
        s = datetime.fromisoformat(self.start)
        e = datetime.fromisoformat(self.end)
        wd = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"][s.weekday()]
        part = _daypart(s, e)
        return f"{wd} {s.strftime('%d.%m.')} {part}"


def _daypart(s: datetime, e: datetime) -> str:
    if s.hour >= 17:
        return "abends"
    if s.hour < 12:
        return "vormittags"
    return "nachmittags"


@dataclass
class Candidate:
    """A ranked candidate slot with per-preference tallies."""

    start: str
    end: str
    total: int
    available_users: list[int] = field(default_factory=list)
    missing_users: list[int] = field(default_factory=list)
    preferred: int = 0
    yes: int = 0
    maybe: int = 0

    @property
    def coverage(self) -> int:
        return len(self.available_users)

    @property
    def full(self) -> bool:
        """True when every participant can attend."""
        return self.coverage == self.total and self.total > 0

    @property
    def weighted(self) -> int:
        return self.preferred * 3 + self.yes * 2 + self.maybe * 1

    @property
    def sort_key(self) -> tuple:
        # more people first, then stronger preferences, then earlier slot
        return (-self.coverage, -self.weighted, self.start)

    def slot(self) -> Slot:
        return Slot(self.start, self.end)

    def label(self) -> str:
        return self.slot().label()

    def to_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "label": self.label(),
            "coverage": self.coverage,
            "total": self.total,
            "full": self.full,
            "preferred": self.preferred,
            "yes": self.yes,
            "maybe": self.maybe,
            "available_users": self.available_users,
            "missing_users": self.missing_users,
        }


def generate_slots(range_start: date, range_end: date, granularity: str = "evening") -> list[Slot]:
    """Enumerate all candidate slots in ``[range_start, range_end]`` inclusive.

    ``granularity`` is 'evening' (one slot/day) or 'halfday' (two slots/day).
    """
    templates = _HALFDAY if granularity == "halfday" else _EVENING
    slots: list[Slot] = []
    day = range_start
    while day <= range_end:
        for start_t, end_t in templates:
            start = datetime.combine(day, start_t)
            end = datetime.combine(day, end_t)
            slots.append(Slot(start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes")))
        day += timedelta(days=1)
    return slots


def compute_candidates(
    availability: list[dict],
    participant_ids: list[int],
    top_n: int = 5,
) -> list[Candidate]:
    """Rank meeting slots from raw availability.

    ``availability`` items are dicts with keys ``user_id, slot_start, slot_end,
    preference``. Only participants in ``participant_ids`` are counted. Slots are
    grouped by ``(start, end)``; the best ``top_n`` are returned sorted by
    coverage, then weighted preference, then time. Duplicate (user, slot) entries
    keep the strongest preference.
    """
    participants = set(participant_ids)
    total = len(participants)
    # (start,end) -> {user_id: preference}
    grouped: dict[tuple[str, str], dict[int, str]] = {}
    for row in availability:
        uid = row["user_id"]
        if uid not in participants:
            continue
        key = (row["slot_start"], row["slot_end"])
        prefs = grouped.setdefault(key, {})
        prev = prefs.get(uid)
        pref = row["preference"]
        if prev is None or PREF_WEIGHT[pref] > PREF_WEIGHT[prev]:
            prefs[uid] = pref

    candidates: list[Candidate] = []
    for (start, end), prefs in grouped.items():
        cand = Candidate(start=start, end=end, total=total, available_users=sorted(prefs.keys()))
        cand.missing_users = sorted(participants - set(prefs.keys()))
        for pref in prefs.values():
            setattr(cand, pref, getattr(cand, pref) + 1)
        candidates.append(cand)

    candidates.sort(key=lambda c: c.sort_key)
    return candidates[:top_n]


def has_full_intersection(candidates: list[Candidate]) -> bool:
    """True if at least one candidate works for everyone."""
    return any(c.full for c in candidates)


def find_candidate(candidates: list[Candidate], start: str, end: str) -> Candidate | None:
    """Look up a candidate by its slot (used to validate the admin's decision)."""
    for c in candidates:
        if c.start == start and c.end == end:
            return c
    return None


def render_candidate_table(candidates: list[Candidate]) -> str:
    """Markdown table of candidates for posting into the room."""
    if not candidates:
        return "_Keine gemeinsamen Termine gefunden._"
    lines = [
        "| # | Termin | Verfügbar | ⭐ bevorzugt | ✓ ja | ~ vielleicht |",
        "|---|--------|-----------|-------------|------|--------------|",
    ]
    for i, c in enumerate(candidates, 1):
        cov = f"{c.coverage}/{c.total}" + (" ✅" if c.full else "")
        lines.append(f"| {i} | {c.label()} | {cov} | {c.preferred} | {c.yes} | {c.maybe} |")
    return "\n".join(lines)
