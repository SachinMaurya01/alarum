"""Alarm domain model. Pure data + validation, no I/O."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from alarmclock.errors import InvalidInput

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def new_id() -> str:
    return secrets.token_hex(3)


def validate_timezone(name: str) -> str:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidInput(f"Unknown time zone: {name!r}") from exc
    return name


@dataclass
class Alarm:
    """A single alarm.

    `time` is stored as hour/minute + zone name so recurrence
    is computed on demand. `date` is set only for absolute one-shot
    alarms (YYYY-MM-DD). `recurrence` is a normalized rule, see
    `domain.recurrence.normalize_recurrence`.
    """

    id: str
    label: str
    hour: int
    minute: int
    timezone: str
    recurrence: str = "ONCE"
    sound: str | None = None
    enabled: bool = True
    snooze_until: datetime | None = None
    created_at: datetime | None = None
    date: str | None = None  # YYYY-MM-DD for absolute one-shot alarms
    last_fired_at: datetime | None = None  # dedup marker for the scheduler
    snooze_count: int = 0  # consecutive snoozes; reset on dismiss/natural fire

    def __post_init__(self) -> None:
        if not self.id:
            raise InvalidInput("Alarm id must not be empty")
        if not (0 <= self.hour <= 23):
            raise InvalidInput(f"Hour out of range: {self.hour}")
        if not (0 <= self.minute <= 59):
            raise InvalidInput(f"Minute out of range: {self.minute}")
        validate_timezone(self.timezone)
        if self.date is not None and not _DATE_RE.match(self.date):
            raise InvalidInput(f"Bad date {self.date!r}, expected YYYY-MM-DD")
        if self.snooze_until is not None and self.snooze_until.tzinfo is None:
            raise InvalidInput("snooze_until must be timezone-aware")
        if self.created_at is not None and self.created_at.tzinfo is None:
            raise InvalidInput("created_at must be timezone-aware")
        if self.last_fired_at is not None and self.last_fired_at.tzinfo is None:
            raise InvalidInput("last_fired_at must be timezone-aware")
        if self.snooze_count < 0:
            raise InvalidInput("snooze_count must be >= 0")

    @property
    def time_str(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "time": self.time_str,
            "timezone": self.timezone,
            "recurrence": self.recurrence,
            "sound": self.sound,
            "enabled": self.enabled,
            "snooze_until": self.snooze_until.isoformat()
            if self.snooze_until
            else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "date": self.date,
            "last_fired_at": self.last_fired_at.isoformat()
            if self.last_fired_at
            else None,
            "snooze_count": self.snooze_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Alarm:
        try:
            time_raw = str(data["time"])
            m = _TIME_RE.match(time_raw)
            if not m:
                raise InvalidInput(f"Bad time {time_raw!r}, expected HH:MM")
            snooze_raw = data.get("snooze_until")
            created_raw = data.get("created_at")
            fired_raw = data.get("last_fired_at")
            return cls(
                id=str(data["id"]),
                label=str(data.get("label", "")),
                hour=int(m.group(1)),
                minute=int(m.group(2)),
                timezone=str(data.get("timezone", "UTC")),
                recurrence=str(data.get("recurrence", "ONCE")),
                sound=data.get("sound"),
                enabled=bool(data.get("enabled", True)),
                snooze_until=datetime.fromisoformat(snooze_raw) if snooze_raw else None,
                created_at=datetime.fromisoformat(created_raw) if created_raw else None,
                date=data.get("date"),
                last_fired_at=datetime.fromisoformat(fired_raw) if fired_raw else None,
                snooze_count=int(data.get("snooze_count", 0)),
            )
        except KeyError as exc:
            raise InvalidInput(f"Alarm entry missing field: {exc}") from exc
