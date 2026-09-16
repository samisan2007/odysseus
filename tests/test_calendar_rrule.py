"""Issue #1320 — the agent's manage_calendar tool can create a recurring event.

The create_event handler already persists `rrule`, but it wasn't documented in the
tool schema, so the agent took "a roundabout way". This pins the end-to-end path:
calling do_manage_calendar with an rrule stores a single event carrying that RRULE.
"""

import json
import sys
import uuid

import pytest

from tests.helpers.import_state import clear_fake_database_modules
from tests.helpers.sqlite_db import make_temp_sqlite

clear_fake_database_modules()

import core.database as cdb
from core.database import CalendarEvent

_TS, _ENGINE, _TMPDB = make_temp_sqlite(cdb.Base.metadata)


@pytest.fixture(autouse=True)
def _bind_temp_db(monkeypatch):
    # do_manage_calendar does `from core.database import SessionLocal` at call
    # time, so patch the module attribute to our temp DB — via monkeypatch so it
    # is RESTORED after each test and can't leak into later tests in the process.
    monkeypatch.setitem(sys.modules, "core.database", cdb)
    parent = sys.modules.get("core")
    if parent is not None:
        monkeypatch.setattr(parent, "database", cdb, raising=False)
    monkeypatch.setattr(cdb, "SessionLocal", _TS)
    yield


async def test_create_event_with_rrule_persists_recurrence():
    from src.tool_implementations import do_manage_calendar

    owner = "tester-" + uuid.uuid4().hex[:6]
    rrule = "FREQ=WEEKLY;BYDAY=MO"
    res = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "Standup",
        "dtstart": "2026-06-08T09:00:00Z",
        "rrule": rrule,
    }), owner=owner)
    assert res.get("exit_code", 0) == 0, res
    uid = res.get("uid")
    assert uid, res

    db = _TS()
    try:
        ev = db.query(CalendarEvent).filter(CalendarEvent.uid == uid).first()
        assert ev is not None
        assert ev.rrule == rrule  # ONE event carrying the recurrence rule
        assert ev.summary == "Standup"
    finally:
        db.close()


async def test_create_event_without_rrule_is_single():
    from src.tool_implementations import do_manage_calendar

    owner = "tester-" + uuid.uuid4().hex[:6]
    res = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "One-off",
        "dtstart": "2026-06-09T10:00:00Z",
    }), owner=owner)
    assert res.get("exit_code", 0) == 0, res
    db = _TS()
    try:
        ev = db.query(CalendarEvent).filter(CalendarEvent.uid == res["uid"]).first()
        assert ev is not None and (ev.rrule or "") == ""
    finally:
        db.close()


async def test_update_event_can_clear_rrule():
    from src.tool_implementations import do_manage_calendar

    owner = "tester-" + uuid.uuid4().hex[:6]
    created = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "Repeating standup",
        "dtstart": "2026-07-01T14:00:00Z",
        "rrule": "FREQ=WEEKLY;BYDAY=WE",
    }), owner=owner)
    assert created.get("exit_code", 0) == 0, created

    updated = await do_manage_calendar(json.dumps({
        "action": "update_event",
        "uid": created["uid"],
        "rrule": "",
    }), owner=owner)
    assert updated.get("exit_code", 0) == 0, updated

    db = _TS()
    try:
        ev = db.query(CalendarEvent).filter(CalendarEvent.uid == created["uid"]).first()
        assert ev is not None
        assert (ev.rrule or "") == ""
    finally:
        db.close()


async def test_update_event_can_clear_rrule_with_repeat_none_alias():
    from src.tool_implementations import do_manage_calendar

    owner = "tester-" + uuid.uuid4().hex[:6]
    created = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "Repeating review",
        "dtstart": "2026-07-01T15:00:00Z",
        "rrule": "FREQ=WEEKLY;BYDAY=WE",
    }), owner=owner)
    assert created.get("exit_code", 0) == 0, created

    updated = await do_manage_calendar(json.dumps({
        "action": "update_event",
        "uid": created["uid"],
        "repeat": "none",
    }), owner=owner)
    assert updated.get("exit_code", 0) == 0, updated

    db = _TS()
    try:
        ev = db.query(CalendarEvent).filter(CalendarEvent.uid == created["uid"]).first()
        assert ev is not None
        assert (ev.rrule or "") == ""
    finally:
        db.close()


async def test_list_events_exposes_rrule_for_repeating_events():
    from src.tool_implementations import do_manage_calendar

    owner = "tester-" + uuid.uuid4().hex[:6]
    rrule = "FREQ=WEEKLY;BYDAY=WE"
    created = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "Weekly sync",
        "dtstart": "2026-07-01T14:00:00Z",
        "rrule": rrule,
    }), owner=owner)
    assert created.get("exit_code", 0) == 0, created

    listed = await do_manage_calendar(json.dumps({
        "action": "list_events",
        "start": "2026-07-01T00:00:00Z",
        "end": "2026-07-02T00:00:00Z",
    }), owner=owner)
    assert listed.get("exit_code", 0) == 0, listed
    # list_events expands recurrences like GET /calendar/events does, so the
    # row comes back as an occurrence: compound uid, series_uid = base uid.
    matches = [ev for ev in listed["events"] if ev.get("series_uid") == created["uid"]]
    assert matches
    assert matches[0]["rrule"] == rrule
    assert matches[0]["is_recurrence"] is True
    assert matches[0]["uid"].startswith(created["uid"] + "::")
    assert f"repeats({rrule})" in listed["response"]


async def test_list_events_expands_series_starting_before_window():
    """A yearly birthday whose DTSTART is years earlier must still be listed.

    Regression: list_events filtered on `dtstart < end AND dtend > start`, which
    only ever matched the series' original occurrence. Birthdays and long-running
    weekly meetings were therefore invisible to the agent even though the
    calendar UI — which expands RRULEs — displayed them.
    """
    from src.tool_implementations import do_manage_calendar

    owner = "tester-" + uuid.uuid4().hex[:6]
    created = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "Vasw bd",
        "dtstart": "2018-09-04T00:00:00Z",
        "dtend": "2018-09-05T00:00:00Z",
        "rrule": "FREQ=YEARLY",
    }), owner=owner)
    assert created.get("exit_code", 0) == 0, created

    listed = await do_manage_calendar(json.dumps({
        "action": "list_events",
        "start": "2026-09-01T00:00:00Z",
        "end": "2026-09-30T23:59:59Z",
    }), owner=owner)
    assert listed.get("exit_code", 0) == 0, listed

    occurrences = [ev for ev in listed["events"] if ev["summary"] == "Vasw bd"]
    assert len(occurrences) == 1, listed["events"]
    assert occurrences[0]["dtstart"].startswith("2026-09-04")
    assert occurrences[0]["series_uid"] == created["uid"]
    assert "No events between" not in listed["response"]


async def test_list_events_excludes_series_ended_before_window():
    """An UNTIL-bounded series must not leak into a later window."""
    from src.tool_implementations import do_manage_calendar

    owner = "tester-" + uuid.uuid4().hex[:6]
    created = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "Old standup",
        "dtstart": "2018-01-01T09:00:00Z",
        "rrule": "FREQ=YEARLY;UNTIL=20200101T090000Z",
    }), owner=owner)
    assert created.get("exit_code", 0) == 0, created

    listed = await do_manage_calendar(json.dumps({
        "action": "list_events",
        "start": "2026-01-01T00:00:00Z",
        "end": "2026-12-31T23:59:59Z",
    }), owner=owner)
    assert listed.get("exit_code", 0) == 0, listed
    assert [ev for ev in listed["events"] if ev["summary"] == "Old standup"] == []


async def test_delete_event_occurrence_scope_keeps_series():
    """A compound occurrence uid + scope=occurrence cancels only that date."""
    from src.tool_implementations import do_manage_calendar

    owner = "tester-" + uuid.uuid4().hex[:6]
    created = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "Weekly sync",
        "dtstart": "2026-07-01T14:00:00Z",
        "rrule": "FREQ=WEEKLY;BYDAY=WE",
    }), owner=owner)
    assert created.get("exit_code", 0) == 0, created

    listed = await do_manage_calendar(json.dumps({
        "action": "list_events",
        "start": "2026-07-01T00:00:00Z",
        "end": "2026-07-31T00:00:00Z",
    }), owner=owner)
    occurrences = [ev for ev in listed["events"] if ev["summary"] == "Weekly sync"]
    assert len(occurrences) >= 3, occurrences

    deleted = await do_manage_calendar(json.dumps({
        "action": "delete_event",
        "uid": occurrences[1]["uid"],
        "scope": "occurrence",
    }), owner=owner)
    assert deleted.get("exit_code", 0) == 0, deleted
    assert deleted.get("scope") == "occurrence"

    after = await do_manage_calendar(json.dumps({
        "action": "list_events",
        "start": "2026-07-01T00:00:00Z",
        "end": "2026-07-31T00:00:00Z",
    }), owner=owner)
    remaining = [ev["uid"] for ev in after["events"] if ev["summary"] == "Weekly sync"]
    assert occurrences[1]["uid"] not in remaining
    assert occurrences[0]["uid"] in remaining
    assert len(remaining) == len(occurrences) - 1

    # The series row itself survives.
    db = _TS()
    try:
        assert db.query(CalendarEvent).filter(CalendarEvent.uid == created["uid"]).first() is not None
    finally:
        db.close()


async def test_delete_event_default_scope_removes_whole_series():
    from src.tool_implementations import do_manage_calendar

    owner = "tester-" + uuid.uuid4().hex[:6]
    created = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "Duplicate birthday",
        "dtstart": "2018-09-04T00:00:00Z",
        "rrule": "FREQ=YEARLY",
    }), owner=owner)
    assert created.get("exit_code", 0) == 0, created

    listed = await do_manage_calendar(json.dumps({
        "action": "list_events",
        "start": "2026-09-01T00:00:00Z",
        "end": "2026-09-30T00:00:00Z",
    }), owner=owner)
    occ = [ev for ev in listed["events"] if ev["summary"] == "Duplicate birthday"]
    assert len(occ) == 1, listed["events"]

    deleted = await do_manage_calendar(json.dumps({
        "action": "delete_event",
        "uid": occ[0]["uid"],
    }), owner=owner)
    assert deleted.get("exit_code", 0) == 0, deleted
    assert deleted.get("scope") == "series"

    db = _TS()
    try:
        assert db.query(CalendarEvent).filter(CalendarEvent.uid == created["uid"]).first() is None
    finally:
        db.close()
