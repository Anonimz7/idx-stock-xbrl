"""Integrity verification and duplicate detection for downloaded reports.

SHA-256 is the trust anchor of the resume logic: a report is skipped only when
the recorded hash still matches the file on disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..progress import progress
from .hashing import file_sha256
from .history import (
    history_entry,
    load_download_history,
    save_download_history,
)
from .models import (
    DUPLICATE_OF_KEY,
    INTEGRITY_MISMATCH,
    INTEGRITY_STATUS_KEY,
    INTEGRITY_VERIFIED,
    SHA256_KEY,
    SIZE_KEY,
)
from .paths import final_report_path

QUARTERS = (1, 2, 3, 4)


def audit_stock_year_hashes(
    stock: str,
    year: int,
    download_dir: Path | None = None,
) -> dict[int, str]:
    """Verify every report hash of one stock/year and flag duplicate quarters.

    Entries without a stored hash are backfilled, mismatching entries are marked
    instead of being silently trusted, and identical quarters are linked through
    ``duplicate_of``.
    """
    history = load_download_history(download_dir)
    hashes: dict[int, str] = {}
    changed = False

    for quarter in QUARTERS:
        path = final_report_path(stock, year, quarter, download_dir)
        if not path.is_file() or path.stat().st_size == 0:
            continue

        digest = file_sha256(path)
        hashes[quarter] = digest
        entry = history_entry(history, stock, year, quarter)
        if entry is None:
            progress(f"HASH WARN: TW{quarter} tidak memiliki entri JSON")
            continue

        recorded_hash = str(entry.get(SHA256_KEY) or "")
        if not recorded_hash:
            entry[SHA256_KEY] = digest
            entry[SIZE_KEY] = path.stat().st_size
            entry[INTEGRITY_STATUS_KEY] = INTEGRITY_VERIFIED
            changed = True
            progress(f"HASH OK: TW{quarter}={digest}", quarter=quarter, sha256=digest)
        elif recorded_hash == digest:
            if entry.get(INTEGRITY_STATUS_KEY) != INTEGRITY_VERIFIED:
                entry[INTEGRITY_STATUS_KEY] = INTEGRITY_VERIFIED
                changed = True
            progress(f"HASH OK: TW{quarter}={digest}", quarter=quarter, sha256=digest)
        else:
            if entry.get(INTEGRITY_STATUS_KEY) != INTEGRITY_MISMATCH:
                entry[INTEGRITY_STATUS_KEY] = INTEGRITY_MISMATCH
                changed = True
            progress(
                f"HASH MISMATCH: TW{quarter}; JSON={recorded_hash}, FILE={digest}",
            )

    duplicate_pairs, duplicates_changed = _mark_duplicates(history, stock, year, hashes)
    changed = changed or duplicates_changed
    if not duplicate_pairs:
        progress("DUPLICATE HASH: tidak ada", stock=stock, year=year)

    if changed:
        path = save_download_history(history, download_dir)
        progress(f"HASH JSON OK: {path}", stock=stock, year=year)
    return hashes


def _mark_duplicates(
    history: dict[str, Any],
    stock: str,
    year: int,
    hashes: dict[int, str],
) -> tuple[list[tuple[int, int]], bool]:
    """Link quarters that share a digest and persist the result."""
    by_hash: dict[str, list[int]] = {}
    for quarter, digest in hashes.items():
        by_hash.setdefault(digest, []).append(quarter)

    changed = False
    duplicate_pairs: list[tuple[int, int]] = []
    for quarters in by_hash.values():
        ordered = sorted(quarters)
        first = ordered[0]
        for quarter in ordered:
            entry = history_entry(history, stock, year, quarter)
            if entry is None:
                continue
            duplicate_of = None if len(ordered) == 1 or quarter == first else first
            if entry.get(DUPLICATE_OF_KEY) != duplicate_of:
                entry[DUPLICATE_OF_KEY] = duplicate_of
                changed = True
            if len(ordered) > 1 and quarter != first:
                duplicate_pairs.append((first, quarter))
        if len(ordered) > 1:
            progress(
                "DUPLICATE HASH: "
                + ", ".join(f"TW{quarter}" for quarter in ordered)
                + f" ({hashes[first]})",
            )
    return duplicate_pairs, changed
