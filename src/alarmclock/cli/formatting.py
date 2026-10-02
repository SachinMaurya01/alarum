"""Output formatting: styled tables on TTY, plain when piped."""

from __future__ import annotations

import os
import sys

USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _c(code: str, text: str) -> str:
    if not USE_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


def bold(text: str) -> str:
    return _c("1", text)


def green(text: str) -> str:
    return _c("32", text)


def dim(text: str) -> str:
    return _c("2", text)


def format_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    out = [bold(line)]
    for row in rows:
        out.append("  ".join(cell.ljust(w) for cell, w in zip(row, widths)))
    return "\n".join(out)
