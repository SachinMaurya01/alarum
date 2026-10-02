"""Structured logging setup (stdlib only; structlog deferred)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path


def setup_logging(verbose: bool = False, debug: bool = False, log_file: Path | None = None) -> logging.Logger:
    level = logging.DEBUG if debug else (logging.INFO if verbose else logging.WARNING)
    logger = logging.getLogger("alarmclock")
    logger.setLevel(level)
    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        sh.setLevel(level)
        logger.addHandler(sh)
        if log_file is not None:
            from logging.handlers import RotatingFileHandler

            fh = RotatingFileHandler(str(log_file), maxBytes=512_000, backupCount=3)
            fh.setFormatter(fmt)
            fh.setLevel(level)
            logger.addHandler(fh)
    return logger
