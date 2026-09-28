"""Command line entry point for the IDX report downloader.

Run from a source checkout::

    .venv\\Scripts\\python.exe -m firefox_bridge.cli \\
        --stocks "NCKL,BBCA" --year 2025 --all-detected

After installation the same run is available as ``firefox-bridge-download``.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import httpx

from firefox_bridge.client import FirefoxBridgeClient, FirefoxBridgeClientError
from firefox_bridge.downloader.audit import rebuild_history, verify_history
from firefox_bridge.downloader.errors import DownloaderError, ExtensionDisconnected
from firefox_bridge.downloader.health import EXTENSION_UNAVAILABLE_STATUS, ensure_extension_ready
from firefox_bridge.downloader.history import load_download_history, save_download_history
from firefox_bridge.downloader.models import DownloadResult, RunSummary
from firefox_bridge.downloader.orchestrator import download_all_detected, download_stock
from firefox_bridge.downloader.paths import download_history_path
from firefox_bridge.downloader.reporting import print_run_summary
from firefox_bridge.downloader.staging import describe, scan_staging, staging_root
from firefox_bridge.idx.link_parser import quarter_from_report_href
from firefox_bridge.pacing import minimum_one_second
from firefox_bridge.progress import notice, problem, progress
from firefox_bridge.validation import ValidationError, normalize_stock_code

DEFAULT_STOCK_DELAY_SECONDS = 3.0
EXIT_SUCCESS = 0
EXIT_FAILURES = 1
EXIT_INVALID_INPUT = 2
# Promised by CLI-002 and, until now, unreachable: nothing could produce it.
EXIT_BRIDGE_UNAVAILABLE = 3


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the downloader CLI."""
    parser = argparse.ArgumentParser(
        prog="firefox-bridge-download",
        description="Bulk download IDX inlineXBRL.zip reports",
    )
    parser.add_argument(
        "--stocks",
        default=None,
        help="Comma-separated stock codes (e.g. NCKL,BBCA). Required unless --history is used.",
    )
    parser.add_argument(
        "--history",
        choices=("verify", "rebuild"),
        default=None,
        help=(
            "Audit instead of download: 'verify' checks the history against the files "
            "on disk and writes nothing; 'rebuild' reconstructs the history from the "
            "archives that exist."
        ),
    )
    parser.add_argument("--year", type=int, default=2025, help="Reporting year")
    parser.add_argument(
        "--quarter",
        type=int,
        default=4,
        choices=[1, 2, 3, 4],
        help="Reporting quarter (1-4). Ignored when --all-detected is set",
    )
    parser.add_argument(
        "--all-quarters",
        action="store_true",
        help="Legacy alias for --all-detected",
    )
    parser.add_argument(
        "--all-detected",
        action="store_true",
        help="Download every recognized inlineXBRL.zip link detected for the year",
    )
    parser.add_argument(
        "--delay",
        type=minimum_one_second,
        default=DEFAULT_STOCK_DELAY_SECONDS,
        help="Delay between stocks in seconds (minimum 1)",
    )
    parser.add_argument(
        "--download-dir",
        type=str,
        default=None,
        help=(
            "Root directory for downloaded reports "
            "(default: $FIREFOX_BRIDGE_DOWNLOAD_DIR or ~/Downloads). "
            "Files are saved under <dir>/saham/<STOCK>/<YEAR>/."
        ),
    )
    return parser


def parse_stock_codes(raw: str) -> list[str]:
    """Return normalized, de-duplicated stock codes preserving input order.

    Each code becomes a folder name under `saham/`, so validation happens here
    rather than at the point of use: the cheapest moment to reject `../` is
    before a browser tab is opened, with an exit code the caller can tell apart
    from a download failure.
    """
    codes: list[str] = []
    for item in raw.split(","):
        if not item.strip():
            continue
        try:
            code = normalize_stock_code(item)
        except ValidationError as error:
            raise argparse.ArgumentTypeError(str(error)) from error
        if code not in codes:
            codes.append(code)
    return codes


def _report_processed(
    result: DownloadResult,
    year: int,
    all_detected: bool,
) -> None:
    quarter = quarter_from_report_href(result.href)
    label = "Detected" if all_detected else "Downloaded"
    progress(
        f"{label} {result.stock} {year} TW{quarter}: {result.filename}",
        stock=result.stock,
        year=year,
        quarter=quarter,
    )


def _run_history_command(mode: str, download_dir_arg: str | None) -> int:
    """Run `history verify` or `history rebuild` and return its exit code.

    Both are offline: no browser, no bridge, no pacing. `verify` is read-only by
    construction, which is what makes it safe to run against a history you are
    unsure of -- it is the tool you reach for *because* you do not trust the
    state.
    """
    download_dir = Path(download_dir_arg) if download_dir_arg else None

    if mode == "rebuild":
        # Pass the current history so a repair keeps the URLs it already knows
        # instead of throwing them away.
        existing = load_download_history(download_dir) if download_history_path(
            download_dir
        ).exists() else None
        history, report = rebuild_history(download_dir, existing)
        for finding in report.findings:
            progress(
                f"  {finding.stock} {finding.year} TW{finding.quarter}: "
                f"{finding.detail} ({finding.path})",
                stock=finding.stock,
                year=finding.year,
                quarter=finding.quarter,
                status=finding.status,
            )
        if report.problems:
            problem(
                f"rebuild dilewati: {len(report.problems)} arsip tidak valid, "
                "history tidak ditulis"
            )
            return EXIT_FAILURES
        path = save_download_history(history, download_dir)
        progress(
            f"HISTORY REBUILT: {len(report.findings)} entri -> {path}",
            entries=len(report.findings),
        )
        unrecovered = [
            item for item in report.findings if "dipertahankan" not in item.detail
        ]
        if unrecovered:
            notice(
                f"{len(unrecovered)} entri tanpa URL; run berikutnya akan melengkapinya",
                unrecovered=len(unrecovered),
            )
        return EXIT_SUCCESS

    history = load_download_history(download_dir)
    report = verify_history(history, download_dir)
    for finding in report.findings:
        line = f"  {finding.status.upper():<9} {finding.stock} {finding.year} TW{finding.quarter}"
        if finding.is_problem:
            problem(f"{line}: {finding.detail}", stock=finding.stock, status=finding.status)
        else:
            progress(f"{line}: {finding.path}", stock=finding.stock, status=finding.status)

    progress(
        f"HISTORY VERIFY: {len(report.ok)} ok, {len(report.problems)} bermasalah, "
        f"{len(report.orphans)} tanpa catatan",
        ok=len(report.ok),
        problems=len(report.problems),
        orphans=len(report.orphans),
    )
    if report.orphans:
        notice(
            f"  {len(report.orphans)} file ada tapi belum tercatat; "
            "jalankan --history rebuild untuk mencatatnya",
            orphans=len(report.orphans),
        )
    return EXIT_FAILURES if report.problems else EXIT_SUCCESS


def _as_downloader_error(error: Exception) -> DownloaderError:
    """Translate a failure into the taxonomy, or pass an existing member through.

    A transport failure and a page that changed under us arrive here as
    unrelated exception types. To the person reading the output they are the same
    event -- the bridge or the extension went away -- and it is fatal, so it has
    to become `ExtensionDisconnected` before anything decides whether to continue.
    """
    if isinstance(error, DownloaderError):
        return error

    if isinstance(error, httpx.ConnectError):
        return ExtensionDisconnected(f"Bridge tidak menjawab: {error.__class__.__name__}")

    if isinstance(error, FirefoxBridgeClientError):
        status = getattr(error, "status_code", None)
        if status == EXTENSION_UNAVAILABLE_STATUS:
            return ExtensionDisconnected("Extension berhenti menjawab (HTTP 503)")
        return DownloaderError(f"Bridge menolak perintah (HTTP {status}): {error}")

    return DownloaderError(str(error) or type(error).__name__)


def run(argv: Sequence[str] | None = None) -> int:
    """Execute one CLI run and return its exit code."""
    args = build_parser().parse_args(argv)

    if args.history is not None:
        return _run_history_command(args.history, args.download_dir)

    if args.stocks is None:
        problem("No stock codes supplied. Gunakan --stocks, atau --history verify/rebuild.")
        return EXIT_INVALID_INPUT

    try:
        stock_codes = parse_stock_codes(args.stocks)
    except argparse.ArgumentTypeError as error:
        problem(f"Stock code tidak valid: {error}")
        return EXIT_INVALID_INPUT
    if not stock_codes:
        problem("No stock codes supplied")
        return EXIT_INVALID_INPUT

    all_detected = args.all_detected or args.all_quarters
    download_dir = Path(args.download_dir) if args.download_dir else None
    client = FirefoxBridgeClient()
    summary = RunSummary()

    # Before a single paced browser step: if the extension is not connected, every
    # one of those steps fails identically and the user is left reading a timeout
    # instead of the one fact that matters.
    try:
        version = ensure_extension_ready(client)
    except ExtensionDisconnected as error:
        problem(f"PRAJAMAL: {error}")
        problem("  -> nyalakan bridge, lalu tekan Connect di popup extension")
        return EXIT_BRIDGE_UNAVAILABLE
    progress(f"STEP 0.5: extension {version or 'terhubung'} (siap)", extension=version)

    # Before anything else: a run killed mid-download leaves a partial file at
    # the exact path this run is about to download to, and the completion check
    # would read that leftover as a finished file. Cleared up front, every
    # download starts from a state we know.
    scan = scan_staging(staging_root(download_dir))
    progress(
        f"STEP 0: {describe(scan)}",
        examined=scan.examined,
        removed=len(scan.removed),
        in_flight=len(scan.kept_in_flight),
    )
    for path in scan.removed:
        progress(f"  - dibuang: {path.name}", reason="stale staging")
    for path in scan.kept_in_flight:
        notice(f"  - dipertahankan (sedang diunduh): {path.name}", reason="in-flight")

    for stock in stock_codes:
        try:
            if all_detected:
                results = download_all_detected(
                    client,
                    stock,
                    args.year,
                    download_dir,
                )
                summary.results.extend(results)
                for result in results:
                    _report_processed(result, args.year, all_detected=True)
            else:
                result = download_stock(
                    client,
                    stock,
                    args.year,
                    args.quarter,
                    download_dir,
                )
                summary.results.append(result)
                _report_processed(result, args.year, all_detected=False)
        except Exception as error:  # noqa: BLE001
            failure = _as_downloader_error(error)
            kind = type(failure).__name__
            # The type goes in the message, not only in the log field: a wall of
            # failures is read on the console, and "which of these can I retry"
            # should not require cross-referencing the JSON log.
            summary.failures.append(f"{stock}: [{kind}] {failure}")
            problem(
                f"FAILED {stock} {args.year} [{kind}]: {failure}",
                stock=stock,
                year=args.year,
                error_type=kind,
            )
            if failure.fatal:
                # Nothing after this would work either. Say so once and stop,
                # rather than repeating the same failure for every stock left.
                problem(f"  -> run dihentikan: {failure}")
                summary.fatal_error = str(failure)
                time.sleep(args.delay)
                break
        time.sleep(args.delay)

    # Again at the end. The opening scan keeps yesterday's wreckage from being
    # mistaken for a fresh download, but it leaves behind the empty year folders
    # this run just emptied -- and a batch of hundreds of stocks would leave
    # hundreds of directories nobody will ever look at.
    leftovers = scan_staging(staging_root(download_dir))
    if leftovers.empty_dirs_removed or leftovers.removed:
        progress(
            f"STEP END: {describe(leftovers)}",
            removed=len(leftovers.removed),
            in_flight=len(leftovers.kept_in_flight),
        )

    print_run_summary(summary)
    if summary.fatal_error:
        return EXIT_BRIDGE_UNAVAILABLE
    return summary.exit_code


def main(argv: Sequence[str] | None = None) -> int:
    """Console script wrapper."""
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
