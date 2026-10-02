"""Recurrence normalization + next-fire tests, incl. DST edges."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from alarmclock.domain.alarm import Alarm
from alarmclock.domain.recurrence import next_fire, normalize_recurrence
from alarmclock.errors import InvalidInput

UTC = ZoneInfo("UTC")


def _alarm(**kw):
    base = {
        "id": "a1",
        "label": "t",
        "hour": 9,
        "minute": 30,
        "timezone": "UTC",
        "recurrence": "ONCE",
    }
    base.update(kw)
    return Alarm(**base)


def test_normalize():
    assert normalize_recurrence(None) == "ONCE"
    assert normalize_recurrence("daily") == "DAILY"
    assert normalize_recurrence("weekly:MO,WE") == "WEEKLY;BYDAY=MO,WE"
    assert normalize_recurrence("FREQ=WEEKLY;BYDAY=MO,FR") == "FREQ=WEEKLY;BYDAY=MO,FR"
    with pytest.raises(InvalidInput):
        normalize_recurrence("monthly")
    with pytest.raises(InvalidInput):
        normalize_recurrence("FREQ=MONTHLY")


def test_once_today_or_tomorrow():
    now = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
    assert next_fire(_alarm(), now) == datetime(2026, 10, 1, 9, 30, tzinfo=UTC)
    late = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    assert next_fire(_alarm(), late) == datetime(2026, 10, 2, 9, 30, tzinfo=UTC)


def test_daily_skips_past_today():
    now = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    assert next_fire(_alarm(recurrence="DAILY"), now) == datetime(
        2026, 10, 2, 9, 30, tzinfo=UTC
    )


def test_weekdays_skip_weekend():
    # Friday 10am, 09:30 alarm weekdays -> Monday
    fri = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)  # a Friday
    assert fri.weekday() == 4
    nxt = next_fire(_alarm(recurrence="WEEKDAYS"), fri)
    assert nxt is not None and nxt.weekday() == 0 and (nxt.hour, nxt.minute) == (9, 30)


def test_disabled_returns_none():
    now = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
    assert next_fire(_alarm(enabled=False), now) is None


def test_snooze_overrides():
    now = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
    snooze = datetime(2026, 10, 1, 8, 5, tzinfo=UTC)
    assert next_fire(_alarm(snooze_until=snooze), now) == snooze


def test_absolute_date_past_returns_none():
    now = datetime(2026, 10, 5, 8, 0, tzinfo=UTC)
    a = _alarm(date="2026-10-01")
    assert next_fire(a, now) is None


def test_dst_gap_shifts_forward():
    # America/New_York springs forward 2026-03-08 02:00 -> 03:00; 02:30 doesn't exist.
    tz = "America/New_York"
    now = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)
    a = _alarm(hour=2, minute=30, timezone=tz, recurrence="DAILY")
    nxt = next_fire(a, now)
    assert nxt is not None
    assert (nxt.hour, nxt.minute) != (2, 30) or nxt.date().isoformat() != "2026-03-08"
    # Must still be a valid wall time on/after Mar 8
    assert nxt > now


def test_dst_overlap_uses_first_occurrence():
    # America/New_York falls back 2026-11-01; 01:30 occurs twice -> fold=0.
    tz = "America/New_York"
    now = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    a = _alarm(hour=1, minute=30, timezone=tz, recurrence="DAILY")
    nxt = next_fire(a, now)
    assert nxt is not None and nxt.fold == 0
