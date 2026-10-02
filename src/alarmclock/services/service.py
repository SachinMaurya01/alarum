"""Application service: add/list/remove/snooze orchestration.

Thin orchestration over the repository + clock + domain. No printing here;
the CLI formats results.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from alarmclock.clock import Clock
from alarmclock.config import AppConfig
from alarmclock.domain.alarm import Alarm, new_id
from alarmclock.domain.recurrence import next_fire, normalize_recurrence
from alarmclock.domain.time_parse import parse_time_input
from alarmclock.errors import AlarmNotFound, InvalidInput
from alarmclock.storage.repository import AlarmRepository

_EXPLICIT_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}\b")


def _resolve_date(time_expr: str, date: str | None, recurrence: str) -> str | None:
    """Recurring alarms keep the wall-clock time; only explicit YYYY-MM-DD
    dates are incompatible with repetition (relative/natural inputs just
    describe a clock time to recur at)."""
    if date is not None and recurrence != "ONCE":
        if _EXPLICIT_DATE.match(time_expr.strip()):
            raise InvalidInput("Absolute dates only work with one-shot alarms")
        return None
    return date


class AlarmService:
    def __init__(self, repo: AlarmRepository, clock: Clock, config: AppConfig):
        self.repo = repo
        self.clock = clock
        self.config = config

    def add(
        self,
        time_expr: str,
        label: str = "",
        repeat: str | None = None,
        sound: str | None = None,
        tz: str | None = None,
    ) -> Alarm:
        tzname = tz or self.config.effective_timezone()
        now = self.clock.now(tzname)
        hour, minute, date, resolved_tz = parse_time_input(time_expr, now, tzname)
        recurrence = normalize_recurrence(repeat)
        date = _resolve_date(time_expr, date, recurrence)
        alarm = Alarm(
            id=new_id(),
            label=label,
            hour=hour,
            minute=minute,
            timezone=resolved_tz,
            recurrence=recurrence,
            sound=sound or self.config.default_sound,
            enabled=True,
            created_at=self.clock.now("UTC"),
            date=date,
        )
        # Validate it can fire at least once (catches impossible rules early).
        if next_fire(alarm, self.clock.now("UTC")) is None:
            raise InvalidInput("Alarm would never fire (date is in the past?)")
        return self.repo.save(alarm)

    def list(self, include_disabled: bool = False) -> list[Alarm]:
        alarms = self.repo.list()
        if not include_disabled:
            alarms = [a for a in alarms if a.enabled]
        return sorted(alarms, key=lambda a: (a.hour, a.minute, a.id))

    def remove(self, alarm_id: str) -> None:
        if not self.repo.delete(alarm_id):
            raise AlarmNotFound(f"No alarm {alarm_id!r}")

    def set_enabled(self, alarm_id: str, enabled: bool) -> Alarm:
        alarm = self._require(alarm_id)
        alarm.enabled = enabled
        return self.repo.save(alarm)

    def next(self) -> tuple[Alarm, datetime] | None:
        now = self.clock.now("UTC")
        best: tuple[Alarm, datetime] | None = None
        for alarm in self.repo.list():
            fire_at = next_fire(alarm, now)
            if fire_at is None:
                continue
            if best is None or fire_at < best[1]:
                best = (alarm, fire_at)
        return best

    def snooze(self, alarm_id: str, minutes: int | None = None) -> Alarm:
        alarm = self._require(alarm_id)
        if alarm.snooze_count >= self.config.max_snoozes:
            raise InvalidInput(
                f"Alarm {alarm.id} already snoozed {alarm.snooze_count}x "
                f"(max {self.config.max_snoozes}); dismiss it instead"
            )
        mins = minutes if minutes is not None else self.config.snooze_minutes
        if mins <= 0:
            raise InvalidInput("Snooze duration must be positive")
        alarm.snooze_until = self.clock.now("UTC") + timedelta(minutes=mins)
        alarm.snooze_count += 1
        return self.repo.save(alarm)

    def dismiss(self, alarm_id: str) -> Alarm:
        alarm = self._require(alarm_id)
        alarm.snooze_until = None
        alarm.snooze_count = 0
        alarm.last_fired_at = self.clock.now("UTC")
        if alarm.recurrence == "ONCE":
            alarm.enabled = False
        return self.repo.save(alarm)

    def edit(
        self,
        alarm_id: str,
        time_expr: str | None = None,
        label: str | None = None,
        repeat: str | None = None,
        sound: str | None = None,
        tz: str | None = None,
    ) -> Alarm:
        """Modify an alarm in place; recurrence/time changes clear snooze state."""
        alarm = self._require(alarm_id)
        if time_expr is not None:
            tzname = tz or alarm.timezone
            now = self.clock.now(tzname)
            hour, minute, date, resolved_tz = parse_time_input(time_expr, now, tzname)
            alarm.hour, alarm.minute = hour, minute
            alarm.timezone = resolved_tz
            alarm.date = date
            alarm.snooze_until = None
            alarm.snooze_count = 0
        if tz is not None and time_expr is None:
            from alarmclock.domain.alarm import validate_timezone

            alarm.timezone = validate_timezone(tz)
            alarm.snooze_until = None
            alarm.snooze_count = 0
        if repeat is not None:
            recurrence = normalize_recurrence(repeat)
            alarm.date = _resolve_date(time_expr or "", alarm.date, recurrence)
            alarm.recurrence = recurrence
        if label is not None:
            alarm.label = label
        if sound is not None:
            alarm.sound = sound
        if next_fire(alarm, self.clock.now("UTC")) is None:
            raise InvalidInput("Edited alarm would never fire")
        return self.repo.save(alarm)

    def _require(self, alarm_id: str) -> Alarm:
        alarm = self.repo.get(alarm_id)
        if alarm is None:
            raise AlarmNotFound(f"No alarm {alarm_id!r}")
        return alarm
