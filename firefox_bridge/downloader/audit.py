"""Audit the history JSON against what is actually on disk.

The history is what makes a run resumable: a report is skipped only when the
JSON says it was downloaded *and* the file is still there with a matching hash.
That is a two-sided agreement, and either side can drift:

* A file is deleted, or truncated by something outside this program.
* The history JSON is lost or corrupted and has to be reconstructed.
* A run is killed between moving a file and writing its entry -- the archive
  exists, the record does not, and the next run has no idea it already happened.

That last case is why this reports **orphans** as well as missing files. Looking
only for problems the history knows about would miss exactly the files a crashed
run left behind.

`verify` never writes. `rebuild` reconstructs the history from the filesystem,
which recovers the archive and its hash but **not** the URL it came from -- the
filename encodes stock, quarter, and year, but not the IDX path. A rebuilt entry
says so rather than inventing a plausible-looking URL, and the next real run
fills it in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..validation import ValidationError, normalize_stock_code, validate_archive
from .hashing import file_sha256
from .history import HISTORY_VERSION, empty_history, relative_report_path
from .models import (
    COMPLETED_AT_KEY,
    DUPLICATE_OF_KEY,
    FILE_KEY,
    INTEGRITY_STATUS_KEY,
    INTEGRITY_VERIFIED,
    SHA256_KEY,
    SIZE_KEY,
    URL_KEY,
)
from .paths import SAHAM_FOLDER, download_root

STATUS_OK = "ok"
STATUS_MISSING = "missing"
STATUS_MISMATCH = "mismatch"
STATUS_CORRUPT = "corrupt"
STATUS_UNREADABLE = "unreadable"
STATUS_ORPHAN = "orphan"
STATUS_RECOVERED = "recovered"

# `NCKL_inlineXBRL_T4_2025.zip`
_FILENAME = re.compile(r"^(?P<stock>[A-Za-z0-9]+)_inlineXBRL_T(?P<quarter>[1-4])_(?P<year>\d{4})\.zip$")

# Statuses that mean "this needs a human to look at it".
PROBLEM_STATUSES = frozenset(
    {STATUS_MISSING, STATUS_MISMATCH, STATUS_CORRUPT, STATUS_UNREADABLE}
)


@dataclass(frozen=True, slots=True)
class Finding:
    """One row of the audit."""

    stock: str
    year: int
    quarter: int
    status: str
    detail: str = ""
    expected_sha256: str = ""
    actual_sha256: str = ""
    path: str = ""

    @property
    def is_problem(self) -> bool:
        return self.status in PROBLEM_STATUSES

    @property
    def is_orphan(self) -> bool:
        return self.status == STATUS_ORPHAN


@dataclass(frozen=True, slots=True)
class AuditReport:
    """The whole audit, in a form both the console and a caller can read."""

    findings: tuple[Finding, ...] = ()

    def by_status(self, status: str) -> tuple[Finding, ...]:
        return tuple(item for item in self.findings if item.status == status)

    @property
    def ok(self) -> tuple[Finding, ...]:
        return self.by_status(STATUS_OK)

    @property
    def orphans(self) -> tuple[Finding, ...]:
        return self.by_status(STATUS_ORPHAN)

    @property
    def problems(self) -> tuple[Finding, ...]:
        return tuple(item for item in self.findings if item.is_problem)

    @property
    def healthy(self) -> bool:
        """True when every recorded file is present and matches.

        Orphans are deliberately not counted: an unrecorded archive is something
        to adopt, not something wrong with the archive.
        """
        return not self.problems


def _entry_path(entry: dict[str, Any], download_dir: Path | None) -> Path | None:
    raw = str(entry.get(FILE_KEY) or "")
    if not raw:
        return None
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else download_root(download_dir) / candidate


def _parse_filename(path: Path) -> tuple[str, int, int] | None:
    match = _FILENAME.match(path.name)
    if match is None:
        return None
    try:
        stock = normalize_stock_code(match.group("stock"))
    except ValidationError:
        # A file whose name is not a stock code cannot be filed under one, and
        # guessing would write a record for a report that may not exist.
        return None
    return stock, int(match.group("year")), int(match.group("quarter"))


def _iter_report_files(
    download_dir: Path | None,
    *,
    year: int,
) -> list[tuple[Path, str, int, int]]:
    """Return every recognisable report archive for one year under `saham/`.

    Scoped by year because the history it is compared against is per year. A
    2025 verify that saw 2024's archives would report all of them as orphans --
    files on disk that no history knows about -- which is the exact opposite of
    the truth.
    """
    found: list[tuple[Path, str, int, int]] = []
    root = download_root(download_dir) / SAHAM_FOLDER
    if not root.is_dir():
        return found
    for path in sorted(root.rglob("*.zip")):
        parsed = _parse_filename(path)
        if parsed is None or parsed[1] != year:
            continue
        found.append((path, *parsed))
    return found


def verify_history(
    history: dict[str, Any],
    download_dir: Path | None = None,
    *,
    year: int,
    find_orphans: bool = True,
) -> AuditReport:
    """Compare every history entry for one year with the file it points at.

    Read-only by construction: it opens files to hash them and nothing else.
    """
    findings: list[Finding] = []
    downloads = history.get("downloads") or {}
    seen: set[str] = set()

    for stock in sorted(downloads):
        years = downloads[stock]
        if not isinstance(years, dict):
            continue
        for year in sorted(years):
            quarters = years[year]
            if not isinstance(quarters, dict):
                continue
            for quarter in sorted(quarters):
                entry = quarters[quarter]
                if not isinstance(entry, dict):
                    continue
                try:
                    year_number = int(year)
                    quarter_number = int(quarter)
                except ValueError:
                    continue

                path = _entry_path(entry, download_dir)
                expected = str(entry.get(SHA256_KEY) or "")
                if path is None:
                    findings.append(
                        Finding(
                            stock, year_number, quarter_number, STATUS_UNREADABLE,
                            "entri tidak menyebut file",
                        )
                    )
                    continue

                relative = relative_report_path(path, download_dir)
                seen.add(relative.lower())

                if not path.is_file():
                    findings.append(
                        Finding(
                            stock, year_number, quarter_number, STATUS_MISSING,
                            "file tidak ada di disk", expected_sha256=expected, path=relative,
                        )
                    )
                    continue

                actual = file_sha256(path)
                if expected and actual != expected:
                    findings.append(
                        Finding(
                            stock, year_number, quarter_number, STATUS_MISMATCH,
                            "hash tidak cocok dengan JSON", expected_sha256=expected,
                            actual_sha256=actual, path=relative,
                        )
                    )
                    continue

                # The hash matched, so the bytes are the ones that were recorded.
                # A readable archive is still worth confirming, because a hash is
                # only as meaningful as the thing it was taken from.
                try:
                    validate_archive(path)
                except ValidationError as error:
                    findings.append(
                        Finding(
                            stock, year_number, quarter_number, STATUS_CORRUPT,
                            str(error), expected_sha256=expected,
                            actual_sha256=actual, path=relative,
                        )
                    )
                    continue

                findings.append(
                    Finding(
                        stock, year_number, quarter_number, STATUS_OK,
                        expected_sha256=expected, actual_sha256=actual,
                        path=relative,
                    )
                )

    if find_orphans:
        for path, stock, found_year, quarter in _iter_report_files(
            download_dir, year=year
        ):
            relative = relative_report_path(path, download_dir)
            if relative.lower() in seen:
                continue
            # A file with no history entry: a run killed between moving the
            # archive and writing its record. Re-downloading it would be waste.
            findings.append(
                Finding(
                    stock, found_year, quarter, STATUS_ORPHAN,
                    "file ada tapi tidak tercatat di JSON",
                    actual_sha256=file_sha256(path), path=relative,
                )
            )

    return AuditReport(findings=tuple(findings))


def rebuild_history(
    download_dir: Path | None = None,
    existing: dict[str, Any] | None = None,
    *,
    year: int,
) -> tuple[dict[str, Any], AuditReport]:
    """Reconstruct the history from the archives on disk.

    The filename carries stock, quarter, and year; the size and hash are
    recomputed. The source URL is **not** recoverable from any of that, so where
    `existing` does not already know it the URL is left null with
    `url_recovered: false` rather than filled with a plausible guess. The next
    real download of that report fills the real value in.

    `existing` matters: without it, running this to repair one missing entry
    would throw away every URL already recorded. Rebuild is a repair, so it
    keeps what is already known and only fills the gaps.

    Only archives of `year` are picked up, so a repair of 2024 cannot silently
    rebuild a history document that claims to be 2025's.
    """
    previous = existing or empty_history()
    previous_downloads = previous.get("downloads") or {}
    history = empty_history()
    downloads: dict[str, Any] = history["downloads"]
    findings: list[Finding] = []
    stamp = datetime.now(UTC).isoformat()

    for path, stock, found_year, quarter in _iter_report_files(
        download_dir, year=year
    ):
        relative = relative_report_path(path, download_dir)
        try:
            validate_archive(path)
        except ValidationError as error:
            findings.append(
                Finding(
                    stock, found_year, quarter, STATUS_CORRUPT, str(error), path=relative,
                )
            )
            continue

        digest = file_sha256(path)
        known = _existing_entry(previous_downloads, stock, found_year, quarter)
        kept_url = str(known.get(URL_KEY) or "") if known else ""
        recovered = bool(kept_url)

        downloads.setdefault(stock, {}).setdefault(str(found_year), {})[
            str(quarter)
        ] = {
            URL_KEY: kept_url or None,
            FILE_KEY: relative,
            SIZE_KEY: path.stat().st_size,
            SHA256_KEY: digest,
            DUPLICATE_OF_KEY: None,
            INTEGRITY_STATUS_KEY: INTEGRITY_VERIFIED,
            COMPLETED_AT_KEY: (
                str(known.get(COMPLETED_AT_KEY) or "") if known else stamp
            )
            or stamp,
            "url_recovered": recovered,
        }
        findings.append(
            Finding(
                stock, year, quarter, STATUS_RECOVERED,
                (
                    "entri dipulihkan, URL dipertahankan dari JSON lama"
                    if recovered
                    else "URL tidak dapat direkonstruksi dari nama file"
                ),
                actual_sha256=digest, path=relative,
            )
        )

    history["version"] = HISTORY_VERSION
    return history, AuditReport(findings=tuple(findings))


def _existing_entry(
    downloads: dict[str, Any], stock: str, year: int, quarter: int
) -> dict[str, Any] | None:
    years = downloads.get(stock)
    if not isinstance(years, dict):
        return None
    quarters = years.get(str(year))
    if not isinstance(quarters, dict):
        return None
    entry = quarters.get(str(quarter))
    return entry if isinstance(entry, dict) else None


__all__ = [
    "PROBLEM_STATUSES",
    "STATUS_CORRUPT",
    "STATUS_MISMATCH",
    "STATUS_MISSING",
    "STATUS_OK",
    "STATUS_ORPHAN",
    "STATUS_RECOVERED",
    "STATUS_UNREADABLE",
    "AuditReport",
    "Finding",
    "rebuild_history",
    "verify_history",
]
