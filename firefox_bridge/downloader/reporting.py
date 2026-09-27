"""Human readable run reporting."""

from __future__ import annotations

from ..progress import notice, progress
from .models import RunSummary


def print_run_summary(summary: RunSummary) -> None:
    """Print the end-of-run summary block.

    The failure lines go out at `WARNING` even though the run itself is over and
    nothing is left to act on. "Did anything go wrong" should be answerable by
    filtering one level, and a failure that logs at `INFO` is a failure nobody
    filters for.
    """
    progress("\nSummary:")
    progress(f"  Successful: {summary.successful}", kind="summary")
    progress(f"  Failed:     {summary.failed}", kind="summary")
    for failure in summary.failures:
        notice(f"  - {failure}", kind="summary")
    for result in summary.results:
        progress(f"  + {result.stock}: {result.filename}", kind="summary", stock=result.stock)
