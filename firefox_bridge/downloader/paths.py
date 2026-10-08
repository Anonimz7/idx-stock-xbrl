"""Filesystem layout for staged and final report archives.

Firefox writes into a staging folder first, Python then moves the completed
archive to its final home. The staging folder name deliberately avoids a
leading dot because Firefox rejects it with
``filename must not contain illegal characters``.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..validation import normalize_stock_code

SAHAM_FOLDER = "saham"
STAGING_FOLDER = "staging"
# Riwayat dipecah per tahun pelaporan: satu tahun, satu berkas. Sebuah run
# untuk 2024 menulis `download_history_2024.json` dan tidak pernah menyentuh
# catatan 2025 -- itu gunanya dipisah, tahun yang sudah selesai dibekukan.
HISTORY_STEM = "download_history"
DOWNLOAD_DIR_ENV = "FIREFOX_BRIDGE_DOWNLOAD_DIR"


def history_filename(year: int) -> str:
    """Return the history filename for one reporting year."""
    return f"{HISTORY_STEM}_{year}.json"


def default_download_dir() -> Path:
    """Return the download root from the environment or the user profile."""
    env_dir = os.environ.get(DOWNLOAD_DIR_ENV)
    if env_dir:
        return Path(env_dir).expanduser()
    if os.name == "nt":
        return Path(os.environ.get("USERPROFILE", Path.home())) / "Downloads"
    return Path.home() / "Downloads"


def download_root(download_dir: Path | None = None) -> Path:
    """Return the effective download root."""
    return download_dir if download_dir is not None else default_download_dir()


def report_filename(stock: str, year: int, quarter: int) -> str:
    """Return the anti-duplicate final filename for one report."""
    return f"{stock.upper()}_inlineXBRL_T{quarter}_{year}.zip"


def resolve_output_path(
    stock: str,
    year: int,
    quarter: int,
    download_dir: Path | None = None,
) -> tuple[Path, str]:
    """Build the destination folder and relative filename for a report.

    Returns ``(folder_path, relative_filename)`` where ``folder_path`` is the
    final directory ``<root>/saham/<YEAR>/<STOCK>/`` and ``relative_filename`` is
    relative to ``<root>`` as stored in the history JSON.
    """
    code = normalize_stock_code(stock)
    folder = download_root(download_dir) / SAHAM_FOLDER / str(year) / code
    folder.mkdir(parents=True, exist_ok=True)
    filename = report_filename(code, year, quarter)
    relative_filename = f"{SAHAM_FOLDER}/{year}/{code}/{filename}"
    return folder, relative_filename


def final_report_path(
    stock: str,
    year: int,
    quarter: int,
    download_dir: Path | None = None,
) -> Path:
    """Return the absolute final path of one report."""
    folder, relative_filename = resolve_output_path(stock, year, quarter, download_dir)
    return folder / Path(relative_filename).name


def staging_relative_filename(stock: str, year: int, quarter: int) -> str:
    """Return the Firefox-relative staging path for one report."""
    code = normalize_stock_code(stock)
    filename = report_filename(code, year, quarter)
    return f"{SAHAM_FOLDER}/{STAGING_FOLDER}/{year}/{code}/{filename}"


def staging_report_path(
    stock: str,
    year: int,
    quarter: int,
    download_dir: Path | None = None,
) -> Path:
    """Return the absolute staging path for one report."""
    return download_root(download_dir) / staging_relative_filename(stock, year, quarter)


def download_history_path(download_dir: Path | None = None, *, year: int) -> Path:
    """Return the absolute path of the history JSON file for one year.

    `year` is keyword-only and has no default on purpose: a caller that has
    forgotten which year it is working on must fail at the type checker
    instead of silently reading some other year's record.
    """
    return download_root(download_dir) / SAHAM_FOLDER / history_filename(year)


def saham_folder(download_dir: Path | None = None) -> Path:
    """Return the folder holding every downloaded report.

    Exposed so a command can say "there is nothing here" instead of reporting
    zero problems, which reads exactly the same and is the opposite of true.
    """
    return download_root(download_dir) / SAHAM_FOLDER


def download_exists(
    stock: str,
    year: int,
    quarter: int,
    download_dir: Path | None = None,
) -> bool:
    """Return True when the final report file exists and is not empty."""
    path = final_report_path(stock, year, quarter, download_dir)
    return path.is_file() and path.stat().st_size > 0
