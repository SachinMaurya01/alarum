"""Recurrence rules + next-fire calculation.

Supported normalized rules:
  ONCE | DAILY | WEEKDAYS | WEEKENDS | WEEKLY;BYDAY=MO,WE | FREQ=... (raw RRULE)

Only WEEKLY/DAILY FREQ RRULEs are interpreted; anything else raises
InvalidInput at normalize time so bad rules fail fast on `add`.
Next-fire is computed on demand from wall-clock time, never stored.

DST policy:
  - Gap (non-existent local time, spring forward): shift forward to the
    first valid wall-clock time (up to 3h scan, minute steps).
  - Overlap (ambiguous local time, fall back): use the first occurrence
    (fold=0).
"""

from __future__ import annotations

import re
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from alarmclock.domain.alarm import Alarm
from alarmclock.errors import InvalidInput

DAY_TO_NUM = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
NUM_TO_DAY = {v: k for k, v in DAY_TO_NUM.items()}
_BYDAY_RE = re.compile(r"^[A-Z]{2}(,[A-Z]{2})*$")


def normalize_recurrence(raw: str | None) -> str:
    """Normalize user/CLI repeat input to a stored rule."""
    if raw is None or raw.strip() == "" or raw.strip().lower() in {"once", "one-shot", "oneshot"}:
        return "ONCE"
    s = raw.strip()
    upper = s.upper()
    if upper in {"DAILY", "WEEKDAYS", "WEEKENDS"}:
        return upper
    if upper.startswith("WEEKLY"):
        # Accept "weekly", "weekly:MO,WE", "WEEKLY;BYDAY=MO,WE"
        rest = s[6:].strip()
        if rest == "":
            return "DAILY"  # bare 'weekly' without days is meaningless -> daily
        if rest.startswith(":"):
            rest = rest[1:]
        m = re.match(r"(?i)BYDAY=(.+)", rest)
        days = m.group(1) if m else rest
        day_list = [d.strip().upper() for d in days.split(",") if d.strip()]
        if not day_list or any(d not in DAY_TO_NUM for d in day_list):
            raise InvalidInput(f"Bad weekly days: {raw!r}. Use e.g. weekly:MO,WE")
        return "WEEKLY;BYDAY=" + ",".join(day_list)
    if upper.startswith("FREQ="):
        return _normalize_rrule(s)
    raise InvalidInput(
        f"Bad recurrence {raw!r}. Use once|daily|weekdays|weekends|weekly:MO,WE or FREQ=WEEKLY;BYDAY=MO,WE"
    )


def _normalize_rrule(s: str) -> str:
    parts: dict[str, str] = {}
    for chunk in s.split(";"):
        if "=" not in chunk:
            raise InvalidInput(f"Bad RRULE {s!r}")
        k, v = chunk.split("=", 1)
        parts[k.strip().upper()] = v.strip().upper()
    freq = parts.get("FREQ")
    if freq not in {"DAILY", "WEEKLY"}:
        raise InvalidInput(f"Only FREQ=DAILY/WEEKLY supported, got {s!r}")
    if freq == "WEEKLY":
        byday = parts.get("BYDAY", "")
        days = [d for d in byday.split(",") if d]
        if not days or any(d not in DAY_TO_NUM for d in days):
            raise InvalidInput(f"WEEKLY RRULE needs BYDAY, got {s!r}")
        return "FREQ=WEEKLY;BYDAY=" + ",".join(days)
    return "FREQ=DAILY"


def allowed_weekdays(recurrence: str) -> set[int] | None:
    """Weekday numbers (Mon=0) allowed by the rule, or None for daily/once."""
    r = recurrence.upper()
    if r in {"ONCE", "DAILY", "FREQ=DAILY"}:
        return None
    if r == "WEEKDAYS":
        return {0, 1, 2, 3, 4}
    if r == "WEEKENDS":
        return {5, 6}
    m = re.search(r"BYDAY=([A-Z,]+)", r)
    if m:
        return {DAY_TO_NUM[d] for d in m.group(1).split(",")}
    return None


def _make_aware(candidate_date: date, hour: int, minute: int, zone: ZoneInfo) -> datetime:
    """Build aware datetime honoring the DST gap/overlap policy."""
    naive_base = datetime.combine(candidate_date, dtime(hour, minute))
    # Overlap: fold=0 picks first occurrence.
    dt = naive_base.replace(tzinfo=zone, fold=0)
    # Gap detection: round-trip through UTC; a gap shifts wall time.
    back = dt.astimezone(ZoneInfo("UTC")).astimezone(zone)
    if (back.hour, back.minute) != (hour, minute) or back.date() != candidate_date:
        # Scan forward for first valid wall time (spring-forward gaps < 3h).
        probe = naive_base
        for _ in range(1, 3 * 60 + 1):
            probe = probe + timedelta(minutes=1)
            cand = probe.replace(tzinfo=zone, fold=0)
            rt = cand.astimezone(ZoneInfo("UTC")).astimezone(zone).replace(fold=0)
            if (rt.hour, rt.minute) == (probe.hour, probe.minute) and rt.date() == probe.date():
                return cand
        return dt  # fallback: return as-is rather than crash
    return dt


def next_fire(alarm: Alarm, now: datetime) -> datetime | None:
    """Next datetime this alarm fires after `now`, or None if never."""
    if not alarm.enabled:
        return None
    if now.tzinfo is None:
        raise InvalidInput("now must be timezone-aware")
    zone = ZoneInfo(alarm.timezone)
    now_local = now.astimezone(zone)

    # Snooze overrides the rule while it is in the future.
    if alarm.snooze_until is not None and alarm.snooze_until > now:
        return alarm.snooze_until

    # Absolute one-shot date: single candidate.
    if alarm.date is not None:
        try:
            y, mo, d = (int(p) for p in alarm.date.split("-"))
            cand = _make_aware(date(y, mo, d), alarm.hour, alarm.minute, zone)
        except ValueError as exc:
            raise InvalidInput(f"Bad alarm date {alarm.date!r}") from exc
        # Compare in a common timeline; allow 1s tolerance handled by caller.
        return cand if cand > now else None

    allowed = allowed_weekdays(alarm.recurrence)
    if alarm.recurrence.upper() == "ONCE":
        today_cand = _make_aware(now_local.date(), alarm.hour, alarm.minute, zone)
        if today_cand > now:
            return today_cand
        tomorrow = now_local.date() + timedelta(days=1)
        return _make_aware(tomorrow, alarm.hour, alarm.minute, zone)

    for offset in range(0, 366):
        day = now_local.date() + timedelta(days=offset)
        if allowed is not None and day.weekday() not in allowed:
            continue
        cand = _make_aware(day, alarm.hour, alarm.minute, zone)
        if cand > now:
            return cand
    return None


def last_fire(alarm: Alarm, now: datetime) -> datetime | None:
    """Most recent scheduled occurrence at or before `now` (scheduler use).

    Returns None when nothing is due (disabled, snoozed into the future,
    or one-shot still in the future). Unlike `next_fire`, a past-due
    one-shot returns its (past) candidate so the missed-alarm policy
    can act on it instead of silently rolling to tomorrow.
    """
    if not alarm.enabled:
        return None
    if now.tzinfo is None:
        raise InvalidInput("now must be timezone-aware")
    zone = ZoneInfo(alarm.timezone)

    if alarm.snooze_until is not None:
        if alarm.snooze_until > now:
            return None
        return alarm.snooze_until

    if alarm.date is not None:
        try:
            y, mo, d = (int(p) for p in alarm.date.split("-"))
            cand = _make_aware(date(y, mo, d), alarm.hour, alarm.minute, zone)
        except ValueError as exc:
            raise InvalidInput(f"Bad alarm date {alarm.date!r}") from exc
        return cand if cand <= now else None

    now_local = now.astimezone(zone)
    if alarm.recurrence.upper() == "ONCE":
        cand = _make_aware(now_local.date(), alarm.hour, alarm.minute, zone)
        return cand if cand <= now else None

    allowed = allowed_weekdays(alarm.recurrence)
    for offset in range(0, 366):
        day = now_local.date() - timedelta(days=offset)
        if allowed is not None and day.weekday() not in allowed:
            continue
        cand = _make_aware(day, alarm.hour, alarm.minute, zone)
        if cand <= now:
            return cand
    return None
