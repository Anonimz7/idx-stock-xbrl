"""One call, two audiences: the terminal and the log file.

The downloader already narrates itself on stdout with ``print``. That narration
is useful to a person watching a run and useless afterwards, because it leaves
nothing behind that can be filtered: a failed quarter is only findable by
scrolling back through console output, or worse, by not scrolling at all.

Routing those lines through the ordinary logger instead would fix the file but
ruin the terminal, because the bridge logger has a console handler that would
print each line a second time. So this module keeps both jobs apart: the plain
line still goes to stdout exactly as before, and the same line is also emitted
as a JSON record carrying the fields that make it searchable.
"""

from __future__ import annotations

import sys
from typing import Any

from .logging_config import get_progress_logger, safe_extra


def progress(message: str, **fields: Any) -> None:
    """Print `message` to stdout and record it in the log file.

    `fields` become top-level keys on the JSON record, so a later run can be
    narrowed to one stock, one year, or one step without regexing the message
    text.
    """
    print(message, flush=True)
    get_progress_logger().info(message, extra=safe_extra(fields))


def notice(message: str, **fields: Any) -> None:
    """Report a recoverable oddity: stdout as normal, `WARNING` in the file.

    For the lines that say something went wrong but did not stop the run -- a
    hash that changed, a JSON entry pointing at a file that is gone. They belong
    on stdout because the run continues, and at `WARNING` because "did anything
    go wrong" should be answerable by level without reading the messages.
    """
    print(message, flush=True)
    get_progress_logger().warning(message, extra=safe_extra(fields))


def problem(message: str, **fields: Any) -> None:
    """Report a failure: stderr for the operator, `WARNING` in the file.

    Kept separate from `progress` because a failure is worth a level above
    `INFO` -- the first question about any log is "did anything go wrong", and
    that should be answerable without reading the messages.
    """
    print(message, file=sys.stderr, flush=True)
    get_progress_logger().warning(message, extra=safe_extra(fields))


def detail(message: str, **fields: Any) -> None:
    """Record a line in the file without echoing it to the terminal.

    For facts that are only worth having after the fact: sizes, hashes, resolved
    paths. The run stays readable, and the evidence survives it.
    """
    get_progress_logger().info(message, extra=safe_extra(fields))


__all__ = ["detail", "notice", "problem", "progress"]
