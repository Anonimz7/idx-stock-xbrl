"""Typed models for the download domain."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

INTEGRITY_VERIFIED = "verified"
INTEGRITY_MISMATCH = "mismatch"

# How a report ended up in its final folder. `downloaded` covers both a first
# fetch and a repair after a missing or corrupt file; `attempts` and the run log
# say which happened.
STATUS_DOWNLOADED = "downloaded"
STATUS_SKIPPED = "skipped"
STATUS_PROCESSED = "processed"

SHA256_KEY = "sha256"
SIZE_KEY = "size"
DUPLICATE_OF_KEY = "duplicate_of"
INTEGRITY_STATUS_KEY = "integrity_status"
COMPLETED_AT_KEY = "completed_at"
URL_KEY = "url"
FILE_KEY = "file"


@dataclass(slots=True)
class DownloadResult:
    """Outcome of processing one detected report link.

    The reporting fields carry what a machine-readable run report needs: whether
    the archive was fetched or already there, how many attempts it took, and the
    hash that was recorded. A consumer should not have to re-read the history
    JSON or re-hash a file to learn any of this.
    """

    stock: str
    href: str
    filename: str
    status: str = STATUS_PROCESSED
    year: int = 0
    quarter: int = 0
    sha256: str = ""
    bytes: int = 0
    attempts: int = 1

    @property
    def downloaded(self) -> bool:
        return self.status == STATUS_DOWNLOADED

    @property
    def skipped(self) -> bool:
        return self.status == STATUS_SKIPPED


@dataclass(frozen=True, slots=True)
class DownloadRecord:
    """One persisted history entry for a report on disk."""

    url: str
    file: str
    size: int
    sha256: str
    duplicate_of: int | None
    integrity_status: str
    completed_at: str

    @classmethod
    def from_entry(cls, entry: Mapping[str, Any]) -> DownloadRecord:
        duplicate_of = entry.get("duplicate_of")
        return cls(
            url=str(entry.get("url") or ""),
            file=str(entry.get("file") or ""),
            size=int(entry.get("size") or 0),
            sha256=str(entry.get("sha256") or ""),
            duplicate_of=None if duplicate_of is None else int(duplicate_of),
            integrity_status=str(entry.get("integrity_status") or ""),
            completed_at=str(entry.get("completed_at") or ""),
        )

    @property
    def has_hash(self) -> bool:
        return bool(self.sha256)


@dataclass(slots=True)
class RunSummary:
    """Aggregated outcome of one CLI run."""

    results: list[DownloadResult] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    # Set when a failure means the run could not meaningfully continue, e.g. the
    # extension disappeared. Recorded separately from `failures` because the
    # exit code has to distinguish "some stocks failed" from "the environment is
    # gone", and a caller cannot tell those apart from a list of strings.
    fatal_error: str | None = None

    @property
    def successful(self) -> int:
        return len(self.results)

    @property
    def failed(self) -> int:
        return len(self.failures)

    @property
    def exit_code(self) -> int:
        return 1 if self.failures else 0
