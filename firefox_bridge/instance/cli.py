"""Command line entry point for the direct instance.zip downloader.

Run from a source checkout::

    .venv\\Scripts\\python.exe -m firefox_bridge.instance.cli \\
        --stocks "NCKL,BBCA" --year 2025

After installation the same run is available as ``firefox-bridge-instance``.

Separate from ``firefox-bridge-download`` on purpose: that program reads links
out of the page, this one builds them. They share the history, staging, retry and
hash machinery, and keep separate download roots so those histories cannot
overwrite one another.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from firefox_bridge import runconfig
from firefox_bridge.client import FirefoxBridgeClient, FirefoxBridgeClientError
from firefox_bridge.downloader.errors import (
    DownloaderError,
    DownloadTimeout,
    ExtensionDisconnected,
)
from firefox_bridge.downloader.health import EXTENSION_UNAVAILABLE_STATUS, ensure_extension_ready
from firefox_bridge.downloader.history import load_download_history, record_failure_history
from firefox_bridge.downloader.models import RunSummary
from firefox_bridge.downloader.paths import SAHAM_FOLDER
from firefox_bridge.downloader.reporting import print_run_summary
from firefox_bridge.downloader.staging import describe, scan_staging
from firefox_bridge.instance.orchestrator import download_instance
from firefox_bridge.instance.paths import (
    instance_download_dir,
    instance_final_path,
    instance_staging_root,
)
from firefox_bridge.instance.session import IdxSession
from firefox_bridge.instance.urls import AUDITED_QUARTER, instance_url
from firefox_bridge.pacing import minimum_one_second, sleep_between_stocks
from firefox_bridge.progress import problem, progress
from firefox_bridge.stocksource import StockListError, read_stock_list
from firefox_bridge.validation import ValidationError, normalize_stock_code

EXIT_SUCCESS = 0
EXIT_FAILURES = 1
EXIT_INVALID_INPUT = 2
EXIT_BRIDGE_UNAVAILABLE = 3


def _year_argument(value: str) -> int:
    """argparse type for `--year`, sharing the config file's rule."""
    try:
        return runconfig.parse_year(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the instance downloader CLI."""
    parser = argparse.ArgumentParser(
        prog="firefox-bridge-instance",
        description=(
            "Download IDX audited instance.zip archives directly by URL "
            "(no page interaction)"
        ),
    )
    parser.add_argument(
        "--stocks",
        default=None,
        help="Comma-separated stock codes (e.g. NCKL,BBCA). Required unless --stocks-file is used.",
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
    parser.add_argument(
        "--year",
        type=_year_argument,
        default=None,
        help=f"Reporting year ({runconfig.MIN_YEAR}-{runconfig.MAX_YEAR}). Required.",
    )
    parser.add_argument(
        "--delay",
        type=minimum_one_second,
        default=None,
        help="Delay between stocks in seconds (minimum 1)",
    )
    parser.add_argument(
        "--delay-max",
        type=minimum_one_second,
        default=None,
        help=(
            "When set, the delay between stocks is randomized uniformly "
            "between --delay (min) and --delay-max (max) seconds."
        ),
    )
    parser.add_argument(
        "--download-dir",
        type=str,
        default=None,
        help=(
            "Root directory for downloaded archives "
            "(default: $FIREFOX_BRIDGE_INSTANCE_DIR or <default>/instance). "
            "Kept separate from the page-driven program's root so their histories "
            "cannot collide. Files are saved under <dir>/saham/<STOCK>/<YEAR>/."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help=(
            "Report which reports would be downloaded or skipped, and stop there. "
            "Writes no file and changes no history. No browser is needed, because "
            "the URLs are built rather than read from a page."
        ),
    )
    return parser


def parse_stock_codes(raw: str) -> list[str]:
    """Return normalized, de-duplicated stock codes preserving input order.

    Each code becomes a folder name under `saham/`, so validation happens here
    rather than at the point of use: the cheapest moment to reject `../` is
    before a download is started, with an exit code the caller can tell apart
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


def _collect_stocks(args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[str]:
    """Return every requested stock code, from --stocks and/or --stocks-file."""
    stock_codes: list[str] = []
    if args.stocks:
        try:
            stock_codes = parse_stock_codes(args.stocks)
        except argparse.ArgumentTypeError as error:
            parser.error(f"Stock code tidak valid: {error}")

    if args.stocks_file is not None:
        try:
            stock_list = read_stock_list(Path(args.stocks_file))
        except StockListError as error:
            parser.error(f"Daftar saham tidak bisa dibaca: {error}")
        progress(
            f"STEP 0: daftar saham dari {args.stocks_file}: "
            f"{len(stock_list.codes)} aktif, {stock_list.skipped_delisted} delisted dilewati",
            file=args.stocks_file,
            active=len(stock_list.codes),
            skipped=stock_list.skipped_delisted,
        )
        for code in stock_list.codes:
            if code not in stock_codes:
                stock_codes.append(code)

    if not stock_codes:
        parser.error("No stock codes supplied. Gunakan --stocks atau --stocks-file.")
    return stock_codes


def _as_downloader_error(error: Exception) -> DownloaderError:
    """Translate a failure into the taxonomy, or pass an existing member through."""
    if isinstance(error, DownloaderError):
        return error
    if isinstance(error, FirefoxBridgeClientError):
        status = getattr(error, "status_code", None)
        if status == EXTENSION_UNAVAILABLE_STATUS:
            return ExtensionDisconnected("Extension berhenti menjawab (HTTP 503)")
        return DownloaderError(f"Bridge menolak perintah (HTTP {status}): {error}")
    if isinstance(error, TimeoutError):
        return DownloadTimeout(str(error) or type(error).__name__)
    return DownloaderError(str(error) or type(error).__name__)


def _dry_run(stock_codes: list[str], year: int, download_dir: Path | None) -> int:
    """Report what a real run would do, changing nothing.

    Reads the history and hashes the files already on disk -- exactly what the
    real path does before it starts -- and stops. No browser, no bridge.
    """
    from firefox_bridge.downloader.history import history_entry, load_download_history
    from firefox_bridge.downloader.integrity import file_sha256
    from firefox_bridge.instance.urls import instance_url, validate_instance_url

    root = instance_download_dir(download_dir)
    history = load_download_history(root)
    would_download = 0
    skipped = 0

    for stock in stock_codes:
        href = validate_instance_url(instance_url(stock, year), stock, year)
        final_path = instance_final_path(stock, year, root)
        recorded = history_entry(history, stock, year, AUDITED_QUARTER)
        recorded_hash = str((recorded or {}).get("sha256") or "")

        if not final_path.is_file() or final_path.stat().st_size == 0:
            reason = "JSON mencatat, tetapi file tidak ada" if recorded else "belum pernah diunduh"
            action = "UNDUH"
            would_download += 1
        else:
            current = file_sha256(final_path)
            if recorded_hash and recorded_hash != current:
                reason = "hash file tidak cocok dengan JSON"
                action = "UNDUH"
                would_download += 1
            elif not recorded_hash or (recorded or {}).get("url") != href:
                reason = "history belum lengkap"
                action = "LEWATI"
                skipped += 1
            else:
                reason = "sudah tercatat dan hash valid"
                action = "LEWATI"
                skipped += 1

        progress(
            f"DRY-RUN {stock} {year} TW{AUDITED_QUARTER} -> {action} ({reason})",
            stock=stock,
            year=year,
            action=action,
            reason=reason,
        )

    progress(
        f"DRY-RINGKAS: {would_download} akan diunduh, {skipped} dilewati. "
        "Tidak ada file yang diubah.",
        would_download=would_download,
        skipped=skipped,
    )
    return EXIT_SUCCESS


def _stock_delay(args: argparse.Namespace) -> float:
    """Return the pause between stocks, falling back to the shared default.

    The page-flow CLI lets :func:`runconfig.resolve` fill every unset option from
    the config file or the defaults; this one takes no config file, so the single
    value it needs is read from the same place rather than restated. Left as
    ``None`` it reaches ``time.sleep(None)`` after the first stock and abandons
    the rest of the run with a traceback.
    """
    return runconfig.DEFAULT_DELAY_SECONDS if args.delay is None else args.delay


def run(argv: Sequence[str] | None = None) -> int:
    """Execute one CLI run and return its exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.year is None:
        parser.error("--year wajib diisi")
    if not args.stocks and not args.stocks_file:
        parser.error("No stock codes supplied. Gunakan --stocks atau --stocks-file.")

    stock_codes = _collect_stocks(args, parser)
    download_dir = Path(args.download_dir) if args.download_dir else None
    root = instance_download_dir(download_dir)
    delay = _stock_delay(args)

    progress(
        f"ROOT: {root} ({SAHAM_FOLDER}/...); terpisah dari program berbasis halaman",
        download_dir=str(root),
    )

    if args.dry_run:
        return _dry_run(stock_codes, args.year, download_dir)

    client = FirefoxBridgeClient()
    summary = RunSummary()

    # Before any work: if the extension is not connected, every attempt fails
    # identically and the user should read that fact, not one timeout per stock.
    try:
        version = ensure_extension_ready(client)
    except ExtensionDisconnected as error:
        problem(f"PRAJAMAL: {error}")
        problem("  -> nyalakan bridge, lalu tekan Connect di popup extension")
        return EXIT_BRIDGE_UNAVAILABLE
    progress(f"STEP 0.5: extension {version or 'terhubung'} (siap)", extension=version)

    # A run killed mid-download leaves a partial file at the exact path this run
    # is about to download to. Cleared up front, so every download starts from a
    # state we know.
    # Anchored to where Firefox writes, not to this program's root: cleaning the
    # wrong folder would report "bersih" while a partial file waited elsewhere for
    # the next completion check to mistake for a finished download.
    staging_root = instance_staging_root()
    scan = scan_staging(staging_root)
    progress(
        f"STEP 0: {describe(scan)} [{staging_root}]",
        examined=scan.examined,
        removed=len(scan.removed),
    )
    for path in scan.removed:
        progress(f"  - dibuang: {path.name}", reason="stale staging")

    # One IDX tab held open for the whole run, so a Cloudflare wait happens in a
    # page it can resolve instead of on the bare download request. Best effort:
    # a failure to open it is logged and the first download is left to prove the
    # point -- it must never abort a run that might otherwise still succeed.
    session = IdxSession(client)
    session.ensure()
    try:
        for stock in stock_codes:
            try:
                result = download_instance(
                    client, stock, args.year, root, session=session,
                )
            except Exception as error:  # noqa: BLE001
                failure = _as_downloader_error(error)
                kind = type(failure).__name__
                detail = str(failure)
                # A dead-window timeout is ambiguous: a 404, a Cloudflare
                # refusal and a transient bridge error all leave no file behind.
                # Ask the warm-up tab what the archive URL itself answered and
                # attach that to the failure line. Every failure kind gets the
                # same probe now -- a hash mismatch today can be a 404
                # tomorrow, and a recorded reason beats guessing. Diagnosis is
                # best-effort: if it cannot run, the original error text is
                # kept verbatim, so this can never abort a run.
                try:
                    reason = session.probe_archive_reason(stock, args.year)
                except Exception as probe_error:  # noqa: BLE001
                    reason = f"probe gagal ({type(probe_error).__name__})"
                detail = f"{failure} | alasan: {reason}"
                summary.failures.append(f"{stock}: [{kind}] {detail}")
                # Persist the failure with its reason next to the successes:
                # the JSON history is the resume mechanism and the debugging
                # record in one. A failure entry carries no file keys, so the
                # skip logic (file must exist on disk with matching hash) can
                # never mistake it for a completed download -- the next run
                # retries it exactly like a stock never attempted.
                try:
                    hist = load_download_history(root)
                    record_failure_history(
                        hist, stock, args.year, AUDITED_QUARTER,
                        instance_url(stock, args.year), kind, reason, root,
                    )
                except Exception as hist_error:  # noqa: BLE001
                    problem(
                        f"  -> gagal mencatat failure ke history: {hist_error}",
                        stock=stock, year=args.year,
                    )
                problem(
                    f"FAILED {stock} {args.year} [{kind}]: {detail}",
                    stock=stock,
                    year=args.year,
                    error_type=kind,
                )
                if failure.fatal:
                    problem(f"  -> run dihentikan: {failure}")
                    summary.fatal_error = str(failure)
                    break
            else:
                label = "Detected" if result.skipped else "Downloaded"
                progress(
                    f"{label} instance {result.stock} {args.year}: {result.filename}",
                    stock=result.stock,
                    year=args.year,
                )
                summary.results.append(result)
            sleep_between_stocks(delay, args.delay_max)
    finally:
        session.close()

    print_run_summary(summary)
    if summary.fatal_error:
        return EXIT_BRIDGE_UNAVAILABLE
    return summary.exit_code


def main(argv: Sequence[str] | None = None) -> int:
    """Console script wrapper."""
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
