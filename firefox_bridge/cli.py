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

from firefox_bridge.client import FirefoxBridgeClient
from firefox_bridge.downloader.models import DownloadResult, RunSummary
from firefox_bridge.downloader.orchestrator import download_all_detected, download_stock
from firefox_bridge.downloader.reporting import print_run_summary
from firefox_bridge.idx.link_parser import quarter_from_report_href
from firefox_bridge.pacing import minimum_one_second
from firefox_bridge.progress import problem, progress
from firefox_bridge.validation import ValidationError, normalize_stock_code

DEFAULT_STOCK_DELAY_SECONDS = 3.0
EXIT_SUCCESS = 0
EXIT_FAILURES = 1
EXIT_INVALID_INPUT = 2


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the downloader CLI."""
    parser = argparse.ArgumentParser(
        prog="firefox-bridge-download",
        description="Bulk download IDX inlineXBRL.zip reports",
    )
    parser.add_argument(
        "--stocks",
        required=True,
        help="Comma-separated stock codes (e.g. NCKL,BBCA)",
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


def run(argv: Sequence[str] | None = None) -> int:
    """Execute one CLI run and return its exit code."""
    args = build_parser().parse_args(argv)
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
            summary.failures.append(f"{stock}: {error}")
            problem(f"FAILED {stock} {args.year}: {error}", stock=stock, year=args.year)
        time.sleep(args.delay)

    print_run_summary(summary)
    return summary.exit_code


def main(argv: Sequence[str] | None = None) -> int:
    """Console script wrapper."""
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
