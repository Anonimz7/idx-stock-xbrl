"""Centralized logging configuration for the Firefox bridge.

Provides a named logger ``firefox_bridge`` with:
- A console handler (stderr) mirroring the FastAPI/uvicorn default.
- A JSON-lines file handler so request traces and extension traffic can be
  inspected without polluting the uvicorn access log.

The token is intentionally never logged.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOGGER_NAME = "firefox_bridge"
PROGRESS_LOGGER_NAME = "firefox_bridge.progress"

# The attributes `logging` puts on every record itself. Taken from a real
# LogRecord rather than hand-listed, so it cannot drift as Python adds fields.
_RESERVED_FIELDS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__  # type: ignore[arg-type]
)


def _log_dir() -> Path:
    """Where the JSON-lines log is written.

    The project folder is the right home here: the venv, the tests, and the
    README already live there, and `.gitignore` already excludes `*.log`, so the
    log sits next to the thing that produced it. There is no EXE or installer in
    scope, so there is no second distribution mode that would need somewhere
    else to write.
    """
    override = os.environ.get("FIREFOX_BRIDGE_LOG_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "logs"


def _log_file_path() -> Path:
    path = _log_dir() / "bridge.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def safe_extra(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Drop keys that would collide with the attributes `logging` sets itself.

    Passing one of those through `extra` raises `KeyError` from inside the
    logging call, so a field innocently named `filename` or `module` would take
    down the very run that was trying to describe itself. Losing one key is a
    fair price for that.
    """
    return {
        key: value
        for key, value in fields.items()
        if key not in _RESERVED_FIELDS and not key.startswith("_")
    }


def _jsonable(value: Any) -> Any:
    """Coerce a field to something `json.dumps` will accept.

    `logging` swallows a serialization error and drops the record with a
    `--- Logging error ---` block on stderr, so a single `Path` in a field would
    make that line disappear from the log -- silently, which is the one outcome
    a log exists to prevent. A readable string is worth more than a lost line.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class JsonLineFormatter(logging.Formatter):
    """Emit compact JSON lines suitable for grep/filter tooling."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        payload: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key not in _RESERVED_FIELDS and not key.startswith("_"):
                payload[key] = _jsonable(value)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


_HANDLER_MARKER = "_firefox_bridge_handler"


def _is_ours(handler: logging.Handler) -> bool:
    """Whether `handler` is one this module installed.

    "Does the logger have any handlers" is the wrong test for "is this logger
    configured". Anything embedding the bridge -- a test runner, a host
    application -- may attach its own handler, and then the file log would be
    skipped in silence. The marker answers the question that was actually asked.
    """
    return getattr(handler, _HANDLER_MARKER, False) is True


def _own_handlers(logger: logging.Logger) -> list[logging.Handler]:
    return [handler for handler in logger.handlers if _is_ours(handler)]


def reset_logging() -> None:
    """Detach this module's handlers, leaving anyone else's in place."""
    for name in (LOGGER_NAME, PROGRESS_LOGGER_NAME):
        logger = logging.getLogger(name)
        for handler in _own_handlers(logger):
            handler.close()
            logger.removeHandler(handler)


def _file_handler(logger: logging.Logger) -> logging.Handler | None:
    """Build the JSON-lines handler, or explain why there will not be one.

    A bridge that refuses to start because a log directory is unwritable helps
    nobody: the console still carries the message, and the file log is a
    convenience rather than the product.
    """
    try:
        handler = logging.FileHandler(_log_file_path(), encoding="utf-8")
    except OSError as error:
        logger.warning("Log file unavailable at %s (%s); continuing on console only", _log_dir(), error)
        return None
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(JsonLineFormatter())
    setattr(handler, _HANDLER_MARKER, True)
    return handler


def configure_logging(level: int | None = None) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    # Only detach our own handlers. Removing every handler would reach into
    # whatever application is hosting us and unhook its logging too.
    for handler in _own_handlers(logger):
        handler.close()
        logger.removeHandler(handler)

    if level is None:
        env_level = os.environ.get("FIREFOX_BRIDGE_LOG", "INFO").upper()
        level = getattr(logging, env_level, logging.INFO)

    logger.setLevel(level)

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(level)
    console.addFilter(_ConsoleIsNotForProgress())
    console.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%F %T")
    )
    setattr(console, _HANDLER_MARKER, True)
    logger.addHandler(console)

    file_handler = _file_handler(logger)
    if file_handler is not None:
        logger.addHandler(file_handler)

    logger.propagate = False
    return logger


def get_logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if not _own_handlers(logger):
        configure_logging()
    return logger


class _ConsoleIsNotForProgress(logging.Filter):
    """Drop progress records from the console, leaving the file handler alone.

    The downloader prints its own plain line to stdout. If the record also
    reached the console handler it would appear twice, once plain and once in
    the verbose `asctime LEVEL name:` shape.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name != PROGRESS_LOGGER_NAME


def get_progress_logger() -> logging.Logger:
    """File-only logger for lines the CLI has already printed itself.

    The downloader writes a plain readable line for whoever is watching the
    terminal. Routing that same line through the normal logger would print it a
    second time. This child logger propagates to the bridge logger, so it reuses
    the one open file handle; the console handler filters these records out, so
    stdout keeps its plain format while the file gains something worth
    filtering.
    """
    logger = logging.getLogger(PROGRESS_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    get_logger()  # ensure the parent has its handlers
    return logger
