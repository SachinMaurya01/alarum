"""Sleep-until-next, mtime cache, and ringing-key tests."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from alarmclock.audio.player import FakePlayer
from alarmclock.clock import FakeClock
from alarmclock.config import AppConfig
from alarmclock.errors import StorageError
from alarmclock.notify.notifier import FakeNotifier
from alarmclock.scheduler.runner import HEARTBEAT_SECONDS, Runner
from alarmclock.services.service import AlarmService
from alarmclock.storage.file_repository import FileAlarmRepository

UTC = ZoneInfo("UTC")


def _service(tmp_path, start: datetime, name="alarms.json", **cfg_kwargs):
    cfg_kwargs.setdefault("ring_timeout_seconds", 3)
    clock = FakeClock(start)
    cfg = AppConfig(default_timezone="UTC", **cfg_kwargs)
    svc = AlarmService(FileAlarmRepository(tmp_path / name), clock, cfg)
    return svc, clock, cfg


def test_sleep_delay_none_far_and_imminent(tmp_path):
    svc, clock, cfg = _service(tmp_path, datetime(2026, 10, 1, 8, 0, tzinfo=UTC))
    runner = Runner(
        svc, clock, FakePlayer(), FakeNotifier(), cfg, sleep_fn=lambda s: None
    )
    assert runner.sleep_delay() == HEARTBEAT_SECONDS  # nothing scheduled
    svc.add("12:00", repeat="daily")  # ~4h away, tick=1
    assert runner.sleep_delay() == HEARTBEAT_SECONDS
    runner.tick = 3600.0
    assert runner.sleep_delay() == HEARTBEAT_SECONDS  # capped by heartbeat
    clock.set(datetime(2026, 10, 1, 11, 59, 30, tzinfo=UTC))
    assert runner.sleep_delay() == 30.0  # 30s out, within long tick


def test_sleep_delay_imminent_precise(tmp_path):
    svc, clock, cfg = _service(tmp_path, datetime(2026, 10, 1, 11, 59, 55, tzinfo=UTC))
    svc.add("12:00", repeat="daily")
    runner = Runner(
        svc, clock, FakePlayer(), FakeNotifier(), cfg, sleep_fn=lambda s: None
    )
    assert runner.sleep_delay() == 5.0  # fires in 5s: exact nap


def test_run_forever_sleeps_by_delay(tmp_path):
    svc, clock, cfg = _service(tmp_path, datetime(2026, 10, 1, 8, 0, tzinfo=UTC))
    svc.add("12:00", repeat="daily")
    delays: list[float] = []
    runner = Runner(
        svc, clock, FakePlayer(), FakeNotifier(), cfg, sleep_fn=delays.append
    )
    assert runner.run_forever(max_iterations=2) == 0
    assert delays == [HEARTBEAT_SECONDS]  # one nap between the two ticks


def test_repo_cache_hit_and_invalidation(tmp_path):
    repo = FileAlarmRepository(tmp_path / "alarms.json")
    assert repo.list() == []
    assert repo._cache is not None  # empty state cached
    from alarmclock.domain.alarm import Alarm

    repo.save(Alarm(id="a1", label="x", hour=7, minute=0, timezone="UTC"))
    assert repo._cache is None  # writes clear the cache
    first = repo.list()
    assert repo._cache is not None
    assert repo.list() == first  # cache hit, equal content


def test_repo_cache_miss_on_external_change(tmp_path):
    repo = FileAlarmRepository(tmp_path / "alarms.json")
    from alarmclock.domain.alarm import Alarm

    repo.save(Alarm(id="a1", label="x", hour=7, minute=0, timezone="UTC"))
    assert len(repo.list()) == 1
    # Simulate another process rewriting the file.
    other = FileAlarmRepository(tmp_path / "alarms.json")
    other._cache = None
    other.save(Alarm(id="b2", label="y", hour=8, minute=0, timezone="UTC"))
    assert {a.id for a in repo.list()} == {"a1", "b2"}


def test_repo_cache_not_served_when_corrupt(tmp_path):
    repo = FileAlarmRepository(tmp_path / "alarms.json")
    from alarmclock.domain.alarm import Alarm

    repo.save(Alarm(id="a1", label="x", hour=7, minute=0, timezone="UTC"))
    repo.list()  # warm the cache
    (tmp_path / "alarms.json").write_text("{corrupt")
    with pytest.raises(StorageError):
        repo.list()


def test_ring_key_dismiss_stops_early(tmp_path, capsys):
    svc, clock, cfg = _service(tmp_path, datetime(2026, 10, 1, 9, 29, tzinfo=UTC))
    a = svc.add("09:30", repeat="daily")
    keys = iter(["d"])
    player = FakePlayer()
    runner = Runner(
        svc,
        clock,
        player,
        FakeNotifier(),
        cfg,
        sleep_fn=lambda s: None,
        key_fn=lambda: next(keys, None),
    )
    assert runner.tick_once() == 0
    clock.advance(timedelta(minutes=2))
    assert runner.tick_once() == 1
    assert len(player.calls) == 1  # dismissed during first pass
    assert svc.repo.get(a.id).enabled is True  # recurring stays on


def test_ring_key_snooze_keeps_snooze(tmp_path):
    svc, clock, cfg = _service(tmp_path, datetime(2026, 10, 1, 9, 29, tzinfo=UTC))
    a = svc.add("09:30", repeat="daily")
    keys = iter(["s"])
    runner = Runner(
        svc,
        clock,
        FakePlayer(),
        FakeNotifier(),
        cfg,
        sleep_fn=lambda s: None,
        key_fn=lambda: next(keys, None),
    )
    assert runner.tick_once() == 0
    clock.advance(timedelta(minutes=2))
    assert runner.tick_once() == 1
    fresh = svc.repo.get(a.id)
    assert fresh.snooze_until is not None and fresh.snooze_count == 1


def test_ring_key_snooze_cap_continues_ringing(tmp_path, capsys):
    svc, clock, cfg = _service(
        tmp_path, datetime(2026, 10, 1, 9, 29, tzinfo=UTC), max_snoozes=0
    )
    svc.add("09:30", repeat="daily")
    player = FakePlayer()
    runner = Runner(
        svc,
        clock,
        player,
        FakeNotifier(),
        cfg,
        sleep_fn=lambda s: None,
        key_fn=lambda: "s",
    )
    assert runner.tick_once() == 0
    clock.advance(timedelta(minutes=2))
    assert runner.tick_once() == 1
    assert len(player.calls) == 3  # cap hit: rings to timeout
    assert "Cannot snooze" in capsys.readouterr().out
