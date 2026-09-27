"""Download domain layer: paths, history, integrity, and orchestration."""

from __future__ import annotations

from .filesystem import move_completed_download, wait_for_completed_download
from .hashing import file_sha256, hash_matches_record
from .history import (
    history_entry,
    history_record,
    load_download_history,
    record_download_history,
    save_download_history,
)
from .integrity import audit_stock_year_hashes
from .models import DownloadRecord, DownloadResult, RunSummary
from .orchestrator import download_all_detected, download_detected_link, download_stock
from .paths import (
    download_exists,
    download_history_path,
    download_root,
    final_report_path,
    resolve_output_path,
    staging_relative_filename,
    staging_report_path,
)
from .reporting import print_run_summary

__all__ = [
    "DownloadRecord",
    "DownloadResult",
    "RunSummary",
    "audit_stock_year_hashes",
    "download_all_detected",
    "download_detected_link",
    "download_exists",
    "download_history_path",
    "download_root",
    "download_stock",
    "file_sha256",
    "final_report_path",
    "hash_matches_record",
    "history_entry",
    "history_record",
    "load_download_history",
    "move_completed_download",
    "print_run_summary",
    "record_download_history",
    "resolve_output_path",
    "save_download_history",
    "staging_relative_filename",
    "staging_report_path",
    "wait_for_completed_download",
]
