"""Shared error types and exit-code contract."""

from __future__ import annotations

EXIT_SUCCESS = 0
EXIT_GENERAL_ERROR = 1
EXIT_INVALID_USAGE = 2
EXIT_NOT_FOUND = 3
EXIT_STORAGE_ERROR = 4
EXIT_SCHEDULER_ERROR = 5


class AlarmError(Exception):
    """Base error with a stable CLI exit code."""

    exit_code: int = EXIT_GENERAL_ERROR


class InvalidInput(AlarmError):
    exit_code = EXIT_INVALID_USAGE


class AlarmNotFound(AlarmError):
    exit_code = EXIT_NOT_FOUND


class StorageError(AlarmError):
    exit_code = EXIT_STORAGE_ERROR


class SchedulerError(AlarmError):
    exit_code = EXIT_SCHEDULER_ERROR


def exit_code_for(exc: BaseException) -> int:
    if isinstance(exc, AlarmError):
        return exc.exit_code
    if isinstance(exc, (ValueError, KeyError)):
        return EXIT_INVALID_USAGE
    return EXIT_GENERAL_ERROR
