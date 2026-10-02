"""Backward-compatible entry: `python main.py` delegates to the CLI."""

from alarmclock.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
