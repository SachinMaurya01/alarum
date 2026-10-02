"""CLI entry point. Argparse now; Typer+Rich later.

Commands: add, list, remove, enable, disable, next, run, snooze, dismiss,
edit, daemon, config, doctor, completions.
All read commands support --json. Exit codes: 0 ok, 1 error, 2 bad input,
3 not found, 4 storage error, 5 scheduler error.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from alarmclock.audio.player import SystemPlayer
from alarmclock.clock import SystemClock
from alarmclock.config import AppConfig, data_file_path, load_config
from alarmclock.domain.recurrence import next_fire
from alarmclock.errors import AlarmError, exit_code_for
from alarmclock.logging_setup import setup_logging
from alarmclock.notify.notifier import DesktopNotifier
from alarmclock.scheduler.daemon import DaemonManager, daemon_main
from alarmclock.scheduler.runner import Runner
from alarmclock.services.service import AlarmService
from alarmclock.storage.file_repository import FileAlarmRepository
from alarmclock.cli.formatting import dim, format_table, green


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="alarm", description="CLI alarm clock")
    p.add_argument("--data-file", default=None, help="State file path (overrides default)")
    p.add_argument("--config", default=None, help="Config file path")
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                   help="Machine-readable output")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--debug", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("add", help="Create an alarm")
    a.add_argument("time", help='e.g. 07:30, "in 15m", "tomorrow 6am"')
    a.add_argument("--label", default="")
    a.add_argument("--repeat", default=None, help="once|daily|weekdays|weekends|weekly:MO,WE|RRULE")
    a.add_argument("--sound", default=None)
    a.add_argument("--tz", default=None)

    li = sub.add_parser("list", help="List alarms")
    li.add_argument("--all", action="store_true", help="Include disabled alarms")

    r = sub.add_parser("remove", help="Delete an alarm")
    r.add_argument("id")

    e = sub.add_parser("enable", help="Enable an alarm")
    e.add_argument("id")
    d = sub.add_parser("disable", help="Disable an alarm")
    d.add_argument("id")

    n = sub.add_parser("next", help="Show next alarm to fire")
    n.add_argument("--watch", action="store_true", help="Live countdown (refreshes)")

    run = sub.add_parser("run", help="Foreground scheduler loop")
    run.add_argument("--tick", type=float, default=1.0, help="Poll interval seconds (1-30)")

    s = sub.add_parser("snooze", help="Snooze an alarm")
    s.add_argument("id")
    s.add_argument("minutes", nargs="?", type=int, default=None)
    di = sub.add_parser("dismiss", help="Dismiss an alarm")
    di.add_argument("id")

    ed = sub.add_parser("edit", help="Modify an alarm")
    ed.add_argument("id")
    ed.add_argument("time", nargs="?", default=None, help='e.g. 07:30, "in 15m"')
    ed.add_argument("--label", default=None)
    ed.add_argument("--repeat", default=None)
    ed.add_argument("--sound", default=None)
    ed.add_argument("--tz", default=None)

    daemon_p = sub.add_parser("daemon", help="Background scheduler")
    dsub = daemon_p.add_subparsers(dest="daemon_cmd", required=True)
    d_start = dsub.add_parser("start", help="Start the background daemon")
    d_start.add_argument("--tick", type=float, default=1.0)
    dsub.add_parser("stop", help="Stop the background daemon")
    d_status = dsub.add_parser("status", help="Daemon status")
    d_status.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    d_child = dsub.add_parser("_child", help=argparse.SUPPRESS)
    d_child.add_argument("--tick", type=float, default=1.0)

    cfg_p = sub.add_parser("config", help="Show or change configuration")
    csub = cfg_p.add_subparsers(dest="config_cmd", required=True)
    c_show = csub.add_parser("show", help="Show effective configuration")
    c_show.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    c_set = csub.add_parser("set", help="Set a configuration value")
    c_set.add_argument("key", help="e.g. snooze_minutes")
    c_set.add_argument("value", help="e.g. 10")
    c_set.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    doc = sub.add_parser("doctor", help="Diagnose audio, notifications, storage")
    doc.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    comp = sub.add_parser("completions", help="Print shell completion script")
    comp.add_argument("shell", help="bash, zsh or fish")
    # Allow --json after the subcommand too (e.g. `alarm list --json`).
    # (doctor/status/show define their own --json, so are excluded here.)
    for sp in (a, li, r, e, d, n, run, s, di, ed, daemon_p):
        sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="Machine-readable output")
    return p


def _wire(args: argparse.Namespace) -> tuple[AlarmService, AppConfig, str]:
    config = load_config(args.config)
    data_file = args.data_file or (config.data_dir or str(data_file_path()))
    repo = FileAlarmRepository(data_file)
    return AlarmService(repo, SystemClock(), config), config, data_file


def _daemon_manager(data_file: str) -> DaemonManager:
    from pathlib import Path

    return DaemonManager(Path(data_file).expanduser().parent)


def _forward_to_daemon(data_file: str, action: str, alarm_id: str, minutes: int | None = None) -> None:
    """Best-effort: let a running daemon know about snooze/dismiss/stop."""
    try:
        mgr = _daemon_manager(data_file)
        if mgr.is_running():
            mgr.enqueue_command(action, alarm_id, minutes)
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)  # argparse usage errors -> exit 2, no traceback
    if not hasattr(args, "json"):
        args.json = False  # SUPPRESS defaults: flag given nowhere
    setup_logging(args.verbose, args.debug)
    try:
        return _dispatch(args)
    except AlarmError as exc:
        if not args.quiet:
            print(f"error: {exc}", file=sys.stderr)
        return exit_code_for(exc)
    except BrokenPipeError:
        return 0  # piped to head/tail: silent success per CLI convention
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001 - last resort: never a traceback
        if args.debug:
            raise
        print(f"internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def _dispatch(args: argparse.Namespace) -> int:
    service, config, data_file = _wire(args)
    now = datetime.now().astimezone()

    if args.command == "add":
        alarm = service.add(args.time, label=args.label, repeat=args.repeat, sound=args.sound, tz=args.tz)
        fire_at = next_fire(alarm, now)
        if args.json:
            print(json.dumps({"alarm": alarm.to_dict(), "next_fire": fire_at.isoformat() if fire_at else None}))
        elif not args.quiet:
            print(green(f"Added {alarm.id} {alarm.time_str} {alarm.timezone} ({alarm.recurrence})"))
            if fire_at:
                print(dim(f"  next fire: {fire_at:%Y-%m-%d %H:%M %Z}"))
        return 0

    if args.command == "list":
        alarms = service.list(include_disabled=args.all)
        if args.json:
            print(json.dumps([a.to_dict() for a in alarms], indent=2))
        elif not args.quiet:
            if not alarms:
                print(dim("No alarms."))
            else:
                rows = [
                    [a.id, a.time_str, a.timezone, a.recurrence, a.label or "-", "on" if a.enabled else "off"]
                    for a in alarms
                ]
                print(format_table(["ID", "TIME", "TZ", "REPEAT", "LABEL", "STATE"], rows))
        return 0

    if args.command == "remove":
        service.remove(args.id)
        if args.json:
            print(json.dumps({"removed": args.id}))
        elif not args.quiet:
            print(f"Removed {args.id}")
        return 0

    if args.command in {"enable", "disable"}:
        alarm = service.set_enabled(args.id, args.command == "enable")
        if args.json:
            print(json.dumps(alarm.to_dict()))
        elif not args.quiet:
            print(f"{'Enabled' if alarm.enabled else 'Disabled'} {alarm.id}")
        return 0

    if args.command == "next":
        nxt = service.next()
        if nxt is None:
            if args.json:
                print(json.dumps(None))
            elif not args.quiet:
                print(dim("No upcoming alarms."))
            return 0
        alarm, fire_at = nxt
        if args.json:
            print(json.dumps({"alarm": alarm.to_dict(), "next_fire": fire_at.isoformat()}))
        elif not args.quiet:
            if args.watch:
                _watch(alarm.id, service)
            else:
                delta = fire_at - now
                print(f"{alarm.id} {alarm.time_str} {alarm.label or ''} fires in {_fmt_delta(delta)} ({fire_at:%Y-%m-%d %H:%M %Z})")
        return 0

    if args.command == "run":
        tick = min(30.0, max(1.0, args.tick))
        runner = Runner(service, SystemClock(), SystemPlayer(), DesktopNotifier(), config, tick=tick)
        if not args.quiet:
            print(dim(f"Running foreground scheduler (tick {tick}s, Ctrl-C to stop)…"))
        runner.run_forever()
        return 0

    if args.command == "snooze":
        alarm = service.snooze(args.id, args.minutes)
        _forward_to_daemon(data_file, "snooze", alarm.id, args.minutes)
        if args.json:
            print(json.dumps(alarm.to_dict()))
        elif not args.quiet:
            print(f"Snoozed {alarm.id} until {alarm.snooze_until:%Y-%m-%d %H:%M %Z}")
        return 0

    if args.command == "dismiss":
        alarm = service.dismiss(args.id)
        _forward_to_daemon(data_file, "dismiss", alarm.id)
        if args.json:
            print(json.dumps(alarm.to_dict()))
        elif not args.quiet:
            print(f"Dismissed {alarm.id}")
        return 0

    if args.command == "edit":
        alarm = service.edit(
            args.id, time_expr=args.time, label=args.label,
            repeat=args.repeat, sound=args.sound, tz=args.tz,
        )
        if args.json:
            print(json.dumps(alarm.to_dict()))
        elif not args.quiet:
            print(f"Edited {alarm.id} {alarm.time_str} {alarm.timezone} ({alarm.recurrence})")
        return 0

    if args.command == "daemon":
        from pathlib import Path

        mgr = _daemon_manager(data_file)
        if args.daemon_cmd == "_child":
            tick = min(30.0, max(1.0, args.tick))
            daemon_main(Path(data_file).expanduser().parent, tick=tick)
            return 0
        if args.daemon_cmd == "start":
            tick = min(30.0, max(1.0, args.tick))
            if args.config:
                mgr.extra_argv = ["--config", args.config]
            pid = mgr.start(tick=tick)
            if args.json:
                print(json.dumps({"started": True, "pid": pid}))
            elif not args.quiet:
                print(f"Daemon started (pid {pid})")
            return 0
        if args.daemon_cmd == "stop":
            stopped = mgr.stop()
            if args.json:
                print(json.dumps({"stopped": stopped}))
            elif not args.quiet:
                print("Daemon stopped" if stopped else "Daemon was not running")
            return 0
        if args.daemon_cmd == "status":
            info = mgr.status()
            if args.json or getattr(args, "json", False):
                print(json.dumps(info))
            elif not args.quiet:
                if info["running"]:
                    print(f"Daemon running (pid {info['pid']})")
                else:
                    print("Daemon not running")
            return 0

    if args.command == "config":
        from dataclasses import asdict

        from alarmclock.config import CONFIG_KEYS, coerce_config_value, save_config

        if args.config_cmd == "show":
            data = asdict(config)
            if args.json:
                print(json.dumps(data, indent=2))
            elif not args.quiet:
                rows = [[k, str(v)] for k, v in data.items()]
                print(format_table(["KEY", "VALUE"], rows))
            return 0
        # config set
        value = coerce_config_value(args.key, args.value)
        setattr(config, args.key, value)
        path = save_config(config, args.config)
        if args.json:
            print(json.dumps({"key": args.key, "value": value, "path": str(path)}))
        elif not args.quiet:
            print(f"Set {args.key}={value} ({path})")
            print(dim(f"Available keys: {', '.join(CONFIG_KEYS)}"))
        return 0

    if args.command == "doctor":
        from alarmclock.cli.doctor import overall_status, run_checks

        checks = run_checks(data_file, args.config)
        if args.json:
            print(json.dumps({"status": overall_status(checks), "checks": checks}, indent=2))
        elif not args.quiet:
            rows = [[c["check"], c["status"].upper(), c["detail"]] for c in checks]
            print(format_table(["CHECK", "STATUS", "DETAIL"], rows))
        return 0 if overall_status(checks) != "fail" else 1

    if args.command == "completions":
        from alarmclock.cli.completions import completion_script

        print(completion_script(args.shell), end="")
        return 0

    parser = build_parser()
    parser.error(f"unknown command {args.command}")
    return 2


def _fmt_delta(delta) -> str:
    s = max(0, int(delta.total_seconds()))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {sec}s"
    return f"{sec}s"


def _watch(alarm_id: str, service: AlarmService) -> None:
    import time

    try:
        while True:
            nxt = service.next()
            if nxt is None:
                print("\rNo upcoming alarms.   ", end="", flush=True)
                return
            alarm, fire_at = nxt
            delta = fire_at - datetime.now().astimezone()
            print(f"\r{alarm.id} fires in {_fmt_delta(delta)}   ", end="", flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    raise SystemExit(main())
