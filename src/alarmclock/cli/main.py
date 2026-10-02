"""CLI entry point. Typer commands + rich output; machine-readable --json.

Exit codes: 0 ok, 1 error, 2 bad input, 3 not found, 4 storage error,
5 scheduler error.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta

import typer
from rich.console import Console
from rich.live import Live
from typer._click.exceptions import NoArgsIsHelpError, UsageError

from alarmclock.audio.player import SystemPlayer
from alarmclock.cli.completions import completion_script
from alarmclock.cli.doctor import overall_status, run_checks
from alarmclock.cli.formatting import dim, format_table, green
from alarmclock.cli.views import alarm_table, run_banner, spinner
from alarmclock.clock import SystemClock
from alarmclock.config import (
    AppConfig,
    coerce_config_value,
    data_file_path,
    load_config,
    save_config,
)
from alarmclock.domain.recurrence import next_fire
from alarmclock.errors import AlarmError, exit_code_for
from alarmclock.logging_setup import setup_logging
from alarmclock.notify.notifier import DesktopNotifier
from alarmclock.scheduler.daemon import DaemonManager, daemon_main
from alarmclock.scheduler.runner import Runner
from alarmclock.services.service import AlarmService
from alarmclock.storage.file_repository import FileAlarmRepository

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
daemon_app = typer.Typer(no_args_is_help=True, help="Background scheduler.")
config_app = typer.Typer(no_args_is_help=True, help="Show or change configuration.")
app.add_typer(daemon_app, name="daemon")
app.add_typer(config_app, name="config")

_DEBUG = False


@app.callback()
def _global(
    ctx: typer.Context,
    data_file: str | None = typer.Option(None, "--data-file", help="State file path."),
    config_path: str | None = typer.Option(None, "--config", help="Config file path."),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output."),
    quiet: bool = typer.Option(False, "--quiet", help="Suppress normal output."),
    verbose: bool = typer.Option(False, "--verbose", help="Verbose logging."),
    debug: bool = typer.Option(
        False, "--debug", help="Debug logging; re-raise errors."
    ),
) -> None:
    global _DEBUG
    _DEBUG = debug
    setup_logging(verbose, debug)
    ctx.obj = {
        "data_file": data_file,
        "config": config_path,
        "json": json_output,
        "quiet": quiet,
    }


def _wire(ctx: typer.Context) -> tuple[AlarmService, AppConfig, str]:
    opts = ctx.obj
    config = load_config(opts["config"])
    data_file = opts["data_file"] or (config.data_dir or str(data_file_path()))
    repo = FileAlarmRepository(data_file)
    return AlarmService(repo, SystemClock(), config), config, data_file


def _json_out(ctx: typer.Context, flag: bool) -> bool:
    return bool(ctx.obj["json"] or flag)


def _quiet(ctx: typer.Context) -> bool:
    return bool(ctx.obj["quiet"])


def _fail(exc: AlarmError, ctx: typer.Context) -> None:
    if not _quiet(ctx):
        print(f"error: {exc}", file=sys.stderr)
    raise typer.Exit(code=exit_code_for(exc))


def _daemon_manager(data_file: str) -> DaemonManager:
    from pathlib import Path

    return DaemonManager(Path(data_file).expanduser().parent)


def _forward_to_daemon(
    data_file: str, action: str, alarm_id: str, minutes: int | None = None
) -> None:
    """Best-effort: let a running daemon know about snooze/dismiss."""
    try:
        mgr = _daemon_manager(data_file)
        if mgr.is_running():
            mgr.enqueue_command(action, alarm_id, minutes)
    except OSError:
        pass


def _next_map(service: AlarmService, now: datetime) -> dict[str, datetime]:
    from alarmclock.domain.recurrence import next_fire as _nf

    result: dict[str, datetime] = {}
    for alarm in service.list(include_disabled=True):
        fire_at = _nf(alarm, now)
        if fire_at is not None:
            result[alarm.id] = fire_at
    return result


@app.command()
def add(
    ctx: typer.Context,
    time_expr: str = typer.Argument(..., help='e.g. 07:30, "in 15m", "tomorrow 6am".'),
    label: str = typer.Option("", "--label", help="Alarm label."),
    repeat: str | None = typer.Option(
        None, "--repeat", help="once|daily|weekdays|weekends|weekly:MO,WE|RRULE."
    ),
    sound: str | None = typer.Option(None, "--sound", help="Sound file."),
    tz: str | None = typer.Option(None, "--tz", help="IANA time zone."),
    json_flag: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Create an alarm."""
    service, _, _ = _wire(ctx)
    now = datetime.now().astimezone()
    try:
        alarm = service.add(time_expr, label=label, repeat=repeat, sound=sound, tz=tz)
    except AlarmError as exc:
        _fail(exc, ctx)
        return
    fire_at = next_fire(alarm, now)
    if _json_out(ctx, json_flag):
        print(
            json.dumps(
                {
                    "alarm": alarm.to_dict(),
                    "next_fire": fire_at.isoformat() if fire_at else None,
                }
            )
        )
    elif not _quiet(ctx):
        print(
            green(
                f"Added {alarm.id} {alarm.time_str} {alarm.timezone} ({alarm.recurrence})"
            )
        )
        if fire_at:
            print(dim(f"  next fire: {fire_at:%Y-%m-%d %H:%M %Z}"))


@app.command(name="list")
def list_alarms(
    ctx: typer.Context,
    all: bool = typer.Option(False, "--all", help="Include disabled alarms."),
    json_flag: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """List alarms."""
    service, _, _ = _wire(ctx)
    alarms = service.list(include_disabled=all)
    if _json_out(ctx, json_flag):
        print(json.dumps([a.to_dict() for a in alarms], indent=2))
        return
    if _quiet(ctx):
        return
    if not alarms:
        print(dim("No alarms."))
        return
    now = datetime.now().astimezone()
    nxt = _next_map(service, now)
    con = Console()
    if con.is_terminal:
        con.print(alarm_table(alarms, nxt))
    else:
        rows = [
            [
                a.id,
                a.time_str,
                a.timezone,
                a.recurrence,
                a.label or "-",
                "on" if a.enabled else "off",
                nxt[a.id].strftime("%Y-%m-%d %H:%M") if a.id in nxt else "-",
            ]
            for a in alarms
        ]
        print(
            format_table(["ID", "TIME", "TZ", "REPEAT", "LABEL", "STATE", "NEXT"], rows)
        )


@app.command()
def remove(
    ctx: typer.Context,
    alarm_id: str = typer.Argument(..., help="Alarm id (prefix ok)."),
    json_flag: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Delete an alarm."""
    service, _, _ = _wire(ctx)
    try:
        service.remove(alarm_id)
    except AlarmError as exc:
        _fail(exc, ctx)
        return
    if _json_out(ctx, json_flag):
        print(json.dumps({"removed": alarm_id}))
    elif not _quiet(ctx):
        print(f"Removed {alarm_id}")


@app.command()
def enable(
    ctx: typer.Context,
    alarm_id: str = typer.Argument(...),
    json_flag: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Enable an alarm."""
    _set_enabled(ctx, alarm_id, True, json_flag)


@app.command()
def disable(
    ctx: typer.Context,
    alarm_id: str = typer.Argument(...),
    json_flag: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Disable an alarm."""
    _set_enabled(ctx, alarm_id, False, json_flag)


def _set_enabled(
    ctx: typer.Context, alarm_id: str, enabled: bool, json_flag: bool
) -> None:
    service, _, _ = _wire(ctx)
    try:
        alarm = service.set_enabled(alarm_id, enabled)
    except AlarmError as exc:
        _fail(exc, ctx)
        return
    if _json_out(ctx, json_flag):
        print(json.dumps(alarm.to_dict()))
    elif not _quiet(ctx):
        print(f"{'Enabled' if alarm.enabled else 'Disabled'} {alarm.id}")


@app.command(name="next")
def show_next(
    ctx: typer.Context,
    watch: bool = typer.Option(False, "--watch", help="Live countdown."),
    json_flag: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Show the next alarm to fire."""
    service, _, _ = _wire(ctx)
    now = datetime.now().astimezone()
    nxt = service.next()
    if nxt is None:
        if _json_out(ctx, json_flag):
            print(json.dumps(None))
        elif not _quiet(ctx):
            print(dim("No upcoming alarms."))
        return
    alarm, fire_at = nxt
    if _json_out(ctx, json_flag):
        print(json.dumps({"alarm": alarm.to_dict(), "next_fire": fire_at.isoformat()}))
        return
    if _quiet(ctx):
        return
    if watch:
        _watch(service)
    else:
        delta = fire_at - now
        print(
            f"{alarm.id} {alarm.time_str} {alarm.label or ''} fires in {_fmt_delta(delta)} ({fire_at:%Y-%m-%d %H:%M %Z})"
        )


@app.command()
def run(
    ctx: typer.Context,
    tick: float = typer.Option(1.0, "--tick", help="Poll interval seconds (1-30)."),
    json_flag: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Run the foreground scheduler loop."""
    service, config, _ = _wire(ctx)
    tick = min(30.0, max(1.0, tick))
    runner = Runner(
        service, SystemClock(), SystemPlayer(), DesktopNotifier(), config, tick=tick
    )
    con = Console()
    if con.is_terminal and not _quiet(ctx):
        nxt = service.next()
        next_text = "none scheduled"
        if nxt is not None:
            alarm, fire_at = nxt
            delta = fire_at - datetime.now().astimezone()
            next_text = f"{alarm.label or alarm.id} in {_fmt_delta(delta)}"
        con.print(run_banner(len(service.list(include_disabled=True)), next_text, tick))
    elif not _quiet(ctx):
        print(dim(f"Running foreground scheduler (tick {tick}s, Ctrl-C to stop)…"))
    runner.run_forever()


@app.command()
def snooze(
    ctx: typer.Context,
    alarm_id: str = typer.Argument(...),
    minutes: int | None = typer.Argument(None, help="Snooze length in minutes."),
    json_flag: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Snooze an alarm."""
    service, _, data_file = _wire(ctx)
    try:
        alarm = service.snooze(alarm_id, minutes)
    except AlarmError as exc:
        _fail(exc, ctx)
        return
    _forward_to_daemon(data_file, "snooze", alarm.id, minutes)
    if _json_out(ctx, json_flag):
        print(json.dumps(alarm.to_dict()))
    elif not _quiet(ctx):
        print(f"Snoozed {alarm.id} until {alarm.snooze_until:%Y-%m-%d %H:%M %Z}")


@app.command()
def dismiss(
    ctx: typer.Context,
    alarm_id: str = typer.Argument(...),
    json_flag: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Dismiss an alarm."""
    service, _, data_file = _wire(ctx)
    try:
        alarm = service.dismiss(alarm_id)
    except AlarmError as exc:
        _fail(exc, ctx)
        return
    _forward_to_daemon(data_file, "dismiss", alarm.id)
    if _json_out(ctx, json_flag):
        print(json.dumps(alarm.to_dict()))
    elif not _quiet(ctx):
        print(f"Dismissed {alarm.id}")


@app.command()
def edit(
    ctx: typer.Context,
    alarm_id: str = typer.Argument(...),
    time_expr: str | None = typer.Argument(None, help='e.g. 07:30, "in 15m".'),
    label: str | None = typer.Option(None, "--label"),
    repeat: str | None = typer.Option(None, "--repeat"),
    sound: str | None = typer.Option(None, "--sound"),
    tz: str | None = typer.Option(None, "--tz"),
    json_flag: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Modify an alarm."""
    service, _, _ = _wire(ctx)
    try:
        alarm = service.edit(
            alarm_id,
            time_expr=time_expr,
            label=label,
            repeat=repeat,
            sound=sound,
            tz=tz,
        )
    except AlarmError as exc:
        _fail(exc, ctx)
        return
    if _json_out(ctx, json_flag):
        print(json.dumps(alarm.to_dict()))
    elif not _quiet(ctx):
        print(
            f"Edited {alarm.id} {alarm.time_str} {alarm.timezone} ({alarm.recurrence})"
        )


@daemon_app.command()
def start(
    ctx: typer.Context,
    tick: float = typer.Option(1.0, "--tick", help="Poll interval seconds (1-30)."),
    json_flag: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Start the background daemon."""
    _, _, data_file = _wire(ctx)
    mgr = _daemon_manager(data_file)
    if ctx.obj.get("config"):
        mgr.extra_argv = ["--config", ctx.obj["config"]]
    tick = min(30.0, max(1.0, tick))
    con = Console()
    try:
        with spinner("Starting daemon…", enabled=con.is_terminal and not _quiet(ctx)):
            pid = mgr.start(tick=tick)
    except AlarmError as exc:
        _fail(exc, ctx)
        return
    if _json_out(ctx, json_flag):
        print(json.dumps({"started": True, "pid": pid}))
    elif not _quiet(ctx):
        print(f"Daemon started (pid {pid})")


@daemon_app.command()
def stop(
    ctx: typer.Context,
    json_flag: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Stop the background daemon."""
    _, _, data_file = _wire(ctx)
    stopped = _daemon_manager(data_file).stop()
    if _json_out(ctx, json_flag):
        print(json.dumps({"stopped": stopped}))
    elif not _quiet(ctx):
        print("Daemon stopped" if stopped else "Daemon was not running")


@daemon_app.command()
def status(
    ctx: typer.Context,
    json_flag: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Show daemon status."""
    _, _, data_file = _wire(ctx)
    info = _daemon_manager(data_file).status()
    if _json_out(ctx, json_flag):
        print(json.dumps(info))
    elif not _quiet(ctx):
        if info["running"]:
            print(f"Daemon running (pid {info['pid']})")
        else:
            print("Daemon not running")


@daemon_app.command(name="_child", hidden=True)
def daemon_child(
    ctx: typer.Context,
    tick: float = typer.Option(1.0, "--tick"),
) -> None:
    """Daemon child entry (spawned by `daemon start`; not for manual use)."""
    from pathlib import Path

    _, _, data_file = _wire(ctx)
    tick = min(30.0, max(1.0, tick))
    daemon_main(Path(data_file).expanduser().parent, tick=tick)


@config_app.command()
def show(
    ctx: typer.Context,
    json_flag: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Show effective configuration."""
    from dataclasses import asdict

    _, config, _ = _wire(ctx)
    if _json_out(ctx, json_flag):
        print(json.dumps(asdict(config), indent=2))
    elif not _quiet(ctx):
        rows = [[k, str(v)] for k, v in asdict(config).items()]
        print(format_table(["KEY", "VALUE"], rows))


@config_app.command()
def set(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="e.g. snooze_minutes."),
    value: str = typer.Argument(..., help="e.g. 10."),
    json_flag: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Set a configuration value."""
    from alarmclock.config import CONFIG_KEYS

    _, config, _ = _wire(ctx)
    try:
        coerced = coerce_config_value(key, value)
    except AlarmError as exc:
        _fail(exc, ctx)
        return
    setattr(config, key, coerced)
    try:
        path = save_config(config, ctx.obj["config"])
    except AlarmError as exc:
        _fail(exc, ctx)
        return
    if _json_out(ctx, json_flag):
        print(json.dumps({"key": key, "value": coerced, "path": str(path)}))
    elif not _quiet(ctx):
        print(f"Set {key}={coerced} ({path})")
        print(dim(f"Available keys: {', '.join(CONFIG_KEYS)}"))


@app.command()
def doctor(
    ctx: typer.Context,
    json_flag: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Diagnose audio, notifications, storage and config."""
    _, _, data_file = _wire(ctx)
    checks = run_checks(data_file, ctx.obj["config"])
    status = overall_status(checks)
    if _json_out(ctx, json_flag):
        print(json.dumps({"status": status, "checks": checks}, indent=2))
    elif not _quiet(ctx):
        rows = [[c["check"], c["status"].upper(), c["detail"]] for c in checks]
        print(format_table(["CHECK", "STATUS", "DETAIL"], rows))
    if status == "fail":
        raise typer.Exit(code=1)


@app.command()
def completions(shell: str = typer.Argument(..., help="bash, zsh or fish.")) -> None:
    """Print a shell completion script."""
    try:
        print(completion_script(shell), end="")
    except AlarmError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise typer.Exit(code=exit_code_for(exc)) from None


def main(argv: list[str] | None = None) -> int:
    """Invoke the Typer app with an explicit argv; return the exit code."""
    try:
        result = app(
            args=list(argv) if argv is not None else None,
            prog_name="alarm",
            standalone_mode=False,
        )
        # typer.Exit(code) and --help surface as the return value.
        return result if isinstance(result, int) else 0
    except NoArgsIsHelpError:
        return 0  # help already shown; not an error
    except UsageError as exc:
        print(f"error: {exc.format_message()}", file=sys.stderr)
        return exc.exit_code
    except typer.Abort:
        return 1
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        if _DEBUG:
            raise
        print(f"internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def _fmt_delta(delta: timedelta) -> str:
    s = max(0, int(delta.total_seconds()))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {sec}s"
    return f"{sec}s"


def _watch(service: AlarmService) -> None:
    con = Console()
    if not con.is_terminal:
        nxt = service.next()
        if nxt is None:
            print(dim("No upcoming alarms."))
        else:
            alarm, fire_at = nxt
            delta = fire_at - datetime.now().astimezone()
            print(f"{alarm.id} fires in {_fmt_delta(delta)}")
        return
    try:
        with Live("", refresh_per_second=2, console=con) as live:
            while True:
                nxt = service.next()
                if nxt is None:
                    live.update("No upcoming alarms.   ")
                    return
                alarm, fire_at = nxt
                delta = fire_at - datetime.now().astimezone()
                live.update(
                    f"⏰ {alarm.id} {alarm.time_str} {alarm.label or ''} "
                    f"fires in [bold]{_fmt_delta(delta)}[/bold]"
                )
                time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    con.print()


if __name__ == "__main__":
    raise SystemExit(main())
