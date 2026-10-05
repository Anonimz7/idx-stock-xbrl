"""Direct-download program for IDX audited ``instance.zip`` archives.

A sibling of :mod:`firefox_bridge.downloader`, not a replacement for it. The
existing program *finds* links by driving the Laporan Keuangan panel, because
which periods a page offers is only knowable from the page. This one *builds* the
URL for the audited annual archive, which is fixed for a given stock and year, so
it skips the profile page, the panel, the year dropdown and every element ref.

What it deliberately does not reimplement: skip/resume, staging, archive
validation, retry, hashing and history. Those are passed straight through from
:mod:`firefox_bridge.downloader` with a different download root, so the two
programs cannot drift apart on the rule that decides whether a report needs
fetching at all.

The download root differs on purpose. History is keyed by
``(stock, year, quarter)`` with no column for which archive it describes, so a
shared root would have the two programs overwriting each other's hashes and
re-downloading forever. See :mod:`firefox_bridge.instance.paths`.
"""

from __future__ import annotations

from .orchestrator import download_instance
from .paths import (
    INSTANCE_SUBFOLDER,
    instance_download_dir,
    instance_final_path,
    instance_relative_path,
    instance_report_filename,
    instance_staging_path,
    instance_staging_relative_filename,
    instance_staging_root,
)
from .session import IDX_URL, IdxSession, is_clear_page
from .urls import (
    AUDITED_QUARTER,
    AUDITED_SEGMENT,
    IDX_DOWNLOAD_BASE,
    INSTANCE_FILENAME,
    instance_url,
    validate_instance_url,
)

__all__ = [
    "AUDITED_QUARTER",
    "AUDITED_SEGMENT",
    "IDX_DOWNLOAD_BASE",
    "INSTANCE_FILENAME",
    "INSTANCE_SUBFOLDER",
    "download_instance",
    "instance_download_dir",
    "instance_final_path",
    "instance_relative_path",
    "instance_report_filename",
    "instance_staging_path",
    "instance_staging_relative_filename",
    "instance_staging_root",
    "IdxSession",
    "IDX_URL",
    "is_clear_page",
    "instance_url",
    "validate_instance_url",
]
