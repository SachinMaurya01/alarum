"""Rich views render without a TTY-independent crash."""

import io

from rich.console import Console

from alarmclock.cli.views import alarm_table, run_banner, spinner
from alarmclock.domain.alarm import Alarm


def _alarm():
    return Alarm(
        id="abc123",
        label="Standup",
        hour=9,
        minute=30,
        timezone="UTC",
        recurrence="DAILY",
    )


def _render(renderable, width=100):
    buf = io.StringIO()
    Console(file=buf, force_terminal=True, width=width).print(renderable)
    return buf.getvalue()


def test_alarm_table_columns_and_rows():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    out = _render(
        alarm_table(
            [_alarm()], {"abc123": datetime(2026, 10, 2, 9, 30, tzinfo=ZoneInfo("UTC"))}
        )
    )
    for col in ("ID", "TIME", "TZ", "REPEAT", "LABEL", "STATE", "NEXT"):
        assert col in out
    assert "abc123" in out and "Standup" in out and "2026-10-02 09:30" in out


def test_alarm_table_empty_and_missing_next():
    out = _render(alarm_table([], {}))
    assert "ID" in out  # headers still render


def test_run_banner_content():
    out = _render(run_banner(3, "Standup in 2h 1m", 1.0))
    assert "alarmclock" in out and "3" in out and "Standup in 2h 1m" in out


def test_spinner_disabled_is_plain_passthrough():
    with spinner("working…", enabled=False):
        pass  # must not touch the terminal


def test_spinner_enabled_completes():
    with spinner("working…", enabled=True):
        pass
