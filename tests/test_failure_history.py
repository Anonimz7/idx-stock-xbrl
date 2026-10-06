"""Failure entries in the download history JSON.

A failed download is persisted next to the successes with its diagnosed
reason, so debugging a run never relies on memory. A failure entry carries
no file keys on purpose: the resume logic skips a report only when the final
file exists on disk with a matching hash.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from firefox_bridge.downloader.history import (
    empty_history,
    history_entry,
    load_download_history,
    record_download_history,
    record_failure_history,
)
from firefox_bridge.downloader.history import stock_year_complete
from firefox_bridge.downloader.models import DownloadRecord


def _failure(history: dict[str, Any], tmp_path: Path, **kw: Any) -> dict[str, Any]:
    params = {
        "stock": "ARKA", "year": 2025, "quarter": 4,
        "href": "https://www.idx.co.id/Audit/2025/Audit/ARKA/instance.zip",
        "error_type": "DownloadTimeout", "reason": "404 Not Found",
        "download_dir": tmp_path,
    }
    params.update(kw)
    record_failure_history(history, **params)
    entry = history_entry(history, "ARKA", 2025, 4)
    assert isinstance(entry, dict)
    return entry


def test_failure_entry_has_reason_and_no_file_keys(tmp_path: Path) -> None:
    hist = empty_history()
    entry = _failure(hist, tmp_path)
    assert entry["status"] == "failed"
    assert entry["error_type"] == "DownloadTimeout"
    assert entry["reason"] == "404 Not Found"
    assert entry["fail_count"] == 1
    assert entry["failed_at"]
    assert "file" not in entry
    assert "sha256" not in entry
    assert "size" not in entry


def test_failure_entry_survives_json_roundtrip(tmp_path: Path) -> None:
    hist = empty_history()
    _failure(hist, tmp_path)
    reloaded = load_download_history(tmp_path)
    entry = history_entry(reloaded, "ARKA", 2025, 4)
    assert isinstance(entry, dict)
    assert entry["status"] == "failed"
    assert entry["reason"] == "404 Not Found"


def test_repeat_failure_increments_fail_count(tmp_path: Path) -> None:
    hist = empty_history()
    _failure(hist, tmp_path)
    entry = _failure(hist, tmp_path, reason="Cloudflare challenge")
    assert entry["fail_count"] == 2
    assert entry["reason"] == "Cloudflare challenge"


def test_failure_entry_is_not_complete_so_it_retries(tmp_path: Path) -> None:
    hist = empty_history()
    _failure(hist, tmp_path)
    assert stock_year_complete(hist, "ARKA", 2025, tmp_path) is False


def test_typed_record_tolerates_failure_entry(tmp_path: Path) -> None:
    hist = empty_history()
    _failure(hist, tmp_path)
    record = DownloadRecord.from_entry(history_entry(hist, "ARKA", 2025, 4))  # type: ignore[arg-type]
    assert record.sha256 == ""
    assert not record.has_hash


def test_later_success_overwrites_failure_entry(tmp_path: Path) -> None:
    hist = empty_history()
    _failure(hist, tmp_path)
    target = tmp_path / "ARKA_instance_T4_2025.zip"
    target.write_bytes(b"PK\x03\x04" + b"x" * 100)
    record_download_history(
        hist, "ARKA", 2025, 4,
        "https://www.idx.co.id/Audit/2025/Audit/ARKA/instance.zip",
        target, tmp_path,
    )
    entry = history_entry(hist, "ARKA", 2025, 4)
    assert isinstance(entry, dict)
    assert entry.get("status") != "failed"
    assert entry["sha256"]
