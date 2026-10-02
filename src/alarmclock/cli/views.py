"""Terminal views: rich widgets on a TTY, plain text otherwise.

Rich honors NO_COLOR and non-TTY output automatically, but callers
explicitly choose the plain path so piped/scripted output stays stable
and byte-identical.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from alarmclock import __version__
from alarmclock.domain.alarm import Alarm


def console() -> Console:
    return Console()


def alarm_table(alarms: list[Alarm], next_map: dict[str, datetime]) -> Table:
    table = Table(title="Alarms", show_lines=False)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("TIME", style="bold", justify="right")
    table.add_column("TZ", style="dim")
    table.add_column("REPEAT")
    table.add_column("LABEL")
    table.add_column("STATE")
    table.add_column("NEXT", style="green")
    for a in alarms:
        nxt = next_map.get(a.id)
        table.add_row(
            a.id,
            a.time_str,
            a.timezone,
            a.recurrence,
            a.label or "-",
            "[green]on[/green]" if a.enabled else "[dim]off[/dim]",
            nxt.strftime("%Y-%m-%d %H:%M") if nxt else "-",
        )
    return table


def run_banner(alarm_count: int, next_text: str, tick: float) -> Panel:
    return Panel(
        f"[bold]{alarm_count}[/bold] alarm(s) · next: {next_text} · "
        f"tick {tick:g}s · Ctrl-C to stop",
        title=f"alarmclock {__version__}",
        border_style="green",
    )


@contextlib.contextmanager
def spinner(message: str, enabled: bool) -> Iterator[None]:
    """Rich spinner while blocking; silent no-op when not wanted."""
    if enabled:
        with console().status(message):
            yield
    else:
        yield
