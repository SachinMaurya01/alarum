"""Service orchestration + foreground runner tick tests."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from alarmclock.audio.player import FakePlayer
from alarmclock.clock import FakeClock
from alarmclock.config import AppConfig
from alarmclock.notify.notifier import FakeNotifier
from alarmclock.scheduler.runner import Runner
from alarmclock.services.service import AlarmService
from alarmclock.storage.file_repository import FileAlarmRepository

UTC = ZoneInfo("UTC")


def _service(tmp_path, start: datetime, **cfg_kwargs):
    clock = FakeClock(start)
    cfg = AppConfig(default_timezone="UTC", ring_timeout_seconds=2, **cfg_kwargs)
    repo = FileAlarmRepository(tmp_path / "alarms.json")
    return AlarmService(repo, clock, cfg), clock, cfg


def _runner(svc, clock, cfg):
    player, notifier = FakePlayer(), FakeNotifier()
    return Runner(svc, clock, player, notifier, cfg, sleep_fn=lambda s: None), player, notifier


def test_add_list_next(tmp_path):
    svc, _, _ = _service(tmp_path, datetime(2026, 10, 1, 8, 0, tzinfo=UTC))
    a = svc.add("09:30", label="Standup", repeat="daily")
    assert a.time_str == "09:30"
    assert len(svc.list()) == 1
    nxt = svc.next()
    assert nxt is not None and nxt[0].id == a.id


def test_enable_disable_remove(tmp_path):
    svc, _, _ = _service(tmp_path, datetime(2026, 10, 1, 8, 0, tzinfo=UTC))
    a = svc.add("09:30")
    svc.set_enabled(a.id, False)
    assert svc.list() == [] and svc.list(include_disabled=True) != []
    svc.set_enabled(a.id, True)
    assert len(svc.list()) == 1
    svc.remove(a.id)
    assert svc.list(include_disabled=True) == []


def test_runner_fires_due_alarm(tmp_path):
    svc, clock, cfg = _service(tmp_path, datetime(2026, 10, 1, 9, 29, tzinfo=UTC))
    a = svc.add("09:30")
    runner, player, notifier = _runner(svc, clock, cfg)
    assert runner.tick_once() == 0  # not due yet
    clock.advance(timedelta(minutes=2))  # 09:31, alarm due
    assert runner.tick_once() == 1
    assert player.calls and notifier.calls
    # one-shot auto-disables after firing
    assert svc.list() == []


def test_runner_missed_policy_skip(tmp_path):
    svc, clock, cfg = _service(tmp_path, datetime(2026, 10, 1, 8, 0, tzinfo=UTC))
    cfg.missed_policy = "skip"
    a = svc.add("08:30")
    runner, player, notifier = _runner(svc, clock, cfg)
    assert runner.tick_once() == 0  # watching from 08:00
    clock.advance(timedelta(hours=3))  # way past due + grace
    assert runner.tick_once() == 0
    assert player.calls == []
