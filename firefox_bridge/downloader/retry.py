"""Retry only what is worth retrying, and never on top of a broken file.

Two rules, and the second is the one that matters.

**Only transient failures are retried.** A stale element reference, a download
that ran out of time, and an archive that failed validation are all things that
can go differently on the next attempt. A rejected stock code or an off-host URL
is not: retrying it just spends the same wall-clock time to reach the same
answer. Losing the extension is not either, and the CLI already stops on it.

Some failures are transient-looking only from the inside: a download that
wrote nothing is what a 404, a lapsed Cloudflare clearance and a stalled
handshake all look like, and only the first of the three is worth retrying.
:class:`run_with_retry`'s ``give_up`` is where the caller resolves that
ambiguity by asking, and then stops on the answer instead of paying for the
remaining attempts to reach it a second time.

**A retry must not inherit the previous attempt's wreckage.** This is not
theory. Measured earlier: a file nobody is writing satisfies "the size held
steady across two samples", so the completion check reports it as finished.
Retrying on top of such a file means the second attempt races a partial file
that is the right size to fool the check again. So every retry discards the
staged file first. Without that, retrying would be strictly worse than not
retrying -- it is how a corrupt archive gets recorded.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from .errors import DownloadTimeout, IntegrityError, StaleReference

RETRY_ATTEMPTS = 3
BASE_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 30.0

# Deliberately narrow. `DownloaderError` itself is excluded, so an unclassified
# failure fails once and loudly rather than three times quietly.
RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    StaleReference,
    DownloadTimeout,
    IntegrityError,
)

_T = TypeVar("_T")


def backoff_seconds(attempt: int, base: float = BASE_BACKOFF_SECONDS) -> float:
    """Exponential backoff, capped.

    `attempt` is 1-based: the pause *after* the first failure. Growing the gap
    matters here because a re-render or a busy Firefox usually needs a moment,
    and hammering it immediately tends to produce the same failure again.
    """
    if attempt < 1:
        raise ValueError(f"attempt harus >= 1, bukan {attempt}")
    return min(base * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS)


def is_retryable(error: Exception) -> bool:
    return isinstance(error, RETRYABLE_ERRORS)


def discard_staged(path: Path) -> list[Path]:
    """Remove a staged file and any Firefox temp sibling.

    Returns what was removed, so the caller can say so. Called before every
    retry: this is the idempotency guard, and without it a retry is a way to end
    up with a corrupt archive rather than a way to avoid one.
    """
    removed: list[Path] = []
    for candidate in (path, *(Path(f"{path}{suffix}") for suffix in (".crdownload", ".part"))):
        if candidate.exists():
            candidate.unlink(missing_ok=True)
            removed.append(candidate)
    return removed


def run_with_retry(
    operation: Callable[[], _T],
    *,
    attempts: int = RETRY_ATTEMPTS,
    discard: Callable[[], list[Path]] | None = None,
    on_retry: Callable[[int, Exception], None] | None = None,
    give_up: Callable[[Exception], bool] | None = None,
) -> _T:
    """Run `operation`, retrying only the failures worth retrying.

    `discard` runs before each retry and is the guard described in the module
    docstring. `on_retry` reports the attempt, so the caller can name the stock
    and quarter instead of this module guessing.

    `give_up` asks a question `is_retryable` cannot: not "could this go
    differently?" but "has the answer already come back?". It runs after a
    retryable failure that still has attempts left, and answering True ends the
    loop immediately -- the caller has asked the outside world and been told
    the archive is not there, so the remaining attempts would buy the same
    answer at the same price. Checked before `discard` and `on_retry`, because
    when it says yes there is no next attempt for either to prepare for.
    Left unset, the loop behaves exactly as it did without it.
    """
    if attempts < 1:
        raise ValueError(f"attempts harus >= 1, bukan {attempts}")

    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except RETRYABLE_ERRORS as error:
            last = error
            if attempt == attempts:
                break
            if give_up is not None and give_up(error):
                break
            if discard is not None:
                discard()
            if on_retry is not None:
                on_retry(attempt, error)
            time.sleep(backoff_seconds(attempt))

    assert last is not None  # noqa: S101 - only reachable after a caught failure
    raise last


__all__ = [
    "BASE_BACKOFF_SECONDS",
    "MAX_BACKOFF_SECONDS",
    "RETRYABLE_ERRORS",
    "RETRY_ATTEMPTS",
    "backoff_seconds",
    "discard_staged",
    "is_retryable",
    "run_with_retry",
]
