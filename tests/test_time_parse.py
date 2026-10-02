"""Time parsing grammar tests."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from alarmclock.domain.time_parse import parse_time_input
from alarmclock.errors import InvalidInput

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=ZoneInfo("UTC"))


def test_absolute_24h():
    assert parse_time_input("07:30", NOW, "UTC") == (7, 30, None, "UTC")


def test_absolute_pm():
    assert parse_time_input("7:30pm", NOW, "UTC") == (19, 30, None, "UTC")
    assert parse_time_input("12am", NOW, "UTC") == (0, 0, None, "UTC")
    assert parse_time_input("12pm", NOW, "UTC") == (12, 0, None, "UTC")


def test_relative_minutes():
    h, m, d, _tz = parse_time_input("in 15m", NOW, "UTC")
    assert (h, m, d) == (12, 15, "2026-10-01")


def test_relative_combo():
    h, m, _d, _ = parse_time_input("in 2h30m", NOW, "UTC")
    assert (h, m) == (14, 30)


def test_natural_tomorrow():
    assert parse_time_input("tomorrow 6am", NOW, "UTC") == (6, 0, "2026-10-02", "UTC")


def test_dated():
    assert parse_time_input("2026-12-01 06:00", NOW, "UTC") == (
        6,
        0,
        "2026-12-01",
        "UTC",
    )


def test_invalid():
    for bad in ["", "lunchtime", "in -5m", "25:00", "13pm"]:
        with pytest.raises(InvalidInput):
            parse_time_input(bad, NOW, "UTC")
