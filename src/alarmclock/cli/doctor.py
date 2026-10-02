"""Environment diagnostics: `alarm doctor`.

Every check returns ok/warn/fail — never a traceback. Exit 0 unless a
check fails (warnings don't fail; the terminal bell fallback always works).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path


def run_checks(data_file: str, config_path: str | None = None) -> list[dict]:
    checks: list[dict] = []
    checks.append(_check_python())
    checks.append(_check_timezones())
    checks.append(_check_data_dir(data_file))
    checks.append(_check_state_file(data_file))
    checks.append(_check_config(config_path))
    checks.append(_check_audio())
    checks.append(_check_notifications())
    checks.append(_check_daemon(data_file))
    return checks


def overall_status(checks: list[dict]) -> str:
    if any(c["status"] == "fail" for c in checks):
        return "fail"
    if any(c["status"] == "warn" for c in checks):
        return "warn"
    return "ok"


def _check_python() -> dict:
    ok = sys.version_info >= (3, 10)
    return {
        "check": "python",
        "status": "ok" if ok else "fail",
        "detail": f"{sys.version.split()[0]} (need >= 3.10)",
    }


def _check_timezones() -> dict:
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo("America/New_York")
        ZoneInfo("Asia/Kolkata")
        return {
            "check": "timezones",
            "status": "ok",
            "detail": "zoneinfo database available",
        }
    except Exception as exc:  # noqa: BLE001 - diagnostics must not raise
        return {
            "check": "timezones",
            "status": "fail",
            "detail": f"zoneinfo broken ({exc}); install tzdata",
        }


def _check_data_dir(data_file: str) -> dict:
    parent = Path(data_file).expanduser().parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        probe = parent / ".doctor-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return {"check": "data-dir", "status": "ok", "detail": str(parent)}
    except OSError as exc:
        return {"check": "data-dir", "status": "fail", "detail": f"{parent}: {exc}"}


def _check_state_file(data_file: str) -> dict:
    path = Path(data_file).expanduser()
    if not path.exists():
        return {
            "check": "state-file",
            "status": "ok",
            "detail": "not created yet (first run creates it)",
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {
            "check": "state-file",
            "status": "fail",
            "detail": f"{path} unreadable ({exc}); backups kept next to it",
        }
    alarms = payload.get("alarms") if isinstance(payload, dict) else payload
    detail = f"{path} ({len(alarms) if isinstance(alarms, list) else '?'} alarms)"
    if os.name == "posix":
        try:
            if path.stat().st_mode & 0o777 != 0o600:
                return {
                    "check": "state-file",
                    "status": "warn",
                    "detail": detail + " (permissions not 0o600)",
                }
        except OSError:
            pass
    return {"check": "state-file", "status": "ok", "detail": detail}


def _check_config(config_path: str | None) -> dict:
    from alarmclock.config import load_config, resolve_config_path

    path = resolve_config_path(config_path)
    if not path.exists():
        return {
            "check": "config",
            "status": "ok",
            "detail": "defaults (no config file)",
        }
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        load_config(str(path))
        unknown = sorted(k for k in raw if k not in _config_fields())
        detail = str(path) + (
            " (unknown keys: " + ", ".join(unknown) + ")" if unknown else ""
        )
        return {
            "check": "config",
            "status": "warn" if unknown else "ok",
            "detail": detail,
        }
    except (OSError, ValueError) as exc:
        return {
            "check": "config",
            "status": "warn",
            "detail": f"{path} unreadable, using defaults ({exc})",
        }


def _config_fields() -> set[str]:
    from alarmclock.config import AppConfig

    return set(AppConfig.__dataclass_fields__)


def _check_audio() -> dict:
    from alarmclock.audio.player import SystemPlayer  # noqa: F401 - validates import

    found: list[str] = []
    if sys.platform == "darwin":
        found = [b for b in ("afplay",) if shutil.which(b)]
    elif sys.platform == "win32":
        try:
            import winsound  # noqa: F401

            found = ["winsound"]
        except ImportError:
            found = []
    else:
        found = [b for b in ("paplay", "aplay", "mpv", "play") if shutil.which(b)]
    if found:
        return {
            "check": "audio",
            "status": "ok",
            "detail": "backends: " + ", ".join(found),
        }
    return {
        "check": "audio",
        "status": "warn",
        "detail": "no system player found; terminal bell fallback will be used",
    }


def _check_notifications() -> dict:
    found = False
    if sys.platform == "darwin":
        found = shutil.which("osascript") is not None
    elif sys.platform == "win32":
        found = shutil.which("powershell") is not None
    else:
        found = shutil.which("notify-send") is not None
    if found:
        return {
            "check": "notifications",
            "status": "ok",
            "detail": "desktop backend available",
        }
    return {
        "check": "notifications",
        "status": "warn",
        "detail": "no desktop backend; alarms print to the terminal",
    }


def _check_daemon(data_file: str) -> dict:
    from alarmclock.scheduler.daemon import DaemonManager

    mgr = DaemonManager(Path(data_file).expanduser().parent)
    try:
        info = mgr.status()
    except OSError as exc:
        return {
            "check": "daemon",
            "status": "warn",
            "detail": f"status unavailable ({exc})",
        }
    if info["running"]:
        return {
            "check": "daemon",
            "status": "ok",
            "detail": f"running (pid {info['pid']})",
        }
    return {"check": "daemon", "status": "ok", "detail": "not running"}
