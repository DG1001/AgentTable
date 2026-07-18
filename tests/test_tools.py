"""Tests for strict person-agent tool validation (spec §6)."""
from app import repo
from app.service import bootstrap_group
from app.tools import apply_tool_call

PARAMS = {"range_start": "2026-07-20", "range_end": "2026-07-27", "granularity": "evening"}


def _user(db):
    _, members = bootstrap_group("G", ["Alex"])
    return members[0]["user_id"]


def test_set_availability_valid(db):
    uid = _user(db)
    res = apply_tool_call(uid, "set_availability", {
        "slots": [{"start": "2026-07-21T18:00", "end": "2026-07-21T22:00", "preference": "yes"}]
    }, PARAMS)
    assert res.ok
    assert len(repo.list_availability(uid)) == 1


def test_out_of_range_rejected(db):
    uid = _user(db)
    res = apply_tool_call(uid, "set_availability", {
        "slots": [{"start": "2026-08-30T18:00", "end": "2026-08-30T22:00", "preference": "yes"}]
    }, PARAMS)
    assert not res.ok
    assert repo.list_availability(uid) == []


def test_duplicate_merged_strongest(db):
    uid = _user(db)
    res = apply_tool_call(uid, "set_availability", {
        "slots": [
            {"start": "2026-07-21T18:00", "end": "2026-07-21T22:00", "preference": "maybe"},
            {"start": "2026-07-21T18:00", "end": "2026-07-21T22:00", "preference": "preferred"},
        ]
    }, PARAMS)
    assert res.ok
    rows = repo.list_availability(uid)
    assert len(rows) == 1 and rows[0]["preference"] == "preferred"


def test_set_availability_replaces(db):
    uid = _user(db)
    apply_tool_call(uid, "set_availability", {
        "slots": [{"start": "2026-07-21T18:00", "end": "2026-07-21T22:00", "preference": "yes"}]
    }, PARAMS)
    apply_tool_call(uid, "set_availability", {
        "slots": [{"start": "2026-07-22T18:00", "end": "2026-07-22T22:00", "preference": "yes"}]
    }, PARAMS)
    rows = repo.list_availability(uid)
    assert len(rows) == 1 and rows[0]["slot_start"] == "2026-07-22T18:00"


def test_mark_ready_requires_availability(db):
    uid = _user(db)
    # no slots yet -> refused
    res = apply_tool_call(uid, "mark_ready", {}, PARAMS)
    assert not res.ok and not res.triggered_ready
    # after providing a slot -> allowed
    apply_tool_call(uid, "set_availability", {
        "slots": [{"start": "2026-07-21T18:00", "end": "2026-07-21T22:00", "preference": "yes"}]
    }, PARAMS)
    res2 = apply_tool_call(uid, "mark_ready", {}, PARAMS)
    assert res2.ok and res2.triggered_ready


def test_slots_snapped_to_grid(db):
    """Different free-form end times for the same evening must collapse to one
    canonical slot so the intersection stays reliable."""
    uid = _user(db)
    apply_tool_call(uid, "set_availability", {
        "slots": [{"start": "2026-07-21T19:30", "end": "2026-07-21T23:59", "preference": "yes"}]
    }, PARAMS)
    rows = repo.list_availability(uid)
    assert len(rows) == 1
    assert rows[0]["slot_start"] == "2026-07-21T18:00"
    assert rows[0]["slot_end"] == "2026-07-21T22:00"


def test_halfday_snapping(db):
    uid = _user(db)
    params = {**PARAMS, "granularity": "halfday"}
    apply_tool_call(uid, "set_availability", {
        "slots": [
            {"start": "2026-07-21T10:00", "end": "2026-07-21T12:00", "preference": "yes"},
            {"start": "2026-07-21T15:00", "end": "2026-07-21T18:00", "preference": "preferred"},
        ]
    }, params)
    rows = repo.list_availability(uid)
    assert {r["slot_start"] for r in rows} == {"2026-07-21T09:00", "2026-07-21T14:00"}


def test_remember_stores_and_dedupes(db):
    uid = _user(db)
    r = apply_tool_call(uid, "remember", {"fact": "isst vegetarisch"}, PARAMS)
    assert r.ok and r.triggered_ready is False
    assert [m["content"] for m in repo.list_memories(uid)] == ["isst vegetarisch"]
    # exact dedupe (case-insensitive)
    apply_tool_call(uid, "remember", {"fact": "Isst Vegetarisch"}, PARAMS)
    assert len(repo.list_memories(uid)) == 1
    apply_tool_call(uid, "remember", {"fact": "mag Biergärten"}, PARAMS)
    assert len(repo.list_memories(uid)) == 2


def test_memory_cap(db):
    uid = _user(db)
    for i in range(55):
        repo.add_memory(uid, f"fakt {i}", cap=50)
    mems = repo.list_memories(uid)
    assert len(mems) == 50
    assert mems[-1]["content"] == "fakt 54"  # newest kept
    assert all("fakt 0" != m["content"] for m in mems)  # oldest dropped


def test_migration_v3_user_memory_table(db):
    cols = [r["name"] for r in db.query("PRAGMA table_info(user_memory)")]
    assert "content" in cols and "user_id" in cols


def test_invalid_preference_rejected(db):
    uid = _user(db)
    res = apply_tool_call(uid, "set_availability", {
        "slots": [{"start": "2026-07-21T18:00", "end": "2026-07-21T22:00", "preference": "nope"}]
    }, PARAMS)
    assert not res.ok
