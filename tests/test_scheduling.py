"""Unit tests for the deterministic scheduling engine (spec §6.5 — the core)."""
from datetime import date

from app.scheduling import (
    compute_candidates,
    generate_slots,
    has_full_intersection,
    render_candidate_table,
)


def _av(user_id, start, end, pref):
    return {"user_id": user_id, "slot_start": start, "slot_end": end, "preference": pref}


def test_generate_slots_evening():
    slots = generate_slots(date(2026, 7, 20), date(2026, 7, 22), "evening")
    assert len(slots) == 3
    assert slots[0].start == "2026-07-20T18:00"
    assert slots[0].end == "2026-07-20T22:00"


def test_generate_slots_halfday():
    slots = generate_slots(date(2026, 7, 20), date(2026, 7, 21), "halfday")
    assert len(slots) == 4  # 2 days * 2 slots


def test_full_intersection_ranking():
    s1 = ("2026-07-20T18:00", "2026-07-20T22:00")
    s2 = ("2026-07-21T18:00", "2026-07-21T22:00")
    av = [
        _av(1, *s1, "preferred"), _av(2, *s1, "yes"), _av(3, *s1, "yes"),
        _av(1, *s2, "yes"), _av(2, *s2, "yes"), _av(3, *s2, "yes"),
    ]
    cands = compute_candidates(av, [1, 2, 3])
    assert has_full_intersection(cands)
    # both are full (3/3); s1 wins on stronger preference (one 'preferred')
    assert cands[0].start == s1[0]
    assert cands[0].full and cands[0].coverage == 3
    assert cands[0].preferred == 1


def test_coverage_beats_preference():
    s_all = ("2026-07-20T18:00", "2026-07-20T22:00")
    s_partial = ("2026-07-21T18:00", "2026-07-21T22:00")
    av = [
        _av(1, *s_all, "yes"), _av(2, *s_all, "yes"), _av(3, *s_all, "yes"),
        _av(1, *s_partial, "preferred"), _av(2, *s_partial, "preferred"),
    ]
    cands = compute_candidates(av, [1, 2, 3])
    # full coverage slot ranks first even though the other has stronger prefs
    assert cands[0].start == s_all[0]
    assert cands[0].coverage == 3
    assert cands[1].coverage == 2
    assert cands[1].missing_users == [3]


def test_duplicate_keeps_strongest_preference():
    s = ("2026-07-20T18:00", "2026-07-20T22:00")
    av = [_av(1, *s, "maybe"), _av(1, *s, "preferred")]
    cands = compute_candidates(av, [1])
    assert cands[0].preferred == 1
    assert cands[0].maybe == 0


def test_no_intersection():
    av = [
        _av(1, "2026-07-20T18:00", "2026-07-20T22:00", "yes"),
        _av(2, "2026-07-21T18:00", "2026-07-21T22:00", "yes"),
    ]
    cands = compute_candidates(av, [1, 2])
    assert not has_full_intersection(cands)
    assert all(c.coverage == 1 for c in cands)


def test_ignores_non_participants():
    s = ("2026-07-20T18:00", "2026-07-20T22:00")
    av = [_av(1, *s, "yes"), _av(99, *s, "yes")]
    cands = compute_candidates(av, [1])
    assert cands[0].coverage == 1


def test_render_table_marks_full():
    s = ("2026-07-20T18:00", "2026-07-20T22:00")
    cands = compute_candidates([_av(1, *s, "yes")], [1])
    table = render_candidate_table(cands)
    assert "✅" in table and "Termin" in table
