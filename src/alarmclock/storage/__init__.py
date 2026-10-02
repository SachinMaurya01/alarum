"""Storage package."""

from alarmclock.storage.file_repository import FileAlarmRepository
from alarmclock.storage.repository import AlarmRepository

__all__ = ["AlarmRepository", "FileAlarmRepository"]
