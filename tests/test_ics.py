"""Tests for the ICS export and the share-token lifecycle."""
from app import repo
from app.ics import build_ics

PARAMS = {"description": "Spieleabend", "range_start": "2026-08-10",
          "range_end": "2026-08-31", "granularity": "evening"}
RESULT = {"slot": {"start": "2026-08-15T18:00", "end": "2026-08-15T22:00", "label": "Fr 15.08. abends"},
          "location": "Würfel & Zucker", "summary": "Freitag passt allen"}


def _decided_task(db):
    gid = repo.create_group("G")
    tid = repo.create_task(gid, PARAMS)
    repo.update_task_status(tid, "decided", result=RESULT)
    return gid, tid


def test_build_ics_structure_and_utc(db):
    gid, tid = _decided_task(db)
    repo.set_task_share_token(tid, "tok123")
    text = build_ics(repo.get_task(tid), repo.get_group(gid), ["Alex", "Bea"])
    assert text.startswith("BEGIN:VCALENDAR")
    assert "BEGIN:VEVENT" in text and text.rstrip().endswith("END:VCALENDAR")
    # 18:00 Europe/Berlin on 2026-08-15 is CEST (UTC+2) -> 16:00Z
    assert "DTSTART:20260815T160000Z" in text
    assert "DTEND:20260815T200000Z" in text
    assert "SUMMARY:Spieleabend" in text
    assert "LOCATION:Würfel & Zucker" in text
    assert "\r\n" in text  # CRLF line endings (RFC 5545)
    assert "tok123@agenttable" in text  # UID contains the share token


def test_ensure_share_token_lazy_and_idempotent(db):
    gid, tid = _decided_task(db)
    assert repo.get_task(tid)["share_token"] is None  # not set yet
    tok = repo.ensure_share_token(tid)
    assert tok and repo.get_task(tid)["share_token"] == tok
    assert repo.ensure_share_token(tid) == tok            # idempotent
    assert repo.get_task_by_share_token(tok)["id"] == tid  # reverse lookup


def test_share_token_only_for_decided(db):
    gid = repo.create_group("G")
    tid = repo.create_task(gid, PARAMS)  # collecting
    assert repo.ensure_share_token(tid) is None


def test_migration_v2_added_share_token_column(db):
    # a fresh DB (fixture) must already have the v2 column
    cols = [r["name"] for r in db.query("PRAGMA table_info(task)")]
    assert "share_token" in cols
