"""Scheduler port: foreground, daemon and OS-scheduler backends."""

from __future__ import annotations

from typing import Protocol


class Scheduler(Protocol):
    def run(self) -> int:
        """Run until stopped. Returns alarms fired."""
        ...

    def stop(self) -> None: ...
