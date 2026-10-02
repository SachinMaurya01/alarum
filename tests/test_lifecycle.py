"""Snooze caps, edit, ring loop + missed-policy tests."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from alarmclock.audio.player import FakePlayer
from alarmclock.clock import FakeClock
from alarmclock.config import AppConfig
from alarmclock.errors import InvalidInput
from alarmclock.notify.notifier import FakeNotifier
from alarmclock.scheduler.runner import Runner
from alarmclock.services.service import AlarmService
from alarmclock.storage.file_repository import FileAlarmRepository

UTC = ZoneInfo("UTC")


def _service(tmp_path, start: datetime, name="alarms.json", **cfg_kwargs):
    cfg_kwargs.setdefault("ring_timeout_seconds", 3)
    clock = FakeClock(start)
    cfg = AppConfig(default_timezone="UTC", **cfg_kwargs)
    svc = AlarmService(FileAlarmRepository(tmp_path / name), clock, cfg)
    return svc, clock, cfg


def _runner(svc, clock, cfg):
    player, notifier = FakePlayer(), FakeNotifier()
    return (
        Runner(svc, clock, player, notifier, cfg, sleep_fn=lambda s: None),
        player,
        notifier,
    )


def test_snooze_cap_enforced(tmp_path):
    svc, _, _ = _service(
        tmp_path, datetime(2026, 10, 1, 8, 0, tzinfo=UTC), max_snoozes=2
    )
    a = svc.add("09:30")
    svc.snooze(a.id)
    svc.snooze(a.id)
    with pytest.raises(InvalidInput):
        svc.snooze(a.id)


def test_dismiss_resets_snooze_count(tmp_path):
    svc, _, _ = _service(
        tmp_path, datetime(2026, 10, 1, 8, 0, tzinfo=UTC), max_snoozes=1
    )
    a = svc.add("09:30")
    svc.snooze(a.id)
    with pytest.raises(InvalidInput):
        svc.snooze(a.id)  # capped
    svc.dismiss(a.id)
    assert svc.repo.get(a.id).snooze_count == 0


def test_ring_plays_until_timeout(tmp_path):
    svc, clock, cfg = _service(tmp_path, datetime(2026, 10, 1, 9, 29, tzinfo=UTC))
    svc.add("09:30", repeat="daily")
    runner, player, _ = _runner(svc, clock, cfg)
    assert runner.tick_once() == 0  # watching from 09:29
    clock.advance(timedelta(minutes=2))
    assert runner.tick_once() == 1
    assert len(player.calls) == 3  # ring_timeout_seconds passes, then timeout


def test_external_dismiss_stops_ring(tmp_path):
    svc, clock, cfg = _service(tmp_path, datetime(2026, 10, 1, 9, 29, tzinfo=UTC))
    svc.add("09:30", repeat="daily")
    player = FakePlayer()
    runner = Runner(svc, clock, player, FakeNotifier(), cfg, sleep_fn=lambda s: None)
    orig_get = svc.repo.get
    calls = {"n": 0}

    def get_and_dismiss(alarm_id):
        alarm = orig_get(alarm_id)
        calls["n"] += 1
        if calls["n"] == 2 and alarm is not None:
            svc.dismiss(alarm_id)  # external dismiss mid-ring
            return orig_get(alarm_id)
        return alarm

    svc.repo.get = get_and_dismiss  # type: ignore[method-assign]
    try:
        assert runner.tick_once() == 0  # watching from 09:29
        clock.advance(timedelta(minutes=2))
        assert runner.tick_once() == 1
    finally:
        svc.repo.get = orig_get  # type: ignore[method-assign]
    assert len(player.calls) < 3  # stopped early


def test_missed_fire_now(tmp_path):
    svc, clock, cfg = _service(tmp_path, datetime(2026, 10, 1, 8, 0, tzinfo=UTC))
    svc.add("08:30")
    runner, player, _ = _runner(svc, clock, cfg)
    assert runner.tick_once() == 0  # runner starts watching at 08:00
    clock.advance(timedelta(hours=2))  # suspend-like gap during the run
    assert runner.tick_once() == 1  # fire-now default
    assert player.calls


def test_missed_mark_missed(tmp_path):
    svc, clock, cfg = _service(
        tmp_path, datetime(2026, 10, 1, 8, 0, tzinfo=UTC), missed_policy="mark-missed"
    )
    svc.add("08:30")
    runner, player, notifier = _runner(svc, clock, cfg)
    assert runner.tick_once() == 0
    clock.advance(timedelta(hours=2))
    assert runner.tick_once() == 0
    assert player.calls == []
    assert any("missed" in t.lower() for t, _ in notifier.calls)


def test_fresh_run_does_not_catch_up(tmp_path):
    # (Re)starting the scheduler must not fire yesterday's occurrences.
    svc, clock, cfg = _service(tmp_path, datetime(2026, 10, 2, 8, 0, tzinfo=UTC))
    svc.add("07:00", repeat="daily")  # today's occurrence long past
    runner, player, _ = _runner(svc, clock, cfg)
    assert runner.tick_once() == 0
    assert player.calls == []
    assert svc.list() != []  # recurring alarm stays enabled


def test_edit_time_and_label(tmp_path):
    svc, _, _ = _service(tmp_path, datetime(2026, 10, 1, 8, 0, tzinfo=UTC))
    a = svc.add("09:30", label="Old")
    edited = svc.edit(a.id, time_expr="10:15", label="New", repeat="daily")
    assert (edited.hour, edited.minute) == (10, 15)
    assert edited.label == "New" and edited.recurrence == "DAILY"
