"""Build the machine-readable record of one run.

The console output is written for someone watching. This is written for something
that was not there when the run happened: a scheduler, a dashboard, a second
tool deciding whether it needs to do anything. That difference is why it is a
separate file and never mixed into the progress stream -- mixing them would make
both unreadable, and the console is not a log format.

The schema is versioned from the start. A report is read by programs, and a
program that has to guess whether a field means what it meant last month is a
program that silently stops working.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import STATUS_DOWNLOADED, DownloadResult, RunSummary

REPORT_SCHEMA_VERSION = 1

# Failures carry their error type so a consumer can tell a stale element
# reference from a lost extension without parsing a message. That distinction is
# the whole reason CORE-004 exists.
FAILURE_FIELDS = ("stock", "year", "quarter", "error_type", "message", "fatal")


def build_run_report(
    *,
    summary: RunSummary,
    failures: list[dict[str, Any]],
    started_at: str,
    finished_at: str,
    command: dict[str, Any],
    environment: dict[str, Any],
    staging: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the run report as a plain dictionary."""
    downloaded = [item for item in summary.results if item.status == STATUS_DOWNLOADED]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "started_at": started_at,
        "finished_at": finished_at,
        "command": command,
        "environment": environment,
        "staging": staging or {},
        "results": [
            {
                "stock": item.stock,
                "year": item.year,
                "quarter": item.quarter,
                "status": item.status,
                "href": item.href,
                "file": item.filename,
                "sha256": item.sha256,
                "bytes": item.bytes,
                "attempts": item.attempts,
            }
            for item in summary.results
        ],
        "failures": [
            {key: failure.get(key) for key in FAILURE_FIELDS} for failure in failures
        ],
        "counts": {
            "processed": len(summary.results),
            "downloaded": len(downloaded),
            "skipped": len(summary.results) - len(downloaded),
            "failed": len(failures),
            "bytes": sum(item.bytes for item in summary.results),
        },
        "exit_code": summary.exit_code,
    }


def write_run_report(report: dict[str, Any], path: Path) -> Path:
    """Write the report atomically, so a reader never sees half a document.

    A monitor polling this file would otherwise be able to catch it mid-write
    and treat a truncated JSON document as a failed run.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    temporary.replace(path)
    return path


def result_line(result: DownloadResult) -> str:
    """Return a one-line human summary of one result, for the console."""
    return f"{result.stock} {result.year} TW{result.quarter} {result.status} {result.filename}"


__all__ = [
    "FAILURE_FIELDS",
    "REPORT_SCHEMA_VERSION",
    "build_run_report",
    "result_line",
    "write_run_report",
]
