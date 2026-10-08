"""Where the instance program writes -- a download root of its own.

The page-driven flow and this one must never share a root. The history file is
keyed by ``(stock, year, quarter)`` and carries no column for which archive it
describes, so two programs writing the same key would each record a hash the
other cannot reproduce: whichever ran last would leave the first looking at a
mismatch and re-downloading, forever. That is exactly the duplicate the resume
mechanism exists to prevent, so the two roots are kept apart outright.

Under ``<root>/instance`` this module reuses every path helper unchanged -- they
all take an explicit ``download_dir``, which is what makes the separation free.

Two roots, deliberately. Archives and history live under ``<root>/instance``;
staging does not, because ``browser.downloads.download`` resolves the filename it
is given against **Firefox's own download directory**, which this process does not
control. Measuring staging from the instance root would aim the completion check
at a folder Firefox never writes to, and ``scan_staging`` would then "confirm"
that staging is clean while a partial file sits untouched elsewhere -- the exact
failure ``downloader/staging.py`` exists to prevent. So staging is resolved from
the root the rest of the system already assumes Firefox writes to.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..downloader.paths import SAHAM_FOLDER, STAGING_FOLDER, download_root
from ..validation import normalize_stock_code
from .urls import AUDITED_QUARTER

# Sits under the ordinary download root so both programs are found in one place,
# while still keeping histories, staging folders and archives disjoint.
INSTANCE_SUBFOLDER = "instance"


def instance_download_dir(explicit: Path | str | None = None) -> Path:
    """Return the concrete download root for this program, never ``None``.

    Always resolved up front, and then passed down as a real path: every helper
    downstream treats ``None`` as "fall back to the default root", which is the
    page flow's folder. Resolving once here makes that fallback unreachable.
    """
    if explicit is not None:
        return Path(explicit)
    override = os.environ.get("FIREFOX_BRIDGE_INSTANCE_DIR")
    if override:
        return Path(override).expanduser()
    # Under the ordinary download root so both programs are found in one place,
    # while histories, staging folders and archives stay disjoint.
    return download_root() / INSTANCE_SUBFOLDER


def instance_report_filename(stock: str, year: int) -> str:
    """Return the anti-duplicate final filename for one instance archive.

    Deliberately not ``report_filename`` from the page flow: that names the file
    ``inlineXBRL``, and this archive is not one. The quarter is kept in the name
    even though it is always the audited period, so the shape stays parallel to
    the other program's ``<STOCK>_<asset>_T<quarter>_<year>.zip``.
    """
    code = normalize_stock_code(stock)
    return f"{code}_instance_T{AUDITED_QUARTER}_{year}.zip"


def instance_relative_path(stock: str, year: int) -> str:
    """Return the history-friendly path of one report, relative to the root."""
    code = normalize_stock_code(stock)
    return f"{SAHAM_FOLDER}/{year}/{code}/{instance_report_filename(code, year)}"


def instance_final_path(
    stock: str,
    year: int,
    download_dir: Path | str | None = None,
) -> Path:
    """Return the absolute final path of one instance archive."""
    root = instance_download_dir(download_dir)
    return root / Path(instance_relative_path(stock, year))


def instance_staging_relative_filename(stock: str, year: int) -> str:
    """Return the staging path relative to **Firefox's** download root.

    This is the string handed to ``browser.downloads.download``, which resolves it
    against the browser's own download directory rather than against this
    program's root -- so it must be written from that root's point of view, or the
    two halves of the pipeline would disagree about where the file lands.
    """
    code = normalize_stock_code(stock)
    filename = instance_report_filename(code, year)
    return f"{SAHAM_FOLDER}/{STAGING_FOLDER}/{year}/{code}/{filename}"


def instance_staging_root() -> Path:
    """Return the folder Firefox actually writes staged downloads into.

    Resolved from ``download_root()``, not from ``instance_download_dir()``:
    Firefox places ``filename`` under its own download directory, and the rest of
    the system already assumes that directory equals ``download_root()``. Both
    programs therefore stage in the same folder, which is unavoidable -- and
    harmless, because the two use different filenames and ``scan_staging`` skips
    anything with a ``.crdownload`` or ``.part`` sibling still being written.
    """
    return download_root() / SAHAM_FOLDER / STAGING_FOLDER


def instance_staging_path(
    stock: str,
    year: int,
) -> Path:
    """Return the absolute staging path of one instance archive.

    Takes no ``download_dir``: staging is anchored to where Firefox writes, which
    the caller cannot change by pointing this program's root elsewhere. The final
    path is the one that moves -- :func:`instance_final_path` -- not the staging
    one.
    """
    code = normalize_stock_code(stock)
    return instance_staging_root() / str(year) / code / instance_report_filename(code, year)


__all__ = [
    "INSTANCE_SUBFOLDER",
    "instance_download_dir",
    "instance_final_path",
    "instance_relative_path",
    "instance_report_filename",
    "instance_staging_path",
    "instance_staging_relative_filename",
    "instance_staging_root",
]
