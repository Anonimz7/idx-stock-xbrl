"""Orchestration of one stock download, from detection to recorded history."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from firefox_bridge.client import FirefoxBridgeClient
from firefox_bridge.idx.browser_flow import (
    SNAPSHOT_ELEMENTS,
    prepare_stock_year,
    wait_for_detected_links,
)
from firefox_bridge.idx.link_parser import (
    find_inline_xbrl_link,
    find_inline_xbrl_links,
    quarter_from_report_href,
)
from firefox_bridge.pacing import wait_before_step
from firefox_bridge.validation import (
    ValidationError,
    validate_archive,
    validate_report_url,
)

from ..progress import notice, progress
from .errors import DownloaderError, DownloadTimeout, IntegrityError, StaleReference
from .filesystem import move_completed_download, wait_for_completed_download
from .history import history_entry, load_download_history, record_download_history
from .integrity import audit_stock_year_hashes, file_sha256
from .models import (
    SHA256_KEY,
    STATUS_DOWNLOADED,
    STATUS_SKIPPED,
    DownloadResult,
)
from .paths import (
    download_root,
    final_report_path,
    staging_relative_filename,
    staging_report_path,
)
from .planning import DownloadPlan, plan_for
from .retry import RETRY_ATTEMPTS, discard_staged, run_with_retry

LINK_RETRY_SECONDS = 3.0


def _fetch_archive(
    client: FirefoxBridgeClient,
    tab_id: str,
    stock: str,
    year: int,
    quarter: int,
    staging_relative: str,
    download_dir: Path | None,
    staged: list[Path],
) -> tuple[int, Path]:
    """Download one report into staging and return its validated size and path.

    Every step in here is safe to repeat: re-reading the page, asking Firefox to
    download again, waiting, and validating. Nothing is moved and nothing is
    recorded, so a failed attempt costs a partial file and a pause.

    The path Firefox actually wrote is appended to `staged` rather than returned.
    Firefox may not honour the requested filename, and a retry that cleaned up a
    guessed path would leave the real one behind -- which is precisely the file
    the completion check would then mistake for a finished download.
    """
    ref = _resolve_current_ref(client, tab_id, year, quarter)
    wait_before_step(f"jeda sebelum download TW{quarter}")
    progress(
        f"STEP DOWNLOAD: TW{quarter} {stock} {year} -> staging: {staging_relative}",
        stock=stock,
        year=year,
        quarter=quarter,
    )
    result = client.download(tab_id=tab_id, ref=ref, filename=staging_relative)
    if not isinstance(result, dict) or not result.get("downloaded"):
        # The download was refused rather than interrupted, so this is neither a
        # timeout nor an integrity problem: a plain non-fatal per-stock failure.
        raise DownloaderError(f"Download TW{quarter} {stock} {year} gagal dimulai: {result}")

    staging_path = _resolve_staging_path(result, stock, year, quarter, download_dir)
    staged.append(staging_path)

    wait_before_step("jeda sebelum menunggu selesai download")
    try:
        size = wait_for_completed_download(staging_path)
    except TimeoutError as error:
        raise DownloadTimeout(str(error)) from error

    # Checked here, while the file is still in staging: a truncated or non-ZIP
    # download is rejected before it is ever recorded as a report, and discarding
    # it costs nothing.
    try:
        size = validate_archive(staging_path)
    except ValidationError as error:
        raise IntegrityError(f"Arsip TW{quarter} ditolak: {error}") from error
    return size, staging_path


def _discard_all(paths: list[Path]) -> list[Path]:
    """Remove every staged path recorded so far, then forget them."""
    removed: list[Path] = []
    for path in paths:
        removed.extend(discard_staged(path))
    paths.clear()
    return removed


def _make_result(
    stock: str,
    year: int,
    quarter: int,
    href: str,
    final_path: Path,
    history: dict[str, Any],
    status: str,
    attempts: int = 1,
) -> DownloadResult:
    """Build a result, reading the recorded hash back rather than re-hashing.

    The history write has already hashed the file. Hashing it a second time just
    to fill in a report field would double the IO of every download for no gain.
    """
    entry = history_entry(history, stock, year, quarter) or {}
    return DownloadResult(
        stock=stock,
        href=href,
        filename=str(final_path),
        status=status,
        year=year,
        quarter=quarter,
        sha256=str(entry.get(SHA256_KEY) or ""),
        bytes=final_path.stat().st_size if final_path.is_file() else 0,
        attempts=attempts,
    )


def download_detected_link(
    client: FirefoxBridgeClient,
    tab_id: str,
    stock: str,
    year: int,
    link: dict[str, Any],
    download_dir: Path | None = None,
) -> DownloadResult:
    """Download one detected report, or skip it when it is already valid.

    The final file plus a matching SHA-256 in the history are the only accepted
    reasons to skip. A recorded-but-missing file and a hash mismatch both lead to
    a fresh download.
    """
    href = str(link.get("href") or "")
    quarter = quarter_from_report_href(href)
    if quarter is None:
        raise ValueError(f"Periode tidak dikenali dari URL: {href}")

    history = load_download_history(download_dir)
    final_path = final_report_path(stock, year, quarter, download_dir)
    recorded = history_entry(history, stock, year, quarter)
    integrity_failed = False

    if final_path.is_file() and final_path.stat().st_size > 0:
        current_hash = file_sha256(final_path)
        recorded_hash = str((recorded or {}).get("sha256") or "")
        if recorded_hash and recorded_hash != current_hash:
            integrity_failed = True
            progress(
                f"HASH MISMATCH: TW{quarter} gagal integritas; mengunduh ulang",
            )
        else:
            if recorded is None or recorded.get("url") != href or not recorded_hash:
                history_path = record_download_history(
                    history,
                    stock,
                    year,
                    quarter,
                    href,
                    final_path,
                    download_dir,
                )
                progress(
                    f"STEP SKIP: {stock} {year} TW{quarter} sudah ada; "
                    f"JSON diperbarui di {history_path}",
                    stock=stock,
                    year=year,
                    quarter=quarter,
                )
            else:
                progress(
                    f"STEP SKIP: {stock} {year} TW{quarter} sudah tercatat dan hash valid",
                    stock=stock,
                    year=year,
                    quarter=quarter,
                )
            return _make_result(stock, year, quarter, href, final_path, history, STATUS_SKIPPED)

    if recorded is not None and not integrity_failed:
        notice(
            f"WARN: JSON mencatat TW{quarter}, tetapi file hilang; mengunduh ulang",
            stock=stock,
            year=year,
            quarter=quarter,
        )

    # Re-checked here, not only during recognition. Between the snapshot and this
    # line the page could have been re-rendered, and this is the last point
    # before the URL is actually fetched.
    href = validate_report_url(href, stock, year, quarter)
    staging_relative = staging_relative_filename(stock, year, quarter)
    staged: list[Path] = []
    attempts = {"count": 0}

    # Only this part is retried. Moving the file and writing history come after
    # and must run exactly once: repeating a destructive step without checking
    # whether it already happened is how a report gets recorded twice.
    def _attempt() -> tuple[int, Path]:
        attempts["count"] += 1
        return _fetch_archive(
            client, tab_id, stock, year, quarter, staging_relative, download_dir, staged
        )

    size, staging_path = run_with_retry(
        _attempt,
        discard=lambda: _discard_all(staged),
        on_retry=lambda attempt, error: notice(
            f"RETRY {attempt}/{RETRY_ATTEMPTS - 1}: TW{quarter} "
            f"{type(error).__name__}: {error}",
            stock=stock,
            year=year,
            quarter=quarter,
            attempt=attempt,
        ),
    )

    wait_before_step("jeda sebelum memindahkan file")
    move_completed_download(staging_path, final_path, replace=integrity_failed)
    wait_before_step("jeda sebelum menulis JSON")
    history = load_download_history(download_dir)
    history_path = record_download_history(
        history,
        stock,
        year,
        quarter,
        href,
        final_path,
        download_dir,
    )
    progress(
        f"STEP DOWNLOAD OK: TW{quarter} dipindahkan ke {final_path} ({size} byte)",
        stock=stock,
        year=year,
        quarter=quarter,
        bytes=size,
    )
    progress(f"STEP JSON OK: {history_path}")
    return _make_result(
        stock, year, quarter, href, final_path, history, STATUS_DOWNLOADED, attempts=attempts["count"]
    )


def plan_stock_year(
    client: FirefoxBridgeClient,
    stock: str,
    year: int,
    download_dir: Path | None = None,
) -> list[DownloadPlan]:
    """Return what a real run would do for every report of one stock-year.

    Opens the page and reads the same links a real run would, then asks
    `plan_for` about each. No file is written, no history is touched, and no
    download is started -- so this is safe to run against a history you are not
    sure about, which is the only time you would want to ask.
    """
    tab_id = prepare_stock_year(client, stock, year)
    links, _ = wait_for_detected_links(client, tab_id, year)
    history = load_download_history(download_dir)
    plans: list[DownloadPlan] = []
    for link in links:
        href = str(link.get("href") or "")
        quarter = quarter_from_report_href(href)
        if quarter is None:
            continue
        plans.append(plan_for(stock, year, quarter, href, download_dir, history))
    return plans


def download_stock(
    client: FirefoxBridgeClient,
    stock: str,
    year: int,
    quarter: int,
    download_dir: Path | None = None,
) -> DownloadResult:
    """Download exactly one report of one stock and year."""
    tab_id = prepare_stock_year(client, stock, year)
    wait_before_step("jeda sebelum deteksi link")
    progress(
        f"STEP 4: Mencari link inlineXBRL.zip {year} TW{quarter} untuk {stock}",
    )
    link = _find_link_with_retry(client, tab_id, year, quarter)
    progress(
        f"STEP 4 OK: link ditemukan ref={link.get('ref')}, URL={link.get('href')}",
    )
    result = download_detected_link(
        client,
        tab_id,
        stock,
        year,
        link,
        download_dir,
    )
    audit_stock_year_hashes(stock, year, download_dir)
    return result


def download_all_detected(
    client: FirefoxBridgeClient,
    stock: str,
    year: int,
    download_dir: Path | None = None,
) -> list[DownloadResult]:
    """Download every recognized report link visible for one stock and year."""
    tab_id = prepare_stock_year(client, stock, year)
    wait_before_step("jeda sebelum deteksi seluruh link")
    progress(
        f"STEP 4: Mendeteksi seluruh link inlineXBRL.zip tahun {year} untuk {stock}",
    )
    links, _ = wait_for_detected_links(client, tab_id, year)
    progress(f"STEP 4 OK: {len(links)} link terdeteksi", stock=stock, year=year)

    results: list[DownloadResult] = []
    for link in links:
        href = str(link.get("href") or "")
        quarter = quarter_from_report_href(href)
        progress(f"STEP DETECTED: TW{quarter} {href}", stock=stock, year=year, quarter=quarter)
        results.append(
            download_detected_link(
                client,
                tab_id,
                stock,
                year,
                link,
                download_dir,
            )
        )
        wait_before_step("jeda sebelum link berikutnya")

    audit_stock_year_hashes(stock, year, download_dir)
    progress(
        f"STEP ALL OK: {len(results)} link berhasil diproses",
        stock=stock,
        year=year,
    )
    return results


def _find_link_with_retry(
    client: FirefoxBridgeClient,
    tab_id: str,
    year: int,
    quarter: int,
) -> dict[str, Any]:
    """Return one link element, retrying once while the table still renders."""
    snapshot = client.snapshot(tab_id=tab_id, max_elements=SNAPSHOT_ELEMENTS)
    link = find_inline_xbrl_link(snapshot, year, quarter)
    if link is None:
        time.sleep(LINK_RETRY_SECONDS)
        snapshot = client.snapshot(tab_id=tab_id, max_elements=SNAPSHOT_ELEMENTS)
        link = find_inline_xbrl_link(snapshot, year, quarter)
    if link is None:
        raise StaleReference(f"Link TW{quarter} {year} tidak ditemukan di halaman")
    return link


def _resolve_current_ref(
    client: FirefoxBridgeClient,
    tab_id: str,
    year: int,
    quarter: int,
) -> str:
    """Re-read the page to obtain a fresh ref before triggering a download.

    Element refs are invalidated by every navigation and by IDX's own re-render,
    so a stored ref is never trusted across steps.
    """
    snapshot = client.snapshot(tab_id=tab_id, max_elements=SNAPSHOT_ELEMENTS)
    current_link = next(
        (
            item
            for item in find_inline_xbrl_links(snapshot, year)
            if quarter_from_report_href(str(item.get("href") or "")) == quarter
        ),
        None,
    )
    if current_link is None or not current_link.get("ref"):
        raise StaleReference(
            f"Link TW{quarter} tahun {year} tidak tersedia saat ref diambil ulang"
        )
    return str(current_link["ref"])


def _resolve_staging_path(
    result: dict[str, Any],
    stock: str,
    year: int,
    quarter: int,
    download_dir: Path | None,
) -> Path:
    """Resolve the absolute staged path reported by Firefox."""
    returned_filename = str(result.get("filename") or "")
    if returned_filename:
        staging_path = Path(returned_filename)
        if staging_path.is_absolute():
            return staging_path
        return download_root(download_dir) / staging_path
    return staging_report_path(stock, year, quarter, download_dir)
