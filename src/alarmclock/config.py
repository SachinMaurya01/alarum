"""App configuration.

Precedence: CLI flags > env ALARMCLOCK_* > config file > defaults.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from alarmclock.errors import InvalidInput


@dataclass
class AppConfig:
    default_timezone: str = "local"
    default_sound: str | None = None
    snooze_minutes: int = 5
    max_snoozes: int = 3
    ring_timeout_seconds: int = 300
    missed_policy: str = "fire-now"  # fire-now | skip | mark-missed
    notifications: bool = True
    data_dir: str | None = None

    def effective_timezone(self) -> str:
        if self.default_timezone == "local":
            return _local_tzname()
        return self.default_timezone


def _local_tzname() -> str:
    try:
        from datetime import datetime

        tzinfo = datetime.now().astimezone().tzinfo
    except OSError:
        tzinfo = None
    key = getattr(tzinfo, "key", None)
    if key:
        return str(key)
    return os.environ.get("TZ", "UTC")


def data_file_path(explicit: str | None = None) -> Path:
    """Resolve the state-file path without third-party deps."""
    if explicit:
        return Path(explicit).expanduser()
    if os.environ.get("ALARMCLOCK_DATA_FILE"):
        return Path(os.environ["ALARMCLOCK_DATA_FILE"]).expanduser()
    base = _platform_data_dir()
    return base / "alarms.json"


def _platform_data_dir() -> Path:
    import sys

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "alarmclock"
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        return Path(appdata) / "alarmclock"
    xdg = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    return Path(xdg) / "alarmclock"


def load_config(config_path: str | None = None) -> AppConfig:
    cfg = AppConfig()
    path = resolve_config_path(config_path)
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for k, v in raw.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
        except (OSError, ValueError):
            pass  # corrupt config -> defaults; `doctor` surfaces it
    # Env overrides
    env_map = {
        "ALARMCLOCK_TZ": "default_timezone",
        "ALARMCLOCK_SOUND": "default_sound",
        "ALARMCLOCK_SNOOZE_MINUTES": "snooze_minutes",
        "ALARMCLOCK_RING_TIMEOUT": "ring_timeout_seconds",
        "ALARMCLOCK_MISSED_POLICY": "missed_policy",
        "ALARMCLOCK_DATA_DIR": "data_dir",
    }
    for env, attr in env_map.items():
        if env in os.environ:
            val: object = os.environ[env]
            if attr in {"snooze_minutes", "ring_timeout_seconds", "max_snoozes"}:
                try:
                    val = int(os.environ[env])
                except ValueError:
                    continue
            setattr(cfg, attr, val)
    if os.environ.get("ALARMCLOCK_NO_NOTIFY") == "1":
        cfg.notifications = False
    if cfg.missed_policy not in {"fire-now", "skip", "mark-missed"}:
        cfg.missed_policy = "fire-now"
    return cfg


def _default_config_path() -> Path | None:
    base = _platform_data_dir()
    return base / "config.json"


def resolve_config_path(explicit: str | None = None) -> Path:
    """CLI flag > ALARMCLOCK_CONFIG_FILE > platform default."""
    if explicit:
        return Path(explicit).expanduser()
    if os.environ.get("ALARMCLOCK_CONFIG_FILE"):
        return Path(os.environ["ALARMCLOCK_CONFIG_FILE"]).expanduser()
    path = _default_config_path()
    assert path is not None
    return path


# Keys manageable via `alarm config set`. data_dir is intentionally excluded
# (use --data-file / ALARMCLOCK_DATA_FILE instead).
CONFIG_KEYS = (
    "default_timezone",
    "default_sound",
    "snooze_minutes",
    "max_snoozes",
    "ring_timeout_seconds",
    "missed_policy",
    "notifications",
)


def coerce_config_value(key: str, raw: str) -> object:
    """Validate + convert a `config set` value. Raises InvalidInput."""
    if key == "default_timezone":
        if raw == "local":
            return raw
        from alarmclock.domain.alarm import validate_timezone

        return validate_timezone(raw)
    if key == "default_sound":
        return raw
    if key in {"snooze_minutes", "max_snoozes", "ring_timeout_seconds"}:
        try:
            val = int(raw)
        except ValueError:
            raise InvalidInput(f"{key} must be an integer, got {raw!r}") from None
        if key == "max_snoozes" and val < 0:
            raise InvalidInput("max_snoozes must be >= 0")
        if key != "max_snoozes" and val <= 0:
            raise InvalidInput(f"{key} must be positive")
        return val
    if key == "missed_policy":
        if raw not in {"fire-now", "skip", "mark-missed"}:
            raise InvalidInput(
                f"missed_policy must be fire-now|skip|mark-missed, got {raw!r}"
            )
        return raw
    if key == "notifications":
        if raw.lower() in {"1", "true", "yes", "on"}:
            return True
        if raw.lower() in {"0", "false", "no", "off"}:
            return False
        raise InvalidInput(f"notifications must be true/false, got {raw!r}")
    raise InvalidInput(f"Unknown config key {key!r}. See `alarm config show`.")


def save_config(cfg: AppConfig, config_path: str | Path | None = None) -> Path:
    """Atomically persist config (temp + fsync + replace, mode 0o600)."""
    path = resolve_config_path(str(config_path) if config_path else None)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(asdict(cfg), fh, indent=2)
            fh.write("\n")
            fh.flush()
            with contextlib.suppress(Exception):
                os.fsync(fh.fileno())
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        from alarmclock.errors import StorageError

        raise StorageError(f"Cannot write config file {path}: {exc}") from exc
    return path


LOG_DEFAULT = field(default=None)  # placeholder to keep dataclass import used
