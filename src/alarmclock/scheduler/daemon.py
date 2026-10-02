"""Background daemon.

Cross-platform strategy (stdlib only):
- `start()` spawns a detached child (`sys.executable -m alarmclock.cli.main
  daemon _child`) with its own session (POSIX setsid / Windows detached),
  stdout+stderr appended to a rotating log file.
- Liveness via PID file + stale-PID detection (`os.kill(pid, 0)`).
- Command channel: JSON files dropped in `<datadir>/commands/` and polled
  each tick (file watch). Used for `stop` and to forward
  snooze/dismiss promptly; the state file remains the source of truth.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from alarmclock.errors import SchedulerError

log = logging.getLogger("alarmclock.daemon")

PID_FILENAME = "alarmd.pid"
LOG_FILENAME = "alarmd.log"
COMMANDS_DIRNAME = "commands"
COMMAND_MAX_AGE = 300.0
START_WAIT_SECONDS = 5.0


class DaemonManager:
    def __init__(
        self,
        data_dir: str | Path,
        pid_alive: Callable[[int], bool] | None = None,
        kill_fn: Callable[[int, int], None] | None = None,
        spawn_fn: Callable[[float], int] | None = None,
        extra_argv: list[str] | None = None,
    ):
        self.data_dir = Path(data_dir).expanduser()
        self.pid_file = self.data_dir / PID_FILENAME
        self.log_file = self.data_dir / LOG_FILENAME
        self.commands_dir = self.data_dir / COMMANDS_DIRNAME
        self.extra_argv = list(extra_argv or [])
        self._pid_alive = pid_alive or _pid_alive
        self._kill = kill_fn or _kill
        self._spawn = spawn_fn or self._default_spawn

    # -- status ---------------------------------------------------------
    def read_pid(self) -> int | None:
        try:
            return int(self.pid_file.read_text(encoding="utf-8").strip().split()[0])
        except (OSError, ValueError, IndexError):
            return None

    def is_running(self) -> bool:
        pid = self.read_pid()
        if pid is None:
            return False
        if self._pid_alive(pid):
            return True
        # Stale PID file: clean up so a fresh start works.
        with contextlib.suppress(OSError):
            self.pid_file.unlink()
        return False

    def status(self) -> dict:
        pid = self.read_pid()
        running = pid is not None and self._pid_alive(pid)
        return {
            "running": running,
            "pid": pid if running else None,
            "pid_file": str(self.pid_file),
        }

    # -- lifecycle ------------------------------------------------------
    def start(self, tick: float = 1.0) -> int:
        if self.is_running():
            raise SchedulerError(f"Daemon already running (pid {self.read_pid()})")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.commands_dir.mkdir(parents=True, exist_ok=True)
        pid = self._spawn(tick)
        deadline = time.monotonic() + START_WAIT_SECONDS
        while time.monotonic() < deadline:
            if self.is_running():
                return self.read_pid() or pid
            time.sleep(0.1)
        # Child never reported healthy; try to avoid orphans.
        with contextlib.suppress(OSError):
            self._kill(pid, signal.SIGTERM)
        raise SchedulerError("Daemon failed to start (no PID file); see alarmd.log")

    def stop(self, timeout: float = 10.0) -> bool:
        pid = self.read_pid()
        if pid is None:
            self.purge_commands()
            return False
        if not self._pid_alive(pid):
            with contextlib.suppress(OSError):
                self.pid_file.unlink()
            self.purge_commands()
            return False
        # Ask nicely first (fast path: daemon polls the channel each tick),
        # then SIGTERM, then SIGKILL as a last resort.
        with contextlib.suppress(OSError):
            self.enqueue_command("stop")
        with contextlib.suppress(OSError):
            self._kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._pid_alive(pid):
                break
            time.sleep(0.2)
        else:
            with contextlib.suppress(OSError):
                self._kill(pid, signal.SIGKILL)
        with contextlib.suppress(OSError):
            if not self._pid_alive(pid) and self.read_pid() == pid:
                self.pid_file.unlink()
        # No consumer remains: drop queued commands so a future daemon never
        # acts on this generation's leftovers (e.g. a stale `stop` file from
        # a SIGTERM that beat the drain would instantly kill its successor).
        self.purge_commands()
        return True

    def purge_commands(self, action: str | None = None) -> int:
        """Delete queued command files (optionally only one action)."""
        if not self.commands_dir.is_dir():
            return 0
        removed = 0
        for path in sorted(self.commands_dir.glob("*.json")):
            if action is not None:
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    payload = {}
                if payload.get("action") != action:
                    continue
            with contextlib.suppress(OSError):
                path.unlink()
                removed += 1
        return removed

    # -- command channel --------------------------------------------------
    def enqueue_command(
        self, action: str, alarm_id: str = "", minutes: int | None = None
    ) -> Path:
        self.commands_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "action": action,
            "alarm_id": alarm_id,
            "minutes": minutes,
            "token": secrets.token_hex(8),
            "ts": time.time(),
        }
        # Atomic drop: write temp then rename so the daemon never reads partials.
        tmp = self.commands_dir / f".tmp-{payload['token']}.json"
        dst = self.commands_dir / f"{int(payload['ts'])}-{payload['token']}.json"
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, dst)
        return dst

    def drain_commands(self) -> list[dict]:
        if not self.commands_dir.is_dir():
            return []
        now = time.time()
        commands: list[dict] = []
        for path in sorted(self.commands_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if now - float(payload.get("ts", 0)) <= COMMAND_MAX_AGE:
                    commands.append(payload)
            except (OSError, ValueError):
                pass
            with contextlib.suppress(OSError):
                path.unlink()
        return commands

    # -- internals --------------------------------------------------------
    def _default_spawn(self, tick: float) -> int:
        """Spawn the detached child; returns its PID (best effort)."""
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        argv = (
            [sys.executable, "-m", "alarmclock.cli.main"]
            + self.extra_argv
            + [
                "--data-file",
                str(self.data_dir / "alarms.json"),
                "daemon",
                "_child",
                "--tick",
                str(tick),
            ]
        )
        if sys.platform == "win32":
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
            popen_kwargs: dict[str, object] = {
                "stdin": subprocess.DEVNULL,
                "creationflags": flags,
            }
        else:
            popen_kwargs = {"stdin": subprocess.DEVNULL, "start_new_session": True}
        try:
            # The child inherits its own dup of the log fd; ours closes here.
            with open(self.log_file, "a", encoding="utf-8") as logf:
                proc = subprocess.Popen(
                    argv, stdout=logf, stderr=subprocess.STDOUT, **popen_kwargs
                )
        except OSError as exc:
            raise SchedulerError(f"Cannot spawn daemon: {exc}") from exc
        return proc.pid


def daemon_main(
    data_dir: str | Path,
    tick: float = 1.0,
    stop_after: int | None = None,
    sleep_fn: Callable[[float], object] | None = None,
) -> int:
    """Child entry point: poll + fire until a `stop` command arrives."""
    import threading

    from alarmclock.audio.player import SystemPlayer
    from alarmclock.clock import SystemClock
    from alarmclock.config import AppConfig, load_config
    from alarmclock.logging_setup import setup_logging
    from alarmclock.notify.notifier import DesktopNotifier
    from alarmclock.scheduler.runner import Runner
    from alarmclock.services.service import AlarmService
    from alarmclock.storage.file_repository import FileAlarmRepository

    mgr = DaemonManager(data_dir)
    mgr.data_dir.mkdir(parents=True, exist_ok=True)
    logger = setup_logging(verbose=True, log_file=mgr.log_file)
    stop_requested = False
    stop_event = threading.Event()

    def _on_term(signum: int, frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True
        stop_event.set()  # wake the sleeper immediately (time.sleep retries)

    with contextlib.suppress(OSError):
        signal.signal(signal.SIGTERM, _on_term)

    config: AppConfig = load_config()
    repo = FileAlarmRepository(mgr.data_dir / "alarms.json")
    service = AlarmService(repo, SystemClock(), config)
    runner = Runner(
        service, SystemClock(), SystemPlayer(), DesktopNotifier(), config, tick=tick
    )
    sleeper = sleep_fn or stop_event.wait
    _write_pidfile(mgr.pid_file)
    logger.info("alarmd started (pid %d, tick %ss)", os.getpid(), tick)
    fired = 0
    iters = 0
    try:
        while not stop_requested:
            for cmd in mgr.drain_commands():
                action = cmd.get("action")
                if action == "stop":
                    stop_requested = True
                    break
                _apply_command(service, cmd, logger)
            if stop_requested:
                break
            fired += runner.tick_once()
            iters += 1
            if stop_after is not None and iters >= stop_after:
                break
            sleeper(min(tick, runner.sleep_delay()))
    finally:
        _remove_pidfile(mgr.pid_file)
        logger.info("alarmd stopped (fired %d)", fired)
    return fired


def _apply_command(service: object, cmd: dict, logger: logging.Logger) -> None:
    from alarmclock.errors import AlarmError
    from alarmclock.services.service import AlarmService

    assert isinstance(service, AlarmService)
    try:
        if cmd.get("action") == "snooze":
            alarm = service.repo.get(cmd.get("alarm_id", ""))
            if alarm is None:
                return
            now = service.clock.now("UTC")
            if alarm.snooze_until is None or alarm.snooze_until <= now:
                service.snooze(cmd["alarm_id"], cmd.get("minutes"))
        elif cmd.get("action") == "dismiss":
            alarm = service.repo.get(cmd.get("alarm_id", ""))
            if alarm is None:
                return
            service.dismiss(cmd["alarm_id"])
    except AlarmError as exc:
        logger.info("daemon command %s ignored: %s", cmd.get("action"), exc)


def _write_pidfile(path: Path) -> None:
    path.write_text(str(os.getpid()), encoding="utf-8")


def _remove_pidfile(path: Path) -> None:
    with contextlib.suppress(OSError):
        if path.exists():
            try:
                if (
                    int(path.read_text(encoding="utf-8").strip().split()[0])
                    == os.getpid()
                ):
                    path.unlink()
            except (ValueError, IndexError):
                pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, we just can't signal it
    except OSError:
        return False
    return True


def _kill(pid: int, sig: int) -> None:
    os.kill(pid, sig)
