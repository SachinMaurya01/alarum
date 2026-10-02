"""Foreground scheduler runner.

Short-tick wall-clock loop: never one long sleep, so suspend/resume and
clock changes are handled. Missed-alarm policy: fire-now | skip | mark-missed.
Ring loop plays until dismissed, snoozed or timeout; one-shot alarms auto-disable.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta

from alarmclock.audio.player import Player
from alarmclock.clock import Clock
from alarmclock.config import AppConfig
from alarmclock.domain.recurrence import last_fire
from alarmclock.notify.notifier import Notifier
from alarmclock.services.service import AlarmService

log = logging.getLogger("alarmclock.scheduler")

TICK_SECONDS = 1.0
MISSED_GRACE = timedelta(seconds=60)


class Runner:
    def __init__(
        self,
        service: AlarmService,
        clock: Clock,
        player: Player,
        notifier: Notifier,
        config: AppConfig,
        tick: float = TICK_SECONDS,
        sleep_fn: Callable[[float], None] | None = None,
    ):
        self.service = service
        self.clock = clock
        self.player = player
        self.notifier = notifier
        self.config = config
        self.tick = tick
        self.sleep_fn = sleep_fn or time.sleep
        self._stop = False
        self._started_at: datetime | None = None

    def stop(self) -> None:
        self._stop = True

    def run_forever(self, max_iterations: int | None = None) -> int:
        """Loop until Ctrl-C, stop(), or max_iterations (tests). Returns fired count."""
        fired = 0
        iters = 0
        try:
            while not self._stop:
                fired += self.tick_once()
                iters += 1
                if max_iterations is not None and iters >= max_iterations:
                    break
                time.sleep(self.tick)
        except KeyboardInterrupt:
            log.info("Runner interrupted")
        return fired

    def tick_once(self, at: datetime | None = None) -> int:
        """Single poll: fire everything due. Returns alarms fired."""
        now = at or self.clock.now("UTC")
        if self._started_at is None:
            self._started_at = now  # this run owns occurrences from here on
        fired = 0
        for alarm in list(self.service.list(include_disabled=False)):
            occ = last_fire(alarm, now)
            if occ is None:
                continue
            if occ < self._started_at and not (
                alarm.snooze_until is not None and occ == alarm.snooze_until
            ):
                continue  # predates this run: no catch-up storms on (re)start
            if alarm.last_fired_at is not None and alarm.last_fired_at >= occ:
                continue  # already handled this occurrence
            if (now - occ) > MISSED_GRACE:
                if self._handle_missed(alarm.id, occ, now) == "fired":
                    fired += 1
                continue
            self._fire(alarm.id, occ, now)
            fired += 1
        return fired

    # -- internals ------------------------------------------------------
    def _handle_missed(self, alarm_id: str, fire_at: datetime, now: datetime) -> str:
        policy = self.config.missed_policy
        log.info("Missed alarm %s (due %s, now %s) policy=%s", alarm_id, fire_at, now, policy)
        if policy == "skip":
            self._mark_handled(alarm_id, now, disable_if_oneshot=True)
            return "skipped"
        if policy == "mark-missed":
            self._mark_handled(alarm_id, now, disable_if_oneshot=True)
            self.notifier.notify("Alarm missed", f"{alarm_id} due {fire_at:%H:%M} missed")
            return "marked"
        # fire-now (default)
        self._fire(alarm_id, fire_at, now)
        return "fired"

    def _mark_handled(self, alarm_id: str, now: datetime, disable_if_oneshot: bool = False) -> None:
        alarm = self.service.repo.get(alarm_id)
        if alarm is None:
            return
        alarm.last_fired_at = now
        alarm.snooze_until = None
        if disable_if_oneshot and alarm.recurrence == "ONCE":
            alarm.enabled = False
        self.service.repo.save(alarm)

    def _fire(self, alarm_id: str, fire_at: datetime, now: datetime) -> None:
        alarm = self.service.repo.get(alarm_id)
        if alarm is None or not alarm.enabled:
            return
        label = alarm.label or f"Alarm {alarm.id}"
        print(f"\n⏰ ALARM: {label} (due {fire_at:%Y-%m-%d %H:%M %Z})")
        if self.config.notifications:
            self.notifier.notify("Alarm", label)
        reason = self._ring(alarm_id, alarm.sound)
        fresh = self.service.repo.get(alarm_id)
        if fresh is None:
            return
        if reason == "snoozed":
            # User snoozed mid-ring via another terminal: keep snooze state
            # and count; this occurrence is handled.
            fresh.last_fired_at = now
        else:
            # Timeout or dismissed: the occurrence is over.
            fresh.snooze_until = None
            fresh.snooze_count = 0
            if fresh.last_fired_at is None or fresh.last_fired_at < fire_at:
                fresh.last_fired_at = now
            if fresh.recurrence == "ONCE":
                fresh.enabled = False
        self.service.repo.save(fresh)
        log.info("Fired alarm %s (ring ended: %s)", alarm_id, reason)

    def _ring(self, alarm_id: str, sound: str | None) -> str:
        """Play in ~1s passes until dismissed/snoozed/timeout.

        Re-reads the repo each pass so `alarm dismiss` / `alarm snooze`
        from another terminal (or the daemon command channel) stops the
        ringing promptly. Returns 'dismissed' | 'snoozed' | 'timeout'.
        """
        passes = max(1, int(self.config.ring_timeout_seconds))
        ring_start = self.clock.now("UTC")
        for i in range(passes):
            self.player.play(sound)
            fresh = self.service.repo.get(alarm_id)
            if fresh is None or not fresh.enabled:
                return "dismissed"
            # >= : at ring entry last_fired < occ <= ring start always holds
            # (else tick would have skipped), so any >= value was set mid-ring.
            if fresh.last_fired_at is not None and fresh.last_fired_at >= ring_start:
                return "dismissed"
            if fresh.snooze_until is not None and fresh.snooze_until > ring_start:
                return "snoozed"
            if i < passes - 1:
                self.sleep_fn(1.0)
        return "timeout"
