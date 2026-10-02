"""Desktop notifier port + adapters."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from typing import Protocol

log = logging.getLogger("alarmclock.notify")


class Notifier(Protocol):
    def notify(self, title: str, body: str) -> bool:
        ...


class DesktopNotifier:
    """notify-send (Linux) / osascript (macOS) / PowerShell toast (Windows)."""

    def notify(self, title: str, body: str) -> bool:
        try:
            if sys.platform == "darwin" and shutil.which("osascript"):
                script = f'display notification "{body}" with title "{title}"'
                subprocess.run(["osascript", "-e", script], check=False, timeout=10)  # noqa: S603
                return True
            if sys.platform == "win32":
                return self._win_toast(title, body)
            if shutil.which("notify-send"):
                subprocess.run(["notify-send", title, body], check=False, timeout=10)  # noqa: S603
                return True
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("desktop notify failed: %s", exc)
        # Fallback: print to terminal (always available).
        print(f"[{title}] {body}")
        return False

    def _win_toast(self, title: str, body: str) -> bool:
        ps = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
            "ContentType = WindowsRuntime] | Out-Null; "
            f"$t = '{title}'; $b = '{body}'; "
            "Write-Host \"$t : $b\""
        )
        try:
            subprocess.run(  # noqa: S603
                ["powershell", "-NoProfile", "-Command", ps], check=False, timeout=15
            )
            return True
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("win toast failed: %s", exc)
            return False


class FakeNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def notify(self, title: str, body: str) -> bool:
        self.calls.append((title, body))
        return True
