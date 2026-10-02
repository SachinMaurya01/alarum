# Changelog

## 1.0.0 — 2026-10-01

First stable release. STDLib-only, Python 3.10+.

- **Domain core:** alarm model, injectable `Clock`, recurrence
  (`once/daily/weekdays/weekends/weekly/RRULE`), on-demand next-fire with
  documented DST gap/overlap policy.
- **Persistence:** atomic JSON writes (temp + fsync + rename, `0o600`),
  cross-platform file locking, schema versioning with migration, corrupt-file
  backup and recovery.
- **Basic CLI:** `add/list/remove/enable/disable/next` with `--json`,
  `NO_COLOR`/piped output handling, PRD exit codes.
- **Foreground runner:** short-tick wall-clock loop, audio → system
  player → terminal-bell fallback chain, desktop notifications.
- **Lifecycle:** snooze with `max_snoozes` cap, `dismiss`, `edit`,
  ring-until-dismissed/snoozed/timeout (externally interruptible),
  `fire-now|skip|mark-missed` missed-alarm policy; no catch-up storms on
  (re)start.
- **Daemon:** `daemon start/stop/status`, PID file with stale-PID
  detection, rotating log file, file-watch command channel.
- **Hardening:** `doctor`, `config show/set`, shell completions
  (bash/zsh/fish), never-a-traceback error handling.
- **Release:** packaging (`alarm` entry point), CI matrix, docs.
