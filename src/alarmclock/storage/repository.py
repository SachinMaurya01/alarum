"""Repository port: storage sits behind this protocol."""

from __future__ import annotations

from typing import Protocol

from alarmclock.domain.alarm import Alarm


class AlarmRepository(Protocol):
    def list(self) -> list[Alarm]: ...

    def get(self, alarm_id: str) -> Alarm | None: ...

    def save(self, alarm: Alarm) -> Alarm:
        """Upsert by id."""
        ...

    def delete(self, alarm_id: str) -> bool: ...
