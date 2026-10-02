#!/usr/bin/env bash
# End-to-end exercise of every README scenario against an isolated state dir.
#
# Usage:
#   ./scripts/test_commands.sh                 # everything (~2 min, includes a live fire)
#   ./scripts/test_commands.sh --skip-live     # fast path (skips the ~60 s live-fire wait)
#
# Uses `alarm` from PATH when available, else the source tree
# (`python3 -m alarmclock.cli.main` with PYTHONPATH=src). Override with:
#   ALARM_BIN="python3 -m alarmclock.cli.main" ./scripts/test_commands.sh
#
# `--watch` mode is intentionally excluded (needs an interactive TTY);
# try `alarm next --watch` manually.

set -u

# Array word-splitting below needs real bash (zsh/sh won't do).
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi

SKIP_LIVE=0
if [ "${1:-}" = "--skip-live" ]; then
  SKIP_LIVE=1
fi

PASS=0
FAIL=0
TMPD="$(mktemp -d)"
export ALARMCLOCK_DATA_FILE="$TMPD/alarms.json"
export ALARMCLOCK_CONFIG_FILE="$TMPD/config.json"

# Anchor at the repo root so `python -m alarmclock...` resolves the source tree.
cd "$(dirname "$0")/.." || exit 1
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"

if [ -n "${ALARM_BIN:-}" ]; then
  # shellcheck disable=SC2206
  ALARM_CMD=($ALARM_BIN)
elif command -v alarm >/dev/null 2>&1; then
  ALARM_CMD=(alarm)
else
  ALARM_CMD=(python3 -m alarmclock.cli.main)
fi

cleanup() {
  "${ALARM_CMD[@]}" daemon stop >/dev/null 2>&1 || true
  rm -rf "$TMPD"
}
trap cleanup EXIT

pass() { PASS=$((PASS + 1)); echo "  ok: $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  FAIL: $1"; }

# expect_exit CODE DESCRIPTION -- COMMAND...
expect_exit() {
  want="$1"; desc="$2"; shift 2
  shift # drop the -- separator
  "$@" >/dev/null 2>&1
  got=$?
  if [ "$got" = "$want" ]; then pass "$desc (exit $got)"; else fail "$desc (want $want, got $got)"; fi
}

# expect_out PATTERN DESCRIPTION -- COMMAND...
expect_out() {
  pat="$1"; desc="$2"; shift 2
  shift # drop the -- separator
  out="$("$@" 2>/dev/null)"
  case "$out" in
    *"$pat"*) pass "$desc" ;;
    *) fail "$desc (missing '$pat' in: $(echo "$out" | head -2))" ;;
  esac
}

echo "== 1. add variants =="
expect_exit 0 "add absolute" -- "${ALARM_CMD[@]}" add 07:30 --label Standup --repeat weekdays
expect_exit 0 "add relative" -- "${ALARM_CMD[@]}" add "in 25m" --label "Focus timer"
expect_exit 0 "add natural + tz" -- "${ALARM_CMD[@]}" add "tomorrow 6am" --tz Asia/Kolkata --label Trip
expect_exit 0 "add fixed date one-shot" -- "${ALARM_CMD[@]}" add "2026-12-01 06:00" --label Flight
expect_exit 0 "add 12h + weekly" -- "${ALARM_CMD[@]}" add 6:30pm --repeat weekly:MO,WE,FR --label Gym
expect_exit 0 "add relative + repeat (recurs at wall time)" -- "${ALARM_CMD[@]}" add "in 20m" --repeat daily --label Meds
expect_exit 2 "reject dated + repeat" -- "${ALARM_CMD[@]}" add "2026-12-01 06:00" --repeat daily
expect_exit 2 "reject bad time" -- "${ALARM_CMD[@]}" add lunchtime

echo "== 2. list / next =="
expect_out "Standup" "list shows alarms" -- "${ALARM_CMD[@]}" list
expect_out "WEEKDAYS" "list shows recurrence" -- "${ALARM_CMD[@]}" list
expect_out "Standup" "list --all --json" -- "${ALARM_CMD[@]}" list --all --json
expect_out "fires in" "next countdown" -- "${ALARM_CMD[@]}" next
expect_out "next_fire" "next --json" -- "${ALARM_CMD[@]}" next --json

ID="$("${ALARM_CMD[@]}" list --json 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['id'])")"
echo "  (using id $ID)"

echo "== 3. enable / disable =="
expect_exit 0 "disable" -- "${ALARM_CMD[@]}" disable "$ID"
expect_out "off" "list --all shows off" -- "${ALARM_CMD[@]}" list --all
expect_exit 0 "enable" -- "${ALARM_CMD[@]}" enable "$ID"

echo "== 4. edit =="
expect_exit 0 "edit time+label" -- "${ALARM_CMD[@]}" edit "$ID" 08:00 --label "Standup (moved)"
expect_out "08:00" "edit applied" -- "${ALARM_CMD[@]}" list
expect_exit 3 "edit missing -> 3" -- "${ALARM_CMD[@]}" edit nope123 08:00

echo "== 5. snooze cap =="
"${ALARM_CMD[@]}" config set max_snoozes 1 >/dev/null 2>&1
SNOOZE_OUT="$("${ALARM_CMD[@]}" snooze "$ID" 2 2>/dev/null)"
case "$SNOOZE_OUT" in
  *"Snoozed"*) pass "snooze output" ;;
  *) fail "snooze output (got: $SNOOZE_OUT)" ;;
esac
expect_exit 2 "second snooze capped" -- "${ALARM_CMD[@]}" snooze "$ID" 2
"${ALARM_CMD[@]}" config set max_snoozes 3 >/dev/null 2>&1

echo "== 6. dismiss / remove =="
expect_exit 0 "dismiss resets" -- "${ALARM_CMD[@]}" dismiss "$ID"
expect_exit 0 "remove" -- "${ALARM_CMD[@]}" remove "$ID"
expect_exit 3 "remove missing -> 3" -- "${ALARM_CMD[@]}" remove "$ID"

echo "== 7. config =="
expect_out "snooze_minutes" "config show" -- "${ALARM_CMD[@]}" config show
expect_exit 0 "config set ok" -- "${ALARM_CMD[@]}" config set ring_timeout_seconds 120
expect_out "120" "config set applied" -- "${ALARM_CMD[@]}" config show
expect_out "missed_policy" "config set --json" -- "${ALARM_CMD[@]}" config set missed_policy skip --json
expect_exit 2 "config bad key" -- "${ALARM_CMD[@]}" config set nope 1
expect_exit 2 "config bad value" -- "${ALARM_CMD[@]}" config set snooze_minutes -3
expect_exit 2 "config bad policy" -- "${ALARM_CMD[@]}" config set missed_policy sometimes
"${ALARM_CMD[@]}" config set missed_policy fire-now >/dev/null 2>&1
"${ALARM_CMD[@]}" config set ring_timeout_seconds 5 >/dev/null 2>&1

echo "== 8. doctor / completions =="
expect_exit 0 "doctor healthy" -- "${ALARM_CMD[@]}" doctor
expect_out "state-file" "doctor table" -- "${ALARM_CMD[@]}" doctor
expect_out '"status"' "doctor --json" -- "${ALARM_CMD[@]}" doctor --json
expect_out "_alarm_complete" "completions bash" -- "${ALARM_CMD[@]}" completions bash
expect_out "#compdef alarm" "completions zsh" -- "${ALARM_CMD[@]}" completions zsh
expect_out "complete -c alarm" "completions fish" -- "${ALARM_CMD[@]}" completions fish
expect_exit 2 "completions bad shell" -- "${ALARM_CMD[@]}" completions powershell

echo "== 9. daemon lifecycle =="
expect_exit 0 "daemon start" -- "${ALARM_CMD[@]}" daemon start --tick 1
sleep 2
expect_out "running" "daemon status" -- "${ALARM_CMD[@]}" daemon status
expect_out '"running": true' "daemon status --json" -- "${ALARM_CMD[@]}" daemon status --json
expect_exit 5 "daemon double start -> 5" -- "${ALARM_CMD[@]}" daemon start --tick 1
expect_exit 0 "daemon stop" -- "${ALARM_CMD[@]}" daemon stop
sleep 1
expect_out "not running" "daemon stopped" -- "${ALARM_CMD[@]}" daemon status

echo "== 10. foreground run starts cleanly =="
"${ALARM_CMD[@]}" run --tick 1 >/dev/null 2>&1 &
RUN_PID=$!
sleep 3
if kill -0 "$RUN_PID" 2>/dev/null; then
  pass "run keeps polling"
  kill "$RUN_PID" 2>/dev/null || true
  for _ in $(seq 1 25); do
    kill -0 "$RUN_PID" 2>/dev/null || break
    sleep 0.2
  done
  if kill -0 "$RUN_PID" 2>/dev/null; then
    kill -9 "$RUN_PID" 2>/dev/null || true
    sleep 1
  fi
  if kill -0 "$RUN_PID" 2>/dev/null; then
    fail "run process would not die (pid $RUN_PID)"
  else
    pass "run stopped on demand"
  fi
else
  fail "run exited early"
fi
wait "$RUN_PID" 2>/dev/null || true

if [ "$SKIP_LIVE" = "1" ]; then
  echo "== 11. live fire SKIPPED (--skip-live) =="
else
  echo "== 11. live fire via daemon (<= ~100 s) =="
  "${ALARM_CMD[@]}" daemon start --tick 1 >/dev/null 2>&1
  sleep 2
  if ! "${ALARM_CMD[@]}" daemon status 2>/dev/null | grep -q "Daemon running"; then
    fail "daemon fired LIVE alarm (daemon not running after start)"
  else
    "${ALARM_CMD[@]}" add "in 1m" --label LIVE --repeat daily >/dev/null 2>&1
    FIRED=0
    for _ in $(seq 1 20); do
      sleep 5
      if grep -q "ALARM: LIVE" "$TMPD/alarmd.log" 2>/dev/null; then FIRED=1; break; fi
    done
    if [ "$FIRED" = "1" ]; then
      pass "daemon fired LIVE alarm"
    else
      fail "daemon fired LIVE alarm"
      echo "  --- diagnostics ---"
      "${ALARM_CMD[@]}" daemon status 2>/dev/null | sed 's/^/  /'
      "${ALARM_CMD[@]}" list --json 2>/dev/null | head -20 | sed 's/^/  /'
      tail -8 "$TMPD/alarmd.log" 2>/dev/null | sed 's/^/  /'
    fi
    "${ALARM_CMD[@]}" daemon stop >/dev/null 2>&1 || true
  fi
fi

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = "0" ]
