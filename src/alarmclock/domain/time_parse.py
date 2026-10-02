"""Minimal time-input grammar — stdlib only.

Accepts:
  Absolute: 07:30, 7:30pm, 7:30 pm, 19:30, 6am, 6:05 am
  Dated:    2026-12-01 06:00, 2026-12-01 6:00pm
  Relative: in 15m, in 2h30m, in 1h 20m, in 45s
  Natural:  tomorrow 6am, tomorrow 06:30, today 6pm

Returns (hour, minute, date|None, tzname). Relative inputs resolve
against `now` into a concrete one-shot (hour/minute + date).

Invalid input raises InvalidInput with a user-facing message.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from alarmclock.errors import InvalidInput

_REL_RE = re.compile(r"^in\s+(.+)$", re.IGNORECASE)
_REL_PART = re.compile(r"(\d+)\s*([hms])", re.IGNORECASE)
_DATED_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2})(?::(\d{2}))?\s*(?:([ap])\.?\s?m?\.?)?$",
    re.IGNORECASE,
)
_HM_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(?:([ap])\.?\s?m?\.?)?$", re.IGNORECASE)
_NAT_RE = re.compile(
    r"^(today|tomorrow)\s+(\d{1,2})(?::(\d{2}))?\s*(?:([ap])\.?\s?m?\.?)?$",
    re.IGNORECASE,
)


def parse_time_input(
    raw: str, now: datetime, default_tz: str
) -> tuple[int, int, str | None, str]:
    s = raw.strip()
    if not s:
        raise InvalidInput("Empty time. Try 07:30, 'in 15m', 'tomorrow 6am'.")
    zone = ZoneInfo(default_tz)

    m = _REL_RE.match(s)
    if m:
        return _parse_relative(m.group(1), now, default_tz)

    m = _DATED_RE.match(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hour, minute = _to_24h(m.group(4), m.group(5), m.group(6))
        try:
            datetime(y, mo, d, hour, minute, tzinfo=zone)
        except ValueError as exc:
            raise InvalidInput(f"Bad date/time {raw!r}: {exc}") from exc
        return hour, minute, f"{y:04d}-{mo:02d}-{d:02d}", default_tz

    m = _NAT_RE.match(s)
    if m:
        day_word = m.group(1).lower()
        hour, minute = _to_24h(m.group(2), m.group(3), m.group(4))
        base = now.astimezone(zone).date()
        if day_word == "tomorrow":
            base = base + timedelta(days=1)
        return hour, minute, base.isoformat(), default_tz

    m = _HM_RE.match(s)
    if m:
        hour, minute = _to_24h(m.group(1), m.group(2), m.group(3))
        return hour, minute, None, default_tz

    raise InvalidInput(
        f"Could not parse time {raw!r}. Try 07:30, 7:30pm, 'in 15m', 'tomorrow 6am', '2026-12-01 06:00'."
    )


def _to_24h(h: str, mi: str | None, ampm: str | None) -> tuple[int, int]:
    hour, minute = int(h), int(mi) if mi else 0
    if minute > 59:
        raise InvalidInput(f"Bad minutes in {h}:{mi}")
    if ampm:
        ap = ampm.lower()
        if hour < 1 or hour > 12:
            raise InvalidInput(f"Hour out of range for am/pm: {h}")
        if ap == "a":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12
    elif hour > 23:
        raise InvalidInput(f"Hour out of range: {h}")
    return hour, minute


def _parse_relative(
    body: str, now: datetime, tzname: str
) -> tuple[int, int, str | None, str]:
    parts = _REL_PART.findall(body)
    if not parts or "".join(f"{n}{u}" for n, u in parts).replace(" ", "") != re.sub(
        r"\s+", "", body
    ):
        raise InvalidInput(f"Bad relative time 'in {body}'. Try 'in 15m', 'in 2h30m'.")
    delta = timedelta()
    for num, unit in parts:
        n = int(num)
        if unit.lower() == "h":
            delta += timedelta(hours=n)
        elif unit.lower() == "m":
            delta += timedelta(minutes=n)
        else:
            delta += timedelta(seconds=n)
    if delta <= timedelta(0):
        raise InvalidInput("Relative time must be in the future")
    zone = ZoneInfo(tzname)
    target = now.astimezone(zone) + delta
    return target.hour, target.minute, target.date().isoformat(), tzname
