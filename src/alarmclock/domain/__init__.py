"""Domain package: pure logic, no I/O."""

from alarmclock.domain.alarm import Alarm, new_id
from alarmclock.domain.recurrence import (
    allowed_weekdays,
    last_fire,
    next_fire,
    normalize_recurrence,
)
from alarmclock.domain.time_parse import parse_time_input

__all__ = [
    "Alarm",
    "allowed_weekdays",
    "last_fire",
    "new_id",
    "next_fire",
    "normalize_recurrence",
    "parse_time_input",
]
