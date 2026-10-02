"""Text/JSON/quiet output contract for every command + main() error paths."""

import json

import pytest

from alarmclock.cli.main import main
from alarmclock.errors import AlarmNotFound


def _run(args, tmp_path, monkeypatch):
    monkeypatch.setenv("ALARMCLOCK_DATA_FILE", str(tmp_path / "alarms.json"))
    return main(args)


def _add(tmp_path, monkeypatch, capsys, *args):
    assert _run(["add", *args], tmp_path, monkeypatch) == 0
    capsys.readouterr()
    assert _run(["list", "--json"], tmp_path, monkeypatch) == 0
    return json.loads(capsys.readouterr().out)[0]["id"]


def test_add_json_and_quiet(tmp_path, monkeypatch, capsys):
    assert _run(["add", "09:30", "--label", "Hi", "--json"], tmp_path, monkeypatch) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["alarm"]["time"] == "09:30" and payload["next_fire"]
    assert _run(["--quiet", "add", "10:00"], tmp_path, monkeypatch) == 0
    assert capsys.readouterr().out == ""


def test_list_empty_and_all_and_quiet(tmp_path, monkeypatch, capsys):
    assert _run(["list"], tmp_path, monkeypatch) == 0
    assert "No alarms" in capsys.readouterr().out
    _add(tmp_path, monkeypatch, capsys, "09:30")
    assert _run(["--quiet", "list", "--all"], tmp_path, monkeypatch) == 0
    assert capsys.readouterr().out == ""


def test_enable_disable_remove_text(tmp_path, monkeypatch, capsys):
    alarm_id = _add(tmp_path, monkeypatch, capsys, "09:30")
    assert _run(["disable", alarm_id], tmp_path, monkeypatch) == 0
    assert "Disabled" in capsys.readouterr().out
    assert _run(["enable", alarm_id], tmp_path, monkeypatch) == 0
    assert "Enabled" in capsys.readouterr().out
    assert _run(["remove", alarm_id], tmp_path, monkeypatch) == 0
    assert "Removed" in capsys.readouterr().out


def test_next_text_and_watch_headless(tmp_path, monkeypatch, capsys):
    _add(tmp_path, monkeypatch, capsys, "09:30", "--label", "Hi")
    assert _run(["next"], tmp_path, monkeypatch) == 0
    assert "fires in" in capsys.readouterr().out
    assert _run(["next", "--watch"], tmp_path, monkeypatch) == 0  # non-TTY: prints once
    assert "fires in" in capsys.readouterr().out


def test_snooze_dismiss_text_and_json(tmp_path, monkeypatch, capsys):
    alarm_id = _add(tmp_path, monkeypatch, capsys, "09:30")
    assert _run(["snooze", alarm_id, "--json"], tmp_path, monkeypatch) == 0
    assert "snooze_until" in capsys.readouterr().out
    assert _run(["dismiss", alarm_id], tmp_path, monkeypatch) == 0
    assert "Dismissed" in capsys.readouterr().out
    assert _run(["dismiss", alarm_id, "--json"], tmp_path, monkeypatch) == 0
    assert json.loads(capsys.readouterr().out)["id"] == alarm_id


def test_edit_json(tmp_path, monkeypatch, capsys):
    alarm_id = _add(tmp_path, monkeypatch, capsys, "09:30")
    assert (
        _run(["edit", alarm_id, "--label", "New", "--json"], tmp_path, monkeypatch) == 0
    )
    assert json.loads(capsys.readouterr().out)["label"] == "New"


def test_daemon_stop_variants(tmp_path, monkeypatch, capsys):
    assert _run(["daemon", "stop"], tmp_path, monkeypatch) == 0
    assert "not running" in capsys.readouterr().out
    assert _run(["daemon", "stop", "--json"], tmp_path, monkeypatch) == 0
    assert json.loads(capsys.readouterr().out) == {"stopped": False}
    assert _run(["daemon", "status"], tmp_path, monkeypatch) == 0
    assert "not running" in capsys.readouterr().out


def test_daemon_start_and_stop_real(tmp_path, monkeypatch, capsys):
    from pathlib import Path

    from alarmclock.scheduler.daemon import DaemonManager

    assert _run(["daemon", "start", "--tick", "1"], tmp_path, monkeypatch) == 0
    assert "pid" in capsys.readouterr().out
    assert _run(["daemon", "status", "--json"], tmp_path, monkeypatch) == 0
    assert json.loads(capsys.readouterr().out)["running"] is True
    try:
        assert _run(["daemon", "start", "--tick", "1"], tmp_path, monkeypatch) == 5
    finally:
        DaemonManager(Path(str(tmp_path))).stop(timeout=10)
    assert _run(["daemon", "status"], tmp_path, monkeypatch) == 0
    assert "not running" in capsys.readouterr().out


def test_config_show_text(tmp_path, monkeypatch, capsys):
    assert _run(["config", "show"], tmp_path, monkeypatch) == 0
    assert "snooze_minutes" in capsys.readouterr().out


def test_main_error_paths(tmp_path, monkeypatch, capsys):
    import sys

    cli_main = sys.modules["alarmclock.cli.main"]  # the module, not the re-export

    _run(["list"], tmp_path, monkeypatch)
    capsys.readouterr()

    class Boom:
        def __init__(self, exc):
            self.exc = exc

        def __call__(self, *args, **kwargs):
            raise self.exc

    monkeypatch.setattr(cli_main, "app", Boom(KeyboardInterrupt()))
    assert cli_main.main(["list"]) == 0
    monkeypatch.setattr(cli_main, "app", Boom(RuntimeError("boom")))
    assert cli_main.main(["list"]) == 1
    assert "internal error" in capsys.readouterr().err
    monkeypatch.setattr(cli_main, "app", Boom(RuntimeError("boom")))
    monkeypatch.setattr(cli_main, "_DEBUG", True)
    with pytest.raises(RuntimeError):
        cli_main.main(["list"])


def test_doctor_config_warn_paths(tmp_path, monkeypatch, capsys):
    from alarmclock.cli.doctor import run_checks

    cfg = tmp_path / "config.json"
    cfg.write_text("{corrupt")
    by_name = {c["check"]: c for c in run_checks(str(tmp_path / "a.json"), str(cfg))}
    assert by_name["config"]["status"] == "warn"
    cfg.write_text('{"unknown_key_xyz": 1, "snooze_minutes": 5}')
    by_name = {c["check"]: c for c in run_checks(str(tmp_path / "a.json"), str(cfg))}
    assert (
        by_name["config"]["status"] == "warn"
        and "unknown_key_xyz" in by_name["config"]["detail"]
    )


def test_doctor_state_file_perms_warn(tmp_path):
    import os

    from alarmclock.cli.doctor import run_checks

    if os.name != "posix":
        pytest.skip("POSIX-only permissions check")
    p = tmp_path / "alarms.json"
    p.write_text('{"version": 1, "alarms": []}')
    os.chmod(p, 0o644)
    by_name = {c["check"]: c for c in run_checks(str(p), None)}
    assert by_name["state-file"]["status"] == "warn"


def test_repo_require_and_shape_errors(tmp_path):
    from alarmclock.errors import StorageError
    from alarmclock.storage.file_repository import FileAlarmRepository

    repo = FileAlarmRepository(tmp_path / "missing.json")
    assert repo.list() == []  # missing file
    with pytest.raises(AlarmNotFound):
        repo.require("nope")
    bad = tmp_path / "bad.json"
    bad.write_text('{"unexpected": true}')
    with pytest.raises(StorageError):
        FileAlarmRepository(bad).list()
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"version": 1, "alarms": [{"nope": true}]}')
    with pytest.raises(StorageError):
        FileAlarmRepository(invalid).list()
    v0 = tmp_path / "v0.json"
    v0.write_text('{"version": 0, "alarms": []}')
    assert FileAlarmRepository(v0).list() == []


def test_repo_lock_fallback_paths(tmp_path, monkeypatch):
    import sys

    import alarmclock.storage.file_repository as fr_mod
    from alarmclock.domain.alarm import Alarm
    from alarmclock.storage.file_repository import FileAlarmRepository

    # fcntl.flock raising -> falls through to sidecar spin lock.
    repo = FileAlarmRepository(tmp_path / "a.json")
    repo.save(Alarm(id="a1", label="x", hour=7, minute=0, timezone="UTC"))
    import fcntl

    monkeypatch.setattr(
        fcntl, "flock", lambda *a: (_ for _ in ()).throw(OSError("busy"))
    )
    assert len(repo.list()) == 1
    # fcntl entirely unavailable -> same fallback.
    monkeypatch.setitem(sys.modules, "fcntl", None)
    assert len(repo.list()) == 1
    assert fr_mod.FileAlarmRepository is FileAlarmRepository


def test_read_keypress_headless():
    from alarmclock.scheduler.runner import read_keypress

    assert read_keypress() is None
