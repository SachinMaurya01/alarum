"""Audio player port + backends with fallback chain.

Chain: preferred backend -> system player -> terminal bell.
No `shell=True`; sound paths validated.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Protocol

log = logging.getLogger("alarmclock.audio")


class Player(Protocol):
    def play(self, sound: str | None) -> bool:
        """Play once (or loop handled by caller). Returns True if audible."""
        ...


def _valid_sound_path(sound: str) -> Path | None:
    p = Path(sound).expanduser()
    try:
        resolved = p.resolve()
    except OSError:
        return None
    if not resolved.is_file():
        return None
    return resolved


class SystemPlayer:
    """Best-effort OS player. Returns False when nothing playable."""

    def play(self, sound: str | None) -> bool:
        if sound:
            path = _valid_sound_path(sound)
            if path is None:
                log.warning("Sound file not found: %s", sound)
            else:
                if self._play_file(path):
                    return True
        return self._play_beep_fallback(sound)

    def _play_file(self, path: Path) -> bool:
        cmds: list[list[str]] = []
        if sys.platform == "darwin":
            cmds.append(["afplay", str(path)])
        elif sys.platform == "win32":
            # winsound is stdlib; use it via powershell-free path below.
            try:
                import winsound  # type: ignore[import-not-found]

                winsound.PlaySound(
                    str(path), winsound.SND_FILENAME | winsound.SND_ASYNC
                )
                return True
            except OSError as exc:
                log.debug("winsound failed: %s", exc)
        else:
            for bin_name in ("paplay", "aplay", "mpv", "play"):
                if shutil.which(bin_name):
                    cmds.append([bin_name, str(path)])
                    break
        for cmd in cmds:
            try:
                subprocess.run(cmd, check=False, timeout=30)
                return True
            except (OSError, subprocess.SubprocessError) as exc:
                log.debug("player %s failed: %s", cmd[0], exc)
        return False

    def _play_beep_fallback(self, sound: str | None) -> bool:
        # Terminal bell always "works" (may be silent if terminal mutes it).
        sys.stdout.write("\a")
        sys.stdout.flush()
        return True


class FakePlayer:
    """Test double recording play calls."""

    def __init__(self) -> None:
        self.calls: list[str | None] = []

    def play(self, sound: str | None) -> bool:
        self.calls.append(sound)
        return True
