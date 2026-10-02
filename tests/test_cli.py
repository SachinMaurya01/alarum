"""CLI exit codes + --json contract tests."""

import json

from alarmclock.cli.main import main


def _run(args, tmp_path, monkeypatch):
    monkeypatch.setenv("ALARMCLOCK_DATA_FILE", str(tmp_path / "alarms.json"))
    return main(args)


def test_add_list_json(tmp_path, monkeypatch, capsys):
    assert _run(["add", "09:30", "--label", "Standup"], tmp_path, monkeypatch) == 0
    capsys.readouterr()
    assert _run(["--json", "list"], tmp_path, monkeypatch) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 1 and data[0]["time"] == "09:30"


def test_bad_time_exit_2(tmp_path, monkeypatch):
    assert _run(["add", "lunchtime"], tmp_path, monkeypatch) == 2


def test_remove_missing_exit_3(tmp_path, monkeypatch):
    assert _run(["remove", "nope"], tmp_path, monkeypatch) == 3


def test_next_empty_json_null(tmp_path, monkeypatch, capsys):
    assert _run(["--json", "next"], tmp_path, monkeypatch) == 0
    assert json.loads(capsys.readouterr().out) is None


def test_edit_command(tmp_path, monkeypatch, capsys):
    assert _run(["add", "09:30", "--label", "Old"], tmp_path, monkeypatch) == 0
    capsys.readouterr()
    assert _run(["list", "--json"], tmp_path, monkeypatch) == 0
    alarm_id = json.loads(capsys.readouterr().out)[0]["id"]
    assert (
        _run(["edit", alarm_id, "10:15", "--label", "New"], tmp_path, monkeypatch) == 0
    )
    capsys.readouterr()
    assert _run(["list", "--json"], tmp_path, monkeypatch) == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["time"] == "10:15" and data[0]["label"] == "New"
    assert _run(["edit", "nope", "10:15"], tmp_path, monkeypatch) == 3


def test_snooze_cap_exit_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ALARMCLOCK_CONFIG_FILE", str(tmp_path / "config.json"))
    assert _run(["config", "set", "max_snoozes", "1"], tmp_path, monkeypatch) == 0
    capsys.readouterr()
    assert _run(["add", "09:30"], tmp_path, monkeypatch) == 0
    capsys.readouterr()
    assert _run(["list", "--json"], tmp_path, monkeypatch) == 0
    alarm_id = json.loads(capsys.readouterr().out)[0]["id"]
    assert _run(["snooze", alarm_id], tmp_path, monkeypatch) == 0
    assert _run(["snooze", alarm_id], tmp_path, monkeypatch) == 2  # capped


def test_daemon_status_not_running(tmp_path, monkeypatch, capsys):
    assert _run(["daemon", "status", "--json"], tmp_path, monkeypatch) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["running"] is False and payload["pid"] is None


def test_bare_and_unknown_command(tmp_path, monkeypatch, capsys):
    assert _run([], tmp_path, monkeypatch) == 0  # help, not an error
    assert _run(["frobnicate"], tmp_path, monkeypatch) == 2
    assert _run(["snooze", "abc", "notanint"], tmp_path, monkeypatch) == 2


def test_quiet_suppresses_output(tmp_path, monkeypatch, capsys):
    assert _run(["add", "09:30"], tmp_path, monkeypatch) == 0
    capsys.readouterr()
    assert _run(["--quiet", "list"], tmp_path, monkeypatch) == 0
    assert capsys.readouterr().out == ""
    assert _run(["list", "--json"], tmp_path, monkeypatch) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1


def test_remove_enable_json_flags(tmp_path, monkeypatch, capsys):
    assert _run(["add", "09:30"], tmp_path, monkeypatch) == 0
    capsys.readouterr()
    assert _run(["list", "--json"], tmp_path, monkeypatch) == 0
    alarm_id = json.loads(capsys.readouterr().out)[0]["id"]
    assert _run(["disable", alarm_id, "--json"], tmp_path, monkeypatch) == 0
    assert json.loads(capsys.readouterr().out)["enabled"] is False
    assert _run(["remove", alarm_id, "--json"], tmp_path, monkeypatch) == 0
    assert json.loads(capsys.readouterr().out) == {"removed": alarm_id}
