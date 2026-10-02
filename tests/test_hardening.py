"""Doctor, config show/set, completions + error-handling tests."""

import json
import os

import pytest

from alarmclock.cli.completions import completion_script
from alarmclock.cli.doctor import overall_status, run_checks
from alarmclock.cli.main import main
from alarmclock.config import coerce_config_value
from alarmclock.errors import InvalidInput


def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("ALARMCLOCK_DATA_FILE", str(tmp_path / "alarms.json"))
    monkeypatch.setenv("ALARMCLOCK_CONFIG_FILE", str(tmp_path / "config.json"))


def test_doctor_ok_and_json(tmp_path, monkeypatch, capsys):
    _env(tmp_path, monkeypatch)
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "state-file" in out
    assert main(["--json", "doctor"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] in {"ok", "warn"}
    assert all({"check", "status", "detail"} <= set(c) for c in payload["checks"])


def test_doctor_flags_corrupt_state(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    (tmp_path / "alarms.json").write_text("{corrupt")
    checks = run_checks(str(tmp_path / "alarms.json"))
    by_name = {c["check"]: c for c in checks}
    assert by_name["state-file"]["status"] == "fail"
    assert overall_status(checks) == "fail"
    assert main(["doctor"]) == 1


def test_config_set_show_roundtrip(tmp_path, monkeypatch, capsys):
    _env(tmp_path, monkeypatch)
    assert (
        main(
            [
                "--config",
                str(tmp_path / "config.json"),
                "config",
                "set",
                "snooze_minutes",
                "10",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(["--config", str(tmp_path / "config.json"), "--json", "config", "show"])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["snooze_minutes"] == 10
    if os.name == "posix":
        assert oct((tmp_path / "config.json").stat().st_mode & 0o777) == "0o600"


def test_config_set_bad_key_and_value(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    assert main(["config", "set", "nope", "1"]) == 2
    assert main(["config", "set", "snooze_minutes", "-3"]) == 2
    assert main(["config", "set", "missed_policy", "sometimes"]) == 2


def test_coerce_values():
    assert coerce_config_value("notifications", "off") is False
    assert coerce_config_value("missed_policy", "skip") == "skip"
    with pytest.raises(InvalidInput):
        coerce_config_value("max_snoozes", "-1")


def test_completions_all_shells():
    for shell in ("bash", "zsh", "fish"):
        script = completion_script(shell)
        assert "daemon" in script and len(script) > 100
    with pytest.raises(InvalidInput):
        completion_script("powershell")


def test_internal_error_never_tracebacks(tmp_path, monkeypatch, capsys):
    _env(tmp_path, monkeypatch)
    (tmp_path / "alarms.json").write_text("[]")  # valid empty store
    # Force an unexpected failure inside dispatch via unreadable data dir
    monkeypatch.setenv("ALARMCLOCK_DATA_FILE", "/proc/cannot-write-here/alarms.json")
    rc = main(["list"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out + captured.err
