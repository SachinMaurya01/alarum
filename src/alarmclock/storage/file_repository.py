"""File-backed repository: atomic writes, locking, versioning.

- Single JSON file: {"version": 1, "alarms": [...]}
- Atomic write: temp file in same dir + flush + os.fsync + os.replace
- Mode 0o600 on POSIX; best-effort ACL tightening skipped (stdlib only)
- Cross-process lock: fcntl.flock (POSIX) / msvcrt.locking (Windows);
  falls back to a best-effort exclusive-create sidecar on unknown platforms
- Corrupt file: backed up to <name>.corrupt-<ts>.bak, StorageError raised
- Schema migrations: dict of version -> fn, applied in order
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from pathlib import Path

from alarmclock.domain.alarm import Alarm
from alarmclock.errors import AlarmNotFound, StorageError

SCHEMA_VERSION = 1


def _migrate_v0_to_v1(payload: dict) -> dict:
    # Legacy: bare list of alarms -> versioned envelope.
    if isinstance(payload, list):
        return {"version": 1, "alarms": payload}
    return payload


MIGRATIONS = {(0, 1): _migrate_v0_to_v1}


class FileAlarmRepository:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        # (mtime_ns, size, alarms): skips JSON re-parses when nothing changed.
        # Every mutation path goes through _write_all_locked, which clears it.
        self._cache: tuple[tuple[int, int], list[Alarm]] | None = None

    # -- public API -----------------------------------------------------
    def list(self) -> list[Alarm]:
        return self._read_all()

    def get(self, alarm_id: str) -> Alarm | None:
        for a in self._read_all():
            if a.id == alarm_id or a.id.startswith(alarm_id):
                return a
        return None

    def save(self, alarm: Alarm) -> Alarm:
        with self._locked():
            alarms = self._read_all_locked()
            for i, existing in enumerate(alarms):
                if existing.id == alarm.id:
                    alarms[i] = alarm
                    break
            else:
                alarms.append(alarm)
            self._write_all_locked(alarms)
        return alarm

    def delete(self, alarm_id: str) -> bool:
        with self._locked():
            alarms = self._read_all_locked()
            kept = [
                a for a in alarms if not (a.id == alarm_id or a.id.startswith(alarm_id))
            ]
            if len(kept) == len(alarms):
                return False
            self._write_all_locked(kept)
            return True

    def require(self, alarm_id: str) -> Alarm:
        alarm = self.get(alarm_id)
        if alarm is None:
            raise AlarmNotFound(f"No alarm {alarm_id!r}")
        return alarm

    # -- internals ------------------------------------------------------
    def _read_all(self) -> list[Alarm]:
        with self._locked():
            return self._read_all_locked()

    def _read_all_locked(self) -> list[Alarm]:
        if not self.path.exists():
            self._cache = None
            return []
        key = self._stat_key()
        if key is not None and self._cache is not None and self._cache[0] == key:
            return list(self._cache[1])
        try:
            raw = self.path.read_text(encoding="utf-8")
            if not raw.strip():
                if key is not None:
                    self._cache = (key, [])
                return []  # freshly created / empty file
            payload = json.loads(raw)
        except (OSError, ValueError) as exc:
            self._cache = None
            self._backup_corrupt()
            raise StorageError(
                f"State file corrupt, backed up next to {self.path.name}. "
                "Delete the corrupt file or restore from backup."
            ) from exc
        if isinstance(payload, list):  # v0 bare list
            payload = self._apply_migrations(payload, from_version=0)
        version = payload.get("version", 1) if isinstance(payload, dict) else 1
        if not isinstance(payload, dict) or "alarms" not in payload:
            self._cache = None
            self._backup_corrupt()
            raise StorageError(f"State file has unexpected shape: {self.path}")
        if version != SCHEMA_VERSION:
            payload = self._apply_migrations(payload, from_version=version)
        alarms: list[Alarm] = []
        try:
            for entry in payload.get("alarms", []):
                alarms.append(Alarm.from_dict(entry))
        except Exception as exc:
            self._cache = None
            self._backup_corrupt()
            raise StorageError(f"State file has invalid alarm entries: {exc}") from exc
        if key is not None:
            self._cache = (key, alarms)
        return alarms

    def _stat_key(self) -> tuple[int, int] | None:
        """(mtime_ns, size) fingerprint, or None when stat fails."""
        try:
            st = self.path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def _apply_migrations(self, payload: dict | list, from_version: int) -> dict:
        current = (
            payload if isinstance(payload, dict) else {"version": 0, "alarms": payload}
        )
        v = (
            from_version
            if isinstance(payload, list)
            else int(current.get("version", 0))
        )
        while v < SCHEMA_VERSION:
            fn = MIGRATIONS.get((v, v + 1))
            if fn is None:
                raise StorageError(f"Cannot migrate state v{v} -> v{SCHEMA_VERSION}")
            current = fn(current)
            v += 1
        current["version"] = SCHEMA_VERSION
        return current

    def _write_all_locked(self, alarms: list[Alarm]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._cache = None  # mutated below; never serve stale entries
        payload = {"version": SCHEMA_VERSION, "alarms": [a.to_dict() for a in alarms]}
        fd, tmp = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".alarms-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
                fh.write("\n")
                fh.flush()
                with contextlib.suppress(Exception):
                    os.fsync(fh.fileno())
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self.path)
            with contextlib.suppress(Exception):
                os.chmod(self.path, 0o600)
        except OSError as exc:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise StorageError(f"Cannot write state file {self.path}: {exc}") from exc

    def _backup_corrupt(self) -> None:
        try:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            backup = self.path.with_name(f"{self.path.name}.corrupt-{stamp}.bak")
            if self.path.exists():
                backup.write_bytes(self.path.read_bytes())
        except OSError:
            pass

    @contextlib.contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # POSIX: real flock on the state file itself.
        try:
            import fcntl  # type: ignore[import-not-found]

            with open(self.path, "a+", encoding="utf-8") as fh:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                    yield
                finally:
                    with contextlib.suppress(OSError):
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            return
        except ImportError:
            pass
        except OSError:
            pass
        # Windows: msvcrt locking on a sidecar.
        try:  # pragma: no cover - Windows-only branch
            import msvcrt  # type: ignore[import-not-found]

            with contextlib.closing(open(self._lock_path, "w", encoding="utf-8")) as fh:
                try:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                    yield
                finally:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            return
        except ImportError:
            pass
        except OSError:
            pass
        # Fallback: exclusive-create sidecar spin lock (short critical sections).
        deadline = time.time() + 10
        while True:
            try:
                fd = os.open(str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                break
            except FileExistsError:
                if time.time() > deadline:
                    raise StorageError(f"State file locked: {self.path}")
                time.sleep(0.02)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                os.unlink(self._lock_path)
