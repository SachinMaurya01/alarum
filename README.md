# alarmclock

A production-grade command-line alarm clock. No GUI, no database — alarms live
in a single local JSON file and fire via a foreground loop or a background daemon.

- Fast, ergonomic CLI (Typer): `alarm add "in 25m"`, `alarm list`, `alarm next --watch`
- Rich terminal UI: styled tables on a TTY, live countdown, startup banner and
  spinner — plain output when piped, `NO_COLOR` respected
- Flexible times (`07:30`, `7:30pm`, `in 2h30m`, `tomorrow 6am`) and recurrence
  (`daily`, `weekdays`, `weekly:MO,WE`, raw `RRULE`)
- Reliable firing: wall-clock scheduler that naps until the next alarm,
  audio → system player → terminal-bell fallback, desktop notifications
- Interactive ringing: press `s`/`d` (+ Enter) in the ringing terminal to
  snooze or dismiss in place
- Safe state: atomic writes, file locking, `0o600` permissions, schema
  versioning, corrupt-file backup
- Scriptable: `--json` output, stable exit codes, `NO_COLOR`/pipe-aware output
- Operable: background daemon, `doctor` diagnostics, validated config,
  shell completions

<img width="1202" height="835" alt="Screenshot From 2026-10-04 10-48-59" src="https://github.com/user-attachments/assets/261f1ebb-937e-4c73-9e00-4fa0917158fa" />

## Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Command reference](#command-reference)
- [Time formats](#time-formats)
- [Recurrence](#recurrence)
- [When an alarm fires](#when-an-alarm-fires)
- [Background daemon](#background-daemon)
- [Configuration](#configuration)
- [State file and data safety](#state-file-and-data-safety)
- [Scripting and automation](#scripting-and-automation)
- [Diagnostics](#diagnostics)
- [Shell completion](#shell-completion)
- [Behavior and edge cases](#behavior-and-edge-cases)
- [Architecture](#architecture)
- [Development](#development)
- [Troubleshooting](#troubleshooting)
- [Changelog](#changelog)

## Requirements

- Python 3.10 or newer
- Runtime dependencies (installed automatically): `typer`, `rich`
- Works on Linux, macOS, and Windows

## Installation

Install as an isolated CLI app (recommended):

```bash
pipx install alarmclock
```

Or into the current environment:

```bash
pip install alarmclock
```

Or from a source checkout:

```bash
pip install .
```

Verify:

```bash
alarm doctor
```

## Quick start

```bash
# A weekday standup reminder
alarm add 07:30 --label Standup --repeat weekdays

# A focus timer 25 minutes from now
alarm add "in 25m" --label "Focus timer"

# Tomorrow morning in a specific time zone
alarm add "tomorrow 6am" --tz Asia/Kolkata

# See what's scheduled, and what fires next
alarm list
alarm next
alarm next --watch      # live countdown, Ctrl-C to quit

# Keep a scheduler in the foreground (Ctrl-C to stop)
alarm run
```

Your first alarm fires in under a minute:

```bash
alarm add "in 1m" && alarm run
```

## Command reference

Global flags (accepted before the subcommand): `--data-file PATH`,
`--config PATH`, `--json`, `--quiet`, `--verbose`, `--debug`. Most commands
also accept `--json` after the subcommand (e.g. `alarm list --json`).

### `alarm add <time> [--label] [--repeat] [--sound] [--tz]`

Create an alarm. Prints the new id plus its next fire time.

```bash
alarm add 07:30 --label Standup --repeat weekdays
alarm add "in 15m" --label "Tea break"
alarm add "2026-12-01 06:00" --label "Flight"  # one-shot on a fixed date
alarm add 18:00 --repeat weekly:MO,WE,FR --sound ~/chime.wav --tz Europe/Berlin
```

- `--repeat`: `once` (default), `daily`, `weekdays`, `weekends`,
  `weekly:MO,WE`, or a raw rule such as `FREQ=WEEKLY;BYDAY=MO,WE`.
- `--tz`: IANA time-zone name for the alarm (defaults to the configured zone).
- Invalid input prints a plain-English error and exits `2` — never a traceback.

### `alarm list [--all] [--json]`

List enabled alarms as a styled table on a TTY (plain text when piped),
including a `NEXT` column, or as JSON. `--all` includes disabled ones.

```bash
alarm list
alarm list --all --json
```

### `alarm remove <id>`

Delete an alarm. A unique id prefix works (`alarm remove f4f9`).

### `alarm enable <id>` / `alarm disable <id>`

Toggle an alarm without deleting it. Disabled alarms are skipped by `list`,
`next`, and the scheduler.

### `alarm edit <id> [time] [--label] [--repeat] [--sound] [--tz]`

Modify an alarm in place. Changing the time or time zone clears any pending
snooze (a new occurrence is being defined).

```bash
alarm edit f4f913 08:00 --label "Standup (moved)"
alarm edit f4f913 --repeat daily
```

### `alarm next [--watch] [--json]`

Show the next alarm to fire and how soon. `--watch` renders a live-updating
countdown (rich `Live`, Ctrl-C to quit; prints once when not a TTY). Prints
JSON `null` with `--json` when nothing is scheduled.

### `alarm run [--tick SEC]`

Run the scheduler in the foreground. Shows a startup banner (alarm count, next
fire, tick), then fires whatever is due, napping efficiently between alarms
(see [Behavior and edge cases](#behavior-and-edge-cases)). `--tick` sets the
minimum poll granularity (clamped to 1–30, default 1); Ctrl-C stops. See
[When an alarm fires](#when-an-alarm-fires).

### `alarm snooze <id> [minutes]`

Push the alarm's current occurrence `minutes` into the future (default from
`config snooze_minutes`, 5). Snoozes are capped per occurrence by
`max_snoozes` (default 3) — further attempts fail with exit code `2` until you
`dismiss`. A snooze always refires, even if the scheduler restarts in between.

### `alarm dismiss <id>`

End the current occurrence: clears any snooze and records the dismissal.
One-shot alarms are additionally disabled. If the daemon (or another terminal)
is ringing this alarm, it stops within about a second.

### `alarm daemon start|stop|status [--tick SEC]`

Manage the background scheduler. See [Background daemon](#background-daemon).

```bash
alarm daemon start --tick 5
alarm daemon status --json
alarm daemon stop
```

### `alarm config show|set <key> <value>`

Inspect or change configuration. See [Configuration](#configuration).

```bash
alarm config show
alarm config set snooze_minutes 10
alarm config set missed_policy skip
```

### `alarm doctor [--json]`

Check the environment: Python version, time-zone database, data directory,
state file (validity + permissions), config file, audio backends,
notification backends, daemon status. Exits `0` unless something is broken
(`1`); warnings (e.g. no desktop notifier — terminal output is used instead)
don't fail.

### `alarm completions <bash|zsh|fish>`

Print a completion script for the given shell. See
[Shell completion](#shell-completion).

## Time formats

| Form | Examples |
|---|---|
| 24-hour | `07:30`, `19:45` |
| 12-hour | `7:30pm`, `7:30 pm`, `6am`, `12pm` |
| Relative | `in 15m`, `in 2h30m`, `in 1h 20m`, `in 45s` |
| Natural day | `today 6pm`, `tomorrow 6am`, `tomorrow 06:30` |
| Fixed date | `2026-12-01 06:00`, `2026-12-01 6:00pm` |

Notes:

- Relative inputs resolve against the current time into a concrete wall-clock
  time (seconds are truncated to the minute).
- Combining a relative/natural time with `--repeat` creates a *recurring*
  alarm at that wall-clock time (e.g. `in 25m --repeat daily` rings daily at
  that time). An explicit `YYYY-MM-DD` date with `--repeat` is rejected — fixed
  dates are one-shot only.

## Recurrence

| Rule | Meaning |
|---|---|
| `once` | Next occurrence only (default) |
| `daily` | Every day at the alarm time |
| `weekdays` | Monday–Friday |
| `weekends` | Saturday–Sunday |
| `weekly:MO,WE,FR` | Chosen weekdays (`MO TU WE TH FR SA SU`) |
| `FREQ=DAILY` / `FREQ=WEEKLY;BYDAY=MO,WE` | Raw RRULE (daily/weekly only) |

Recurring alarms store a local wall-clock time plus the IANA zone name; the
next occurrence is computed on demand in that zone, so daylight-saving
transitions behave sanely (see
[Behavior and edge cases](#behavior-and-edge-cases)).

## When an alarm fires

1. A banner prints to the terminal: `⏰ ALARM: <label> (due <when>)`.
2. A desktop notification is sent (`osascript` on macOS, `notify-send` on
   Linux, PowerShell toast on Windows; terminal output if none is available).
   Disable with `alarm config set notifications false` or `ALARMCLOCK_NO_NOTIFY=1`.
3. Sound plays in ~1-second passes until the alarm is dismissed, snoozed, or
   `ring_timeout_seconds` elapses (default 300):
   - the alarm's `--sound` file via the OS player (`afplay`, `paplay`/`aplay`,
     `mpv`, Windows system sounds),
   - else the terminal bell (`\a`), which always works.
   While ringing in an interactive terminal you'll see
   `Press [s]nooze  [d]ismiss` — type `s` or `d` + Enter to snooze (default
   length) or dismiss without opening another terminal. (Terminals are
   line-buffered, so the Enter keystroke is required.)
4. Afterwards the occurrence is recorded: one-shots are disabled, recurring
   alarms resume their schedule, snooze counters reset — unless you snoozed
   mid-ring, in which case the snooze (and its count) is kept and refires.

If the alarm is past due by more than a 60-second grace window, the
`missed_policy` applies instead:

| Policy | Behavior |
|---|---|
| `fire-now` (default) | Ring now, late |
| `skip` | Silently skip the occurrence (one-shots are disabled) |
| `mark-missed` | Skip it and send a "missed" notification (one-shots are disabled) |

## Background daemon

The daemon is the same scheduler loop running detached, so alarms fire without
an open terminal.

```bash
alarm daemon start --tick 5   # poll granularity, 1–30 s (idle naps up to 30 s)
alarm daemon status            # pid + liveness
alarm daemon stop              # SIGTERM, then SIGKILL fallback
```

`start` shows a spinner while the child reports healthy, then its pid.

Implementation notes:

- **PID file** (`alarmd.pid` next to the state file) with stale-PID detection:
  a dead PID is cleaned up automatically, so a crash never wedges `start`.
- **Log file** (`alarmd.log`, rotating) captures firings and errors — the first
  place to look when something misbehaves.
- **Command channel** (`commands/`): `snooze`/`dismiss` typed in another
  terminal are forwarded to the daemon as JSON command files (atomic drop +
  polled each tick), *in addition to* the shared state file — so ringing stops
  within about a second either way.
- `stop` sends SIGTERM (graceful, finishes the current tick) and escalates to
  SIGKILL after a timeout. It also purges the command queue, so leftovers from
  this generation (e.g. an undrained `stop` file) can never kill the next
  daemon on startup.
- Running `alarm run` while the daemon lives is allowed; the shared state file
  plus occurrence bookkeeping normally prevents double-firing, but don't rely
  on it — pick one.

## Configuration

Precedence (highest first): CLI flags → `ALARMCLOCK_*` environment variables
→ config file → built-in defaults.

```bash
alarm config show
alarm config set <key> <value>
```

| Key | Default | Set example | Meaning |
|---|---|---|---|
| `default_timezone` | `local` | `alarm config set default_timezone Europe/Berlin` | IANA zone for new alarms (`local` = system zone) |
| `default_sound` | _(none)_ | `alarm config set default_sound ~/chime.wav` | Sound file for new alarms |
| `snooze_minutes` | `5` | `alarm config set snooze_minutes 10` | Default snooze length |
| `max_snoozes` | `3` | `alarm config set max_snoozes 1` | Snooze cap per occurrence (`0` disables snoozing) |
| `ring_timeout_seconds` | `300` | `alarm config set ring_timeout_seconds 60` | Ring duration before timeout |
| `missed_policy` | `fire-now` | `alarm config set missed_policy skip` | `fire-now` \| `skip` \| `mark-missed` |
| `notifications` | `true` | `alarm config set notifications false` | Desktop notifications (`true`/`false`) |

Values are validated (`config set` fails with exit `2` on bad keys/values).
The file is written atomically with mode `0o600`.

Environment overrides:

| Variable | Key |
|---|---|
| `ALARMCLOCK_TZ` | `default_timezone` |
| `ALARMCLOCK_SOUND` | `default_sound` |
| `ALARMCLOCK_SNOOZE_MINUTES` | `snooze_minutes` |
| `ALARMCLOCK_RING_TIMEOUT` | `ring_timeout_seconds` |
| `ALARMCLOCK_MISSED_POLICY` | `missed_policy` |
| `ALARMCLOCK_DATA_DIR` | state directory override |
| `ALARMCLOCK_NO_NOTIFY=1` | disables notifications |
| `ALARMCLOCK_DATA_FILE` | state file path (default location override) |
| `ALARMCLOCK_CONFIG_FILE` | config file path (default location override) |

Config file location: `<data-dir>/config.json`, or `--config PATH` /
`ALARMCLOCK_CONFIG_FILE` when specified.

## State file and data safety

- Location: the platform data directory — `~/Library/Application Support/alarmclock`
  (macOS), `%APPDATA%\alarmclock` (Windows), `$XDG_DATA_HOME/alarmclock` or
  `~/.local/share/alarmclock` (Linux) — overridable via `--data-file` /
  `ALARMCLOCK_DATA_FILE`. The daemon keeps `alarmd.pid`, `alarmd.log`, and
  `commands/` alongside it.
- Format: one JSON document, `{"version": 1, "alarms": [...]}`. Example entry:

```json
{
  "id": "a1b2c3",
  "label": "Standup",
  "time": "09:30",
  "timezone": "Asia/Kolkata",
  "recurrence": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
  "sound": null,
  "enabled": true,
  "snooze_until": null,
  "created_at": "2026-10-01T10:00:00+05:30",
  "date": null,
  "last_fired_at": null,
  "snooze_count": 0
}
```

- Writes are atomic (temp file + flush + `fsync` + rename), mode `0o600`.
- CLI and daemon coordinate through cross-platform file locking.
- A corrupt state file is never silently overwritten: it is backed up next to
  the original (`alarms.json.corrupt-<timestamp>.bak`) and a clear error is
  reported. Older schema versions migrate forward automatically.
- Back up `alarms.json` like any important file; everything needed to rebuild
  your schedule is in it.

## Scripting and automation

- `--json` on `add`, `list`, `next`, `remove`, `enable`/`disable`,
  `snooze`, `dismiss`, `edit`, `daemon status`, `daemon stop`,
  `doctor`, `config show`, `config set`:

```bash
alarm --json list | python3 -c "import json,sys; print(len(json.load(sys.stdin)), 'alarms')"
alarm --json next
```

- Stable exit codes: `0` success · `1` general/internal error · `2` invalid
  usage or bad input · `3` alarm not found · `4` storage error (corrupt,
  locked, permissions) · `5` scheduler/daemon error. `doctor` exits `1` when a
  check fails.
- Rich tables and colors on a TTY (`list`, startup banner, live countdown,
  spinner); plain output when piped. `NO_COLOR` is respected natively.
  `--quiet` suppresses normal output, `--verbose`/`--debug` add logging
  (`--debug` re-raises internal errors with a traceback for bug reports).
- The CLI never prints tracebacks under normal operation.

## Diagnostics

```bash
alarm doctor
alarm doctor --json
```

Checks performed:

| Check | Fails when |
|---|---|
| `python` | interpreter is older than 3.10 |
| `timezones` | zoneinfo database can't load common zones |
| `data-dir` | data directory can't be created/written |
| `state-file` | state file exists but is unreadable or invalid |
| `config` | config file is unreadable (warning: falls back to defaults) |
| `audio` | no system player found (warning: bell fallback is used) |
| `notifications` | no desktop backend found (warning: terminal output is used) |
| `daemon` | status probe errors (informational otherwise) |

## Shell completion

```bash
# bash
alarm completions bash >> ~/.bash_completion

# zsh
alarm completions zsh > ~/.zsh/completions/_alarm   # ensure the dir is in $fpath

# fish
alarm completions fish > ~/.config/fish/completions/alarm.fish
```

Completions cover subcommands (`add`, `daemon`, `config`, …), daemon/config
actions, and shell names.

## Behavior and edge cases

- **Naps, not polling**: the scheduler computes the time to the next alarm and
  sleeps until then (up to a 30 s heartbeat for clock changes and external
  edits), instead of waking every second. State reloads are guarded by a file
  `mtime` cache, so idle wakes cost a single `stat`. Firing is wall-clock
  based, so naps can never overshoot; imminent alarms still wake precisely.
  Suspend/resume and system clock changes are handled — there is no long
  `sleep` to drift.
- **No catch-up storms**: a (re)started scheduler only owns occurrences from
  when it starts watching. Yesterday's daily alarm won't fire at startup —
  except a *pending snooze*, which always refires (even late).
- **DST gaps** (spring forward, e.g. 02:30 doesn't exist): the alarm shifts
  forward to the first valid wall-clock time.
- **DST overlaps** (fall back, ambiguous hour): the first occurrence is used.
- **Recurring + relative time**: `alarm add "in 25m" --repeat daily` rings
  daily at that wall-clock time; only explicit `YYYY-MM-DD` dates are
  one-shot-only.
- **Concurrent CLI + daemon**: the state file lock serializes access, and
  firing is deduplicated per occurrence — but run one scheduler at a time.

## Architecture

Ports-and-adapters, dependency-injected. The domain, storage, scheduler,
audio, and notify layers are standard library only; the CLI boundary uses
Typer (commands, help, shell option parsing) and Rich (tables, live views,
banner, spinner):

```
src/alarmclock/
  cli/          # Typer commands + rich/plain output (views.py, doctor.py, ...)
  domain/       # alarm model, recurrence, next-fire (pure, no I/O)
  services/     # AlarmService: add/list/remove/snooze orchestration
  storage/      # AlarmRepository protocol + atomic file implementation
  scheduler/    # foreground Runner, background DaemonManager, Scheduler protocol
  audio/        # Player protocol + system-player/bell backends
  notify/       # Notifier protocol + desktop-backend adapters
  config.py clock.py errors.py logging_setup.py
tests/          # pytest suite (unit, CLI, integration-style runner tests)
```

Principles: `Clock`, `Player`, `Notifier`, `AlarmRepository`, `Scheduler` are
protocols with fakes for tests; the domain layer does no I/O; every "now"
comes from the injected clock; the CLI parses input, calls services, and
formats output.

## Development

```bash
pip install -e ".[dev]"
pytest                                # 80% coverage gate (see pyproject)
ruff check src tests
ruff format --check src tests
mypy --strict src                     # note: pre-existing debt in platform
                                      # branches; contributions should add none
bandit -r src && pip-audit
```

- CI (`.github/workflows/ci.yml`) runs the full matrix: Linux/macOS/Windows ×
  Python 3.10–3.13, plus lint, format check, strict types, security scan, and
  dependency audit. Pre-commit hooks live in `.pre-commit-config.yaml`.
- No pytest available? `python3 run_tests_stdlib.py tests.test_*` (repo root)
  executes the same suite with a minimal fixture shim — CI still uses real pytest.
- Entry point: `alarm = alarmclock.cli.main:main` (`python main.py` also works
  as a thin shim).

## Troubleshooting

**No sound.** Run `alarm doctor` — if no system player is found, only the
terminal bell rings (some terminals mute it). Install `mpv`/`paplay` (Linux)
or check volume; pass an explicit `--sound file.wav` to test.

**Alarm didn't fire.** Is the scheduler running (`alarm daemon status`, or an
`alarm run` in a terminal)? Was the alarm `enable`d? Check `alarmd.log` for the
daemon. If the due time passed while nothing was watching, `missed_policy`
decides: `fire-now` rings late, `skip`/`mark-missed` don't — see
`alarm config show`. A fresh scheduler never back-fires old occurrences.

**Daemon won't start.** `alarm daemon status` — a stale `alarmd.pid` from a
crash is cleaned automatically; if it persists, check `alarmd.log` and that
the data directory is writable (`alarm doctor`).

**State file errors (exit 4).** Your data is backed up as
`alarms.json.corrupt-<timestamp>.bak` next to the original — inspect/restore
it, or delete the corrupt file to start fresh. Never edit `alarms.json` while
the daemon runs.

**Ringing prompt doesn't respond.** The prompt only listens on an interactive
terminal, and terminals are line-buffered — type the letter *plus Enter*.
Nothing to press in daemon mode or pipes; use `alarm snooze/dismiss <id>`
from another terminal instead.

**Snooze refused.** You've hit `max_snoozes` for this occurrence — `dismiss`
it or raise the cap: `alarm config set max_snoozes 5`.

## Changelog

See [CHANGELOG.md](CHANGELOG.md). Current version: **1.0.0**
(`src/alarmclock/__init__.py`, `pyproject.toml`).
