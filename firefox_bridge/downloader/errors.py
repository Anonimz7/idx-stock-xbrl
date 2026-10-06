"""Errors the downloader can explain, and whether to keep going after them.

The previous state was `RuntimeError` and `TimeoutError` everywhere, so a run
that lost the extension and a run that hit one stale element produced the same
indistinguishable message, and the run kept going through every remaining stock
to produce a wall of identical failures.

The distinction that matters is not how bad the failure is, but whether the next
stock could possibly work. `fatal` says that. Losing the extension is fatal
because nothing else will succeed either; a stale element reference is not,
because the next stock gets a fresh page.

Naming these also makes the exit code mean something. CLI-002 already promised
`3` for "bridge/extension gagal", and until now nothing could produce it.
"""

from __future__ import annotations


class DownloaderError(Exception):
    """Base for failures the downloader knows how to describe.

    `fatal` means the run cannot meaningfully continue: every subsequent action
    would fail the same way, so the caller should stop rather than repeat the
    mistake once per remaining stock.
    """

    fatal = False


class ExtensionDisconnected(DownloaderError):
    """The bridge is unreachable, or the extension stopped answering.

    Raised by the health gate before any work starts, and translated from a
    transport failure when it happens mid-run.
    """

    fatal = True

    def __init__(self, message: str = "Extension Firefox tidak terhubung ke bridge") -> None:
        super().__init__(message)


class StaleReference(DownloaderError):
    """An element reference no longer matches the page it came from.

    Expected occasionally: the IDX front end re-renders, and a ref captured
    before the re-render points at nothing. Worth naming so the message can say
    "retry" rather than leaving the reader to guess.
    """


class DownloadTimeout(DownloaderError):
    """A download did not finish inside its time budget.

    ``archive_reason`` carries the diagnosis that ended the retries: a dead
    window looks identical whether the URL 404'd, Cloudflare refused the
    request or the transfer stalled, so the instance program asks the archive
    URL itself and stops retrying on a 404. Hanging that answer here lets the
    caller report the reason the run actually stopped for instead of paying for
    a second probe. ``None`` means no answer ended it -- the default, and what
    the page-flow program, which has no tab to ask, always sees.
    """

    archive_reason: str | None = None


class IntegrityError(DownloaderError):
    """A file failed validation: not a readable archive, or a hash that disagrees."""


__all__ = [
    "DownloadTimeout",
    "DownloaderError",
    "ExtensionDisconnected",
    "IntegrityError",
    "StaleReference",
]
