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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from firefox_bridge.client import FirefoxBridgeClient, FirefoxBridgeClientError
from firefox_bridge.downloader.audit import rebuild_history, verify_history
from firefox_bridge.downloader.errors import DownloaderError, ExtensionDisconnected
from firefox_bridge.downloader.health import EXTENSION_UNAVAILABLE_STATUS, ensure_extension_ready
from firefox_bridge.downloader.history import load_download_history, save_download_history
from firefox_bridge.downloader.models import DownloadResult, RunSummary
from firefox_bridge.downloader.orchestrator import download_all_detected, download_stock
from firefox_bridge.downloader.paths import download_history_path, download_root
from firefox_bridge.downloader.reporting import print_run_summary
from firefox_bridge.downloader.run_report import build_run_report, write_run_report
from firefox_bridge.downloader.staging import (
    StagingScan,
    describe,
    scan_staging,
    staging_root,
)
from firefox_bridge.idx.link_parser import quarter_from_report_href
from firefox_bridge.pacing import minimum_one_second
from firefox_bridge.progress import detail, notice, problem, progress
from firefox_bridge.stocksource import StockListError, read_stock_list
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
    parser.add_argument(
        "--stocks-file",
        type=str,
        default=None,
        help=(
            "Read stock codes from a .sql dump or a .csv/.txt table, excluding rows "
            "whose delisted flag is set. Merged with --stocks when both are given."
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
        "--report",
        type=str,
        default=None,
        help=(
            "Write a machine-readable JSON report of the run to this path. "
            "Never mixed into the progress output, so it can be read by a "
            "scheduler or another tool while the run is still going."
        ),
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


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _command_block(args: argparse.Namespace, stock_codes: list[str]) -> dict[str, Any]:
    """Record what was asked for, so a report is self-describing.

    A report that says what succeeded but not what was requested cannot answer
    "was this supposed to include BBCA?".
    """
    return {
        "stocks": stock_codes,
        "year": args.year,
        "quarter": args.quarter,
        "all_detected": bool(args.all_detected or args.all_quarters),
        "delay": args.delay,
        "mode": "history" if args.history else "download",
    }


def _staging_block(scan: StagingScan | None) -> dict[str, Any]:
    if scan is None:
        return {}
    return {
        "examined": scan.examined,
        "removed": len(scan.removed),
        "in_flight": len(scan.kept_in_flight),
        "empty_dirs_removed": len(scan.empty_dirs_removed),
    }


def _write_report(
    path: str,
    *,
    summary: RunSummary,
    failures: list[dict[str, Any]],
    started_at: str,
    command: dict[str, Any],
    environment: dict[str, Any],
    staging: dict[str, Any],
) -> None:
    """Write the run report, and say so, but never let it break the run.

    A report that cannot be written is worth a warning; it is not worth losing a
    completed set of downloads over.
    """
    report = build_run_report(
        summary=summary,
        failures=failures,
        started_at=started_at,
        finished_at=_utc_now(),
        command=command,
        environment=environment,
        staging=staging,
    )
    try:
        written = write_run_report(report, Path(path))
    except OSError as error:
        problem(f"  -> laporan tidak bisa ditulis ke {path}: {error}")
        return
    detail(f"laporan run ditulis: {written}", report=path)


def run(argv: Sequence[str] | None = None) -> int:
    """Execute one CLI run and return its exit code."""
    args = build_parser().parse_args(argv)

    if args.history is not None:
        return _run_history_command(args.history, args.download_dir)

    if args.stocks is None and args.stocks_file is None:
        problem(
            "No stock codes supplied. Gunakan --stocks, --stocks-file, "
            "atau --history verify/rebuild."
        )
        return EXIT_INVALID_INPUT

    try:
        stock_codes = parse_stock_codes(args.stocks) if args.stocks else []
    except argparse.ArgumentTypeError as error:
        problem(f"Stock code tidak valid: {error}")
        return EXIT_INVALID_INPUT

    if args.stocks_file is not None:
        try:
            stock_list = read_stock_list(Path(args.stocks_file))
        except StockListError as error:
            problem(f"Daftar saham tidak bisa dibaca: {error}")
            return EXIT_INVALID_INPUT
        progress(
            f"STEP 0: daftar saham dari {args.stocks_file}: "
            f"{len(stock_list.codes)} aktif, {stock_list.skipped_delisted} delisted dilewati",
            file=args.stocks_file,
            active=len(stock_list.codes),
            delisted=stock_list.skipped_delisted,
        )
        for code in stock_list.codes:
            if code not in stock_codes:
                stock_codes.append(code)

    if not stock_codes:
        problem("Tidak ada kode saham aktif setelah dibaca dari file")
        return EXIT_INVALID_INPUT

    all_detected = args.all_detected or args.all_quarters
    download_dir = Path(args.download_dir) if args.download_dir else None
    client = FirefoxBridgeClient()
    summary = RunSummary()
    failures: list[dict[str, Any]] = []
    started_at = _utc_now()
    version = ""
    scan: StagingScan | None = None

    def _finish(code: int) -> int:
        """Write the report on every exit path, including the early ones.

        A report that is missing exactly when a run failed is the one case
        anybody would want it for.
        """
        if args.report is not None:
            _write_report(
                args.report,
                summary=summary,
                failures=failures,
                started_at=started_at,
                command=_command_block(args, stock_codes),
                environment={
                    "extension_version": version,
                    "download_dir": str(download_root(download_dir)),
                },
                staging=_staging_block(scan),
            )
        return code

    # Before a single paced browser step: if the extension is not connected, every
    # one of those steps fails identically and the user is left reading a timeout
    # instead of the one fact that matters.
    try:
        version = ensure_extension_ready(client)
    except ExtensionDisconnected as error:
        problem(f"PRAJAMAL: {error}")
        problem("  -> nyalakan bridge, lalu tekan Connect di popup extension")
        return _finish(EXIT_BRIDGE_UNAVAILABLE)
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
            failures.append(
                {
                    "stock": stock,
                    "year": args.year,
                    "quarter": args.quarter if not all_detected else None,
                    "error_type": kind,
                    "message": str(failure),
                    "fatal": failure.fatal,
                }
            )
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
        return _finish(EXIT_BRIDGE_UNAVAILABLE)
    return _finish(summary.exit_code)


def main(argv: Sequence[str] | None = None) -> int:
    """Console script wrapper."""
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
