"""Atomic writes, locking, versioning + corrupt-recovery tests."""

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from alarmclock.domain.alarm import Alarm
from alarmclock.errors import StorageError
from alarmclock.storage.file_repository import FileAlarmRepository


def _alarm(alarm_id="a1b2c3"):
    return Alarm(
        id=alarm_id,
        label="Standup",
        hour=9,
        minute=30,
        timezone="Asia/Kolkata",
        recurrence="FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
        created_at=datetime(2026, 10, 1, 10, 0, tzinfo=ZoneInfo("UTC")),
    )


def test_roundtrip(tmp_path):
    repo = FileAlarmRepository(tmp_path / "alarms.json")
    repo.save(_alarm())
    alarms = repo.list()
    assert len(alarms) == 1 and alarms[0].label == "Standup"


def test_atomic_write_no_partial(tmp_path):
    p = tmp_path / "alarms.json"
    repo = FileAlarmRepository(p)
    repo.save(_alarm())
    assert p.exists()
    payload = json.loads(p.read_text())
    assert payload["version"] == 1 and len(payload["alarms"]) == 1
    assert not list(tmp_path.glob(".alarms-*.tmp"))


def test_file_mode_600(tmp_path):
    p = tmp_path / "alarms.json"
    FileAlarmRepository(p).save(_alarm())
    if os.name == "posix":
        assert oct(p.stat().st_mode & 0o777) == "0o600"


def test_migrate_bare_list(tmp_path):
    p = tmp_path / "alarms.json"
    p.write_text(json.dumps([_alarm().to_dict()]))
    repo = FileAlarmRepository(p)
    assert len(repo.list()) == 1


def test_corrupt_backs_up_and_raises(tmp_path):
    p = tmp_path / "alarms.json"
    p.write_text("{not json")
    with pytest.raises(StorageError):
        FileAlarmRepository(p).list()
    assert list(tmp_path.glob("alarms.json.corrupt-*.bak"))


def test_prefix_lookup_and_delete(tmp_path):
    repo = FileAlarmRepository(tmp_path / "alarms.json")
    repo.save(_alarm("abcdef"))
    assert repo.get("abc") is not None
    assert repo.delete("abc") is True
    assert repo.delete("abc") is False
