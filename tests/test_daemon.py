"""Daemon PID handling, command channel + status tests."""

import pytest

from alarmclock.errors import SchedulerError
from alarmclock.scheduler.daemon import DaemonManager, daemon_main


def _manager(tmp_path, alive=True):
    killed = []
    alive_holder = {"alive": alive}

    def fake_spawn(tick):
        (tmp_path / "alarmd.pid").write_text("424242")
        return 424242

    mgr = DaemonManager(
        tmp_path,
        pid_alive=lambda pid: alive_holder["alive"],
        kill_fn=lambda pid, sig: killed.append((pid, sig)),
        spawn_fn=fake_spawn,
    )
    return mgr, killed, alive_holder


def test_start_and_status(tmp_path):
    mgr, _, _ = _manager(tmp_path)
    assert mgr.start() == 424242
    info = mgr.status()
    assert info == {"running": True, "pid": 424242, "pid_file": str(tmp_path / "alarmd.pid")}


def test_double_start_rejected(tmp_path):
    mgr, _, _ = _manager(tmp_path)
    mgr.start()
    with pytest.raises(SchedulerError):
        mgr.start()


def test_stop_kills_and_cleans_pidfile(tmp_path):
    import signal

    mgr, killed, _alive = _manager(tmp_path)
    mgr.start()
    checks = {"n": 0}

    def dying(pid):
        checks["n"] += 1
        return checks["n"] <= 2  # alive for the check, dead after SIGTERM

    mgr._pid_alive = dying
    assert mgr.stop(timeout=5) is True
    assert killed and killed[0][0] == 424242 and killed[0][1] == signal.SIGTERM
    assert not (tmp_path / "alarmd.pid").exists()


def test_stale_pidfile_cleaned(tmp_path):
    (tmp_path / "alarmd.pid").write_text("999999999")
    mgr = DaemonManager(tmp_path, pid_alive=lambda pid: False)
    assert mgr.is_running() is False
    assert not (tmp_path / "alarmd.pid").exists()
    assert mgr.stop() is False


def test_command_roundtrip(tmp_path):
    mgr, _, _ = _manager(tmp_path)
    mgr.enqueue_command("snooze", "abc123", minutes=10)
    (tmp_path / "commands" / "garbage.json").write_text("{oops")
    cmds = mgr.drain_commands()
    assert len(cmds) == 1 and cmds[0]["action"] == "snooze" and cmds[0]["minutes"] == 10
    assert mgr.drain_commands() == []


def test_daemon_main_runs_iterations(tmp_path):
    fired = daemon_main(tmp_path, tick=0.01, stop_after=2)
    assert fired == 0
    assert not (tmp_path / "alarmd.pid").exists()  # cleaned up


def test_daemon_applies_stop_command(tmp_path):
    mgr, _, _ = _manager(tmp_path)
    mgr.enqueue_command("stop")
    assert daemon_main(tmp_path, tick=0.01, stop_after=100) == 0
