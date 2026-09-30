"""JSON history of completed report downloads.

The history is the resume mechanism: a run skips a report only when the JSON
entry exists *and* the final file is still on disk with a matching hash.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .hashing import file_sha256
from .models import (
    COMPLETED_AT_KEY,
    DUPLICATE_OF_KEY,
    FILE_KEY,
    INTEGRITY_STATUS_KEY,
    INTEGRITY_VERIFIED,
    SHA256_KEY,
    SIZE_KEY,
    URL_KEY,
    DownloadRecord,
)
from .paths import download_history_path, download_root

HISTORY_VERSION = 1


def empty_history() -> dict[str, Any]:
    """Return a fresh, empty history document."""
    return {"version": HISTORY_VERSION, "downloads": {}}


def load_download_history(download_dir: Path | None = None) -> dict[str, Any]:
    """Load the history JSON, returning an empty document when absent."""
    path = download_history_path(download_dir)
    if not path.exists():
        return empty_history()
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("downloads"), dict):
        raise ValueError(f"Format JSON riwayat tidak valid: {path}")
    return data


def save_download_history(
    history: dict[str, Any],
    download_dir: Path | None = None,
) -> Path:
    """Write the history atomically so a crash cannot truncate it."""
    path = download_history_path(download_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(history, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    temporary.replace(path)
    return path


def relative_report_path(local_path: Path, download_dir: Path | None = None) -> str:
    """Return the history-friendly path of a local archive."""
    root = download_root(download_dir).resolve()
    try:
        return local_path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(local_path.resolve())


def history_entry(
    history: dict[str, Any],
    stock: str,
    year: int,
    quarter: int,
) -> dict[str, Any] | None:
    """Return the raw JSON entry for one report, or None when absent."""
    stock_data = history["downloads"].get(stock.upper())
    if not isinstance(stock_data, dict):
        return None
    year_data = stock_data.get(str(year))
    if not isinstance(year_data, dict):
        return None
    entry = year_data.get(str(quarter))
    return entry if isinstance(entry, dict) else None


def history_record(
    history: dict[str, Any],
    stock: str,
    year: int,
    quarter: int,
) -> DownloadRecord | None:
    """Return the typed history record for one report, or None when absent."""
    entry = history_entry(history, stock, year, quarter)
    return None if entry is None else DownloadRecord.from_entry(entry)


def record_download_history(
    history: dict[str, Any],
    stock: str,
    year: int,
    quarter: int,
    href: str,
    local_path: Path,
    download_dir: Path | None = None,
) -> Path:
    """Persist a completed download, including its SHA-256, and save the JSON."""
    downloads = history["downloads"]
    stock_data = downloads.setdefault(stock.upper(), {})
    year_data = stock_data.setdefault(str(year), {})
    year_data[str(quarter)] = {
        URL_KEY: href,
        FILE_KEY: relative_report_path(local_path, download_dir),
        SIZE_KEY: local_path.stat().st_size,
        SHA256_KEY: file_sha256(local_path),
        DUPLICATE_OF_KEY: None,
        INTEGRITY_STATUS_KEY: INTEGRITY_VERIFIED,
        COMPLETED_AT_KEY: datetime.now(UTC).isoformat(),
    }
    return save_download_history(history, download_dir)


def stock_year_complete(
    history: dict[str, Any],
    stock: str,
    year: int,
    download_dir: Path | None = None,
) -> bool:
    """Return True when every recorded quarter for one stock/year is valid.

    A quarter counts as valid only when its history entry exists, the final
    file is still on disk and non-empty, and the SHA-256 matches the recorded
    one. At least one quarter must be recorded; a stock with no history at all
    is never "complete", so its page still gets visited (and retried).
    """
    stock_data = history["downloads"].get(stock.upper())
    if not isinstance(stock_data, dict):
        return False
    year_data = stock_data.get(str(year))
    if not isinstance(year_data, dict) or not year_data:
        return False
    root = download_root(download_dir).resolve()
    for entry in year_data.values():
        if not isinstance(entry, dict):
            return False
        relative = entry.get(FILE_KEY)
        if not relative:
            return False
        final_path = root / str(relative)
        try:
            if not (final_path.is_file() and final_path.stat().st_size > 0):
                return False
        except OSError:
            return False
        recorded_hash = str(entry.get(SHA256_KEY) or "")
        if recorded_hash and file_sha256(final_path) != recorded_hash:
            return False
    return True
