"""Single source of time: every 'now' comes from a Clock."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo


class Clock(Protocol):
    def now(self, tz: str | ZoneInfo | None = None) -> datetime:
        """Return timezone-aware 'now'. Defaults to local zone."""
        ...


class SystemClock:
    """Production clock backed by the OS wall clock."""

    def now(self, tz: str | ZoneInfo | None = None) -> datetime:
        zone = ZoneInfo(tz) if isinstance(tz, str) else tz
        if zone is None:
            return datetime.now().astimezone()
        return datetime.now(tz=zone)


class FakeClock:
    """Manually-advanced clock for unit/integration tests."""

    def __init__(self, start: datetime):
        if start.tzinfo is None:
            raise ValueError("FakeClock start must be timezone-aware")
        self._now = start

    def now(self, tz: str | ZoneInfo | None = None) -> datetime:
        if tz is None:
            return self._now
        zone = ZoneInfo(tz) if isinstance(tz, str) else tz
        return self._now.astimezone(zone)

    def set(self, value: datetime) -> None:
        if value.tzinfo is None:
            raise ValueError("FakeClock value must be timezone-aware")
        self._now = value

    def advance(self, delta: timedelta) -> datetime:
        self._now = self._now + delta
        return self._now
