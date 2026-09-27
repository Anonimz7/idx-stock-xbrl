"""Backward compatible entry point for the IDX report downloader.

The implementation now lives in focused modules:

- :mod:`firefox_bridge.idx` for page semantics and browser flow;
- :mod:`firefox_bridge.downloader` for paths, history, integrity, and orchestration;
- :mod:`firefox_bridge.cli` for the command line interface.

This module only re-exports the public names so existing commands and scripts
keep working unchanged.
"""

from __future__ import annotations

import sys

from firefox_bridge.cli import main
from firefox_bridge.downloader.filesystem import (
    move_completed_download,
    wait_for_completed_download,
)
from firefox_bridge.downloader.hashing import file_sha256
from firefox_bridge.downloader.history import (
    history_entry,
    load_download_history,
    record_download_history,
    save_download_history,
)
from firefox_bridge.downloader.integrity import audit_stock_year_hashes
from firefox_bridge.downloader.models import DownloadResult
from firefox_bridge.downloader.orchestrator import (
    download_all_detected,
    download_detected_link,
    download_stock,
)
from firefox_bridge.downloader.paths import (
    download_exists,
    download_history_path,
    resolve_output_path,
    staging_relative_filename,
)
from firefox_bridge.idx.browser_flow import (
    PROFILE_PATH_PREFIX,
    PROFILE_URL,
    ensure_open,
    find_reusable_profile_tab,
    open_laporan_keuangan,
    prepare_stock_year,
    select_year_dropdown,
    wait_for_detected_links,
    wait_for_load,
)
from firefox_bridge.idx.link_parser import (
    find_inline_xbrl_link,
    find_inline_xbrl_links,
    quarter_from_report_href,
)
from firefox_bridge.idx.selectors import (
    find_dropdown_option_ref,
    find_laporan_keuangan_ref,
    find_year_dropdown_ref,
)
from firefox_bridge.pacing import STEP_DELAY_SECONDS, minimum_one_second, wait_before_step

__all__ = [
    "DownloadResult",
    "PROFILE_PATH_PREFIX",
    "PROFILE_URL",
    "STEP_DELAY_SECONDS",
    "audit_stock_year_hashes",
    "download_all_detected",
    "download_detected_link",
    "download_exists",
    "download_history_path",
    "download_stock",
    "ensure_open",
    "file_sha256",
    "find_dropdown_option_ref",
    "find_inline_xbrl_link",
    "find_inline_xbrl_links",
    "find_laporan_keuangan_ref",
    "find_reusable_profile_tab",
    "find_year_dropdown_ref",
    "history_entry",
    "load_download_history",
    "main",
    "minimum_one_second",
    "move_completed_download",
    "open_laporan_keuangan",
    "prepare_stock_year",
    "quarter_from_report_href",
    "record_download_history",
    "resolve_output_path",
    "save_download_history",
    "select_year_dropdown",
    "staging_relative_filename",
    "wait_before_step",
    "wait_for_completed_download",
    "wait_for_detected_links",
    "wait_for_load",
]


if __name__ == "__main__":
    sys.exit(main())
