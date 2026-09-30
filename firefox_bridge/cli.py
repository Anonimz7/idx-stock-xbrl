"""Command line entry point for the IDX report downloader.

Run from a source checkout::

    .venv\\Scripts\\python.exe -m firefox_bridge.cli \\
        --stocks "NCKL,BBCA" --year 2025 --all-detected

After installation the same run is available as ``firefox-bridge-download``.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from firefox_bridge import runconfig
from firefox_bridge.client import FirefoxBridgeClient, FirefoxBridgeClientError
from firefox_bridge.downloader.audit import rebuild_history, verify_history
from firefox_bridge.downloader.errors import (
    DownloaderError,
    DownloadTimeout,
    ExtensionDisconnected,
)
from firefox_bridge.downloader.health import EXTENSION_UNAVAILABLE_STATUS, ensure_extension_ready
from firefox_bridge.downloader.history import (
    load_download_history,
    save_download_history,
    stock_year_complete,
)
from firefox_bridge.downloader.session import (
    load_session,
    mark_stock_done,
    new_session,
    prompt_session_name,
    save_session,
    validate_session_name,
)
from firefox_bridge.downloader.models import (
    STATUS_DOWNLOADED,
    STATUS_SKIPPED,
    DownloadResult,
    RunSummary,
)
from firefox_bridge.downloader.orchestrator import (
    download_all_detected,
    download_stock,
    plan_stock_year,
)
from firefox_bridge.downloader.paths import (
    SAHAM_FOLDER,
    download_history_path,
    download_root,
    saham_folder,
)
from firefox_bridge.downloader.reporting import print_run_summary
from firefox_bridge.downloader.run_report import build_run_report, write_run_report
from firefox_bridge.downloader.staging import (
    StagingScan,
    describe,
    scan_staging,
    staging_root,
)
from firefox_bridge.idx.link_parser import quarter_from_report_href
from firefox_bridge.pacing import (
    minimum_one_second,
    nonnegative_minutes,
    sleep_between_stocks,
)
from firefox_bridge.progress import detail, notice, problem, progress
from firefox_bridge.stocksource import StockListError, read_stock_list
from firefox_bridge.validation import ValidationError, normalize_stock_code

DEFAULT_STOCK_DELAY_SECONDS = runconfig.DEFAULT_DELAY_SECONDS
"""Kept only so an existing ``from ...cli import DEFAULT_STOCK_DELAY_SECONDS``
keeps working. The value itself now lives in :mod:`runconfig`, next to the other
run defaults and the rule that validates them."""

EXIT_SUCCESS = 0
EXIT_FAILURES = 1
EXIT_INVALID_INPUT = 2
# Promised by CLI-002 and, until now, unreachable: nothing could produce it.
EXIT_BRIDGE_UNAVAILABLE = 3


def _year_argument(value: str) -> int:
    """argparse type for `--year`, sharing the config file's rule."""
    try:
        return runconfig.parse_year(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _quarter_argument(value: str) -> int:
    """argparse type for `--quarter`, sharing the config file's rule."""
    try:
        return runconfig.parse_quarter(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the downloader CLI."""
    parser = argparse.ArgumentParser(
        prog="firefox-bridge-download",
        description="Bulk download IDX inlineXBRL.zip reports",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help=(
            "TOML or JSON file supplying the options below. Explicit wins over "
            "$FIREFOX_BRIDGE_CONFIG, which wins over a firefox-bridge.toml in the "
            "working directory. Any option given here overrides the file."
        ),
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
        "--dry-run",
        action="store_true",
        default=None,
        help=(
            "Detect what would be downloaded or skipped for each stock, and stop "
            "there. Writes no file and changes no history. The browser is still "
            "used, because the links have to be read from the page."
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
    parser.add_argument(
        "--year",
        type=_year_argument,
        default=None,
        help=f"Reporting year ({runconfig.MIN_YEAR}-{runconfig.MAX_YEAR})",
    )
    parser.add_argument(
        "--quarter",
        type=_quarter_argument,
        default=None,
        help="Reporting quarter (1-4). Ignored when --all-detected is set",
    )
    parser.add_argument(
        "--all-quarters",
        action="store_true",
        default=None,
        help="Legacy alias for --all-detected",
    )
    parser.add_argument(
        "--all-detected",
        action="store_true",
        default=None,
        help="Download every recognized inlineXBRL.zip link detected for the year",
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
        "--run-minutes",
        type=nonnegative_minutes,
        default=None,
        help=(
            "Run this many minutes, then rest automatically between stocks. "
            "After each stock, once the cumulative run time reaches this value "
            "the CLI rests for --rest-minutes minutes. 0 or unset = disabled."
        ),
    )
    parser.add_argument(
        "--rest-minutes",
        type=nonnegative_minutes,
        default=None,
        help="Rest length per cycle (minutes) when --run-minutes is active.",
    )
    parser.add_argument(
        "--session",
        type=str,
        default=None,
        help=(
            "Nama sesi run ini. Sesi yang sudah ada dilanjutkan dari emiten "
            "terakhir yang diproses (tanpa mengulang); nama baru mulai dari awal. "
            "Tanpa flag ini program menanyakan nama sesi secara interaktif."
        ),
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
    # "0 ok, 0 problems" from a root with no `saham` folder is a false all-clear,
    # and it is the exact shape a typo'd `--download-dir` produces -- passing
    # `...\saham` instead of its parent. One rule, no heuristic: if there is no
    # library there, nothing was verified, so say so and exit non-zero rather
    # than reporting a clean bill of health for a folder that was never read.
    folder = saham_folder(download_dir)
    if not folder.is_dir():
        problem(
            f"  -> tidak ada folder '{SAHAM_FOLDER}/' di {download_root(download_dir)}\n"
            f"     --download-dir menunjuk folder induk yang berisi '{SAHAM_FOLDER}/', "
            "bukan folder itu sendiri.\n"
            "     tidak ada yang bisa diverifikasi."
        )
        return EXIT_INVALID_INPUT

    report = verify_history(history, download_dir)
    if not report.findings and not folder.is_dir():
        notice("  -> tidak ada yang bisa diverifikasi")
        return EXIT_INVALID_INPUT
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
    if not report.findings:
        # A library folder that exists but holds nothing is a real state -- a
        # fresh install, or a run that downloaded nothing. Saying so is what
        # keeps it from reading as "checked everything, all fine".
        notice(
            f"  -> {folder} ada tapi kosong, tidak ada laporan untuk diverifikasi",
            library=str(folder),
        )
        return EXIT_SUCCESS
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

    if isinstance(error, TimeoutError):
        # Raised by the page layer as a built-in so that layer does not have to
        # import the file layer. Translated here, at the boundary, where every
        # other boundary translation already happens.
        return DownloadTimeout(str(error) or type(error).__name__)

    if isinstance(error, FirefoxBridgeClientError):
        status = getattr(error, "status_code", None)
        if status == EXTENSION_UNAVAILABLE_STATUS:
            return ExtensionDisconnected("Extension berhenti menjawab (HTTP 503)")
        return DownloaderError(f"Bridge menolak perintah (HTTP {status}): {error}")

    return DownloaderError(str(error) or type(error).__name__)


def _run_dry_run(
    client: FirefoxBridgeClient,
    stock_codes: list[str],
    args: argparse.Namespace,
    download_dir: Path | None,
    summary: RunSummary,
    failures: list[dict[str, Any]],
    finish: Callable[[int], int],
) -> int:
    """Report what a real run would do, changing nothing.

    Deliberately placed before the staging scan: that scan deletes files, and a
    dry run that quietly cleaned up wreckage is not a dry run. The browser is
    still used, because the links have to be read from the page to answer the
    question -- what *would* be downloaded is a property of the page, not of the
    history alone.

    `summary` and `failures` are the caller's, not local ones: the JSON report is
    built from them by `finish`, so a local copy here would produce a report
    claiming zero reports inspected for a dry run that inspected several.
    """
    for stock in stock_codes:
        try:
            plans = plan_stock_year(client, stock, args.year, download_dir)
        except Exception as error:  # noqa: BLE001
            failure = _as_downloader_error(error)
            kind = type(failure).__name__
            summary.failures.append(f"{stock}: [{kind}] {failure}")
            failures.append(
                {
                    "stock": stock, "year": args.year, "quarter": None,
                    "error_type": kind, "message": str(failure), "fatal": failure.fatal,
                }
            )
            problem(
                f"DRY-RUN GAGAL {stock} {args.year} [{kind}]: {failure}",
                stock=stock, year=args.year, error_type=kind,
            )
            if failure.fatal:
                return finish(EXIT_BRIDGE_UNAVAILABLE)
            continue

        for plan in plans:
            label = "UNDUH" if plan.would_download else "LEWATI"
            progress(
                f"DRY-RUN {stock} {plan.year} TW{plan.quarter} -> {label} ({plan.reason})",
                stock=plan.stock,
                year=plan.year,
                quarter=plan.quarter,
                action=plan.action,
                reason=plan.reason,
            )

        would_fetch = [plan for plan in plans if plan.would_download]
        summary.results.extend(
            DownloadResult(
                stock=plan.stock, href=plan.href, filename=plan.final_path,
                status=STATUS_DOWNLOADED if plan.would_download else STATUS_SKIPPED,
                year=plan.year, quarter=plan.quarter, attempts=0,
            )
            for plan in plans
        )
        progress(
            f"DRY-RUN {stock} {args.year}: {len(would_fetch)} akan diunduh, "
            f"{len(plans) - len(would_fetch)} dilewati",
            stock=stock, year=args.year, would_download=len(would_fetch),
        )
        sleep_between_stocks(args.delay, args.delay_max)

    total = sum(1 for item in summary.results if item.status == STATUS_DOWNLOADED)
    progress(
        f"DRY-RINGKAS: {total} akan diunduh, {len(summary.results) - total} dilewati, "
        f"{len(summary.failures)} gagal. Tidak ada file yang diubah.",
        would_download=total, skipped=len(summary.results) - total,
    )
    return finish(EXIT_FAILURES if summary.failures else EXIT_SUCCESS)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _command_block(
    args: argparse.Namespace,
    stock_codes: list[str],
    resolution: runconfig.Resolution,
) -> dict[str, Any]:
    """Record what was asked for, so a report is self-describing.

    A report that says what succeeded but not what was requested cannot answer
    "was this supposed to include BBCA?". Including the config file and which
    keys it supplied answers the harder version of that question -- "why did this
    run use 2024 when I always pass 2025 on the command line".
    """
    return {
        "stocks": stock_codes,
        "year": args.year,
        "quarter": args.quarter,
        "all_detected": bool(args.all_detected or args.all_quarters),
        "delay": args.delay,
        "mode": "history" if args.history else "download",
        "config": {
            "path": str(resolution.path) if resolution.path else None,
            "from_file": list(resolution.from_file),
            "from_cli": list(resolution.from_cli),
            "from_default": list(resolution.from_default),
        },
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


def _rest_state_path(download_dir: str):
    return download_root(download_dir) / "rest_state.json"


def clear_rest_state(download_dir: str) -> None:
    """Hapus penanda rehat basi (mis. dari run yang mati saat rehat)."""
    try:
        _rest_state_path(download_dir).unlink()
    except OSError:
        pass


def write_rest_state(download_dir: str, pause_until_epoch: float, reason: str, session_name: str) -> None:
    """Tulis penanda rehat agar cron laporan 30 menit tahu status ISTIRAHAT."""
    payload = {
        "pause_until": pause_until_epoch,
        "reason": reason,
        "session": session_name,
    }
    _rest_state_path(download_dir).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def take_scheduled_rest(args, download_dir: str, session_name: str) -> float:
    """Rehat terjadwal antar-emiten; kembalikan awal window jalan berikutnya."""
    rest_minutes = args.rest_minutes or 0
    if rest_minutes <= 0:
        return time.monotonic()
    pause_until = time.time() + rest_minutes * 60
    write_rest_state(
        download_dir,
        pause_until,
        f"rehat {rest_minutes:g} menit",
        session_name,
    )
    progress(
        f"REHAT: {rest_minutes:g} menit, lanjut otomatis setelahnya",
        phase="rest",
        session=session_name,
    )
    try:
        time.sleep(rest_minutes * 60)
    finally:
        clear_rest_state(download_dir)
    progress("REHAT SELESAI: lanjut unduhan", phase="rest_done", session=session_name)
    return time.monotonic()


def run(argv: Sequence[str] | None = None) -> int:
    """Execute one CLI run and return its exit code."""
    args = build_parser().parse_args(argv)

    # Before anything reads an option. The precedence has to be settled once,
    # here, so that every later branch -- history, dry run, download -- is
    # working from the same resolved values rather than re-deriving them.
    try:
        resolution = runconfig.resolve(args, args.config)
    except runconfig.ConfigError as error:
        problem(f"Config tidak valid: {error}")
        return EXIT_INVALID_INPUT
    if resolution.path is not None:
        # On the terminal, not only in the log file. A config file can change
        # what a run does without anyone passing an argument, so the one thing
        # that must never happen is for that to be invisible. `detail` alone
        # would log it and print nothing. Silent when there is no file, because
        # that is the ordinary case.
        progress(runconfig.describe(resolution), config=str(resolution.path))

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
                command=_command_block(args, stock_codes, resolution),
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

    if args.dry_run:
        return _run_dry_run(
            client, stock_codes, args, download_dir, summary, failures, _finish
        )

    # --- Sesi: nama run, lanjut dari terakhir tanpa mengulang ---
    session_name = args.session
    if session_name is None:
        if sys.stdin.isatty():
            try:
                session_name = prompt_session_name(download_dir)
            except ValueError as error:
                problem(f"Nama sesi: {error}")
                return _finish(EXIT_INVALID_INPUT)
        else:
            problem(
                "Butuh --session NAMA untuk run non-interaktif "
                "(tanpa --session, program menanyakan nama sesi di terminal)."
            )
            return _finish(EXIT_INVALID_INPUT)
    try:
        session_name = validate_session_name(session_name)
    except ValueError as error:
        problem(f"Nama sesi tidak valid: {error}")
        return _finish(EXIT_INVALID_INPUT)
    session = load_session(download_dir, session_name)
    if session is None:
        session = new_session(session_name, args.year)
        save_session(download_dir, session)
        progress(f"Sesi baru '{session_name}' untuk tahun {args.year}")
    else:
        already = set(session.get("stocks_done", []))
        if already:
            remaining = [code for code in stock_codes if code not in already]
            progress(
                f"Sesi '{session_name}': lanjut dari {session.get('last_stock')} "
                f"({len(already)} emiten sudah diproses, {len(remaining)} tersisa)"
            )
            stock_codes = remaining
            if not stock_codes:
                progress(f"Sesi '{session_name}' sudah memproses semua emiten.")
                return _finish(EXIT_SUCCESS)
        else:
            progress(f"Sesi '{session_name}' dilanjutkan (belum ada emiten diproses)")

    # Hapus penanda rehat basi (run sebelumnya mati saat rehat) dan mulai
    # window jalan untuk pola 30-menit-jalan/30-menit-rehat.
    clear_rest_state(download_dir)
    window_start = time.monotonic()

    # Snapshot sekali di awal: pre-check di bawah hanya peduli pada status
    # sebelum run ini (setiap emiten hanya dikunjungi sekali per run).
    history = load_download_history(download_dir)

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
        if all_detected and stock_year_complete(
            history, stock, args.year, download_dir
        ):
            # Emiten yang tahunnya sudah lengkap di history tidak perlu
            # dikunjungi lagi: hemat satu page-load IDX per emiten.
            progress(
                f"STEP SKIP: {stock} {args.year} sudah lengkap di history; "
                "lewati tanpa buka halaman",
                stock=stock,
                year=args.year,
            )
            mark_stock_done(session, stock)
            save_session(download_dir, session)
            continue
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
            mark_stock_done(session, stock)
            save_session(download_dir, session)
            if failure.fatal:
                # Nothing after this would work either. Say so once and stop,
                # rather than repeating the same failure for every stock left.
                problem(f"  -> run dihentikan: {failure}")
                summary.fatal_error = str(failure)
                sleep_between_stocks(args.delay, args.delay_max)
                break
        else:
            mark_stock_done(session, stock)
            save_session(download_dir, session)
        sleep_between_stocks(args.delay, args.delay_max)
        # Rehat terjadwal antar-emiten: emiten yang sedang berjalan selalu
        # diselesaikan dulu, rehat baru mulai setelah jeda antar-emiten.
        if args.run_minutes and time.monotonic() - window_start >= args.run_minutes * 60:
            window_start = take_scheduled_rest(args, download_dir, session_name)

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
