"""Command line entry point for the direct instance.zip downloader.

Run from a source checkout::

    .venv\\Scripts\\python.exe -m firefox_bridge.instance.cli \\
        --catalog db\\instance_catalog.json --year 2025

After installation the same run is available as ``firefox-bridge-instance``.

Separate from ``firefox-bridge-download`` on purpose: that program reads links
out of the page, this one takes them from the catalog -- a list of archives
IDX has actually published, built by ``idx_watcher``. They share the history,
staging, retry and hash machinery, and keep separate download roots so those
histories cannot overwrite one another.

The list is not an option here. It used to be: a stock code on the command
line was turned into a URL whether or not an archive existed behind it, so
every run collected404s that meant "our guess was wrong". Reading the catalog
instead means the program only ever asks for something that was seen.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from firefox_bridge import runconfig
from firefox_bridge.captcha import CaptchaRequired, prompt_to_solve
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
from firefox_bridge.instance.catalog import CatalogError, divergent, load_entries
from firefox_bridge.instance.orchestrator import download_instance
from firefox_bridge.instance.paths import (
    instance_download_dir,
    instance_final_path,
    instance_staging_root,
)
from firefox_bridge.instance.session import NOT_FOUND_REASON, IdxSession
from firefox_bridge.instance.urls import AUDITED_QUARTER
from firefox_bridge.pacing import minimum_one_second, sleep_between_stocks
from firefox_bridge.progress import notice, problem, progress
from firefox_bridge.validation import ValidationError, normalize_stock_code

EXIT_SUCCESS = 0
EXIT_FAILURES = 1
EXIT_INVALID_INPUT = 2
EXIT_BRIDGE_UNAVAILABLE = 3
# Kept apart from the three above: a run stopped for a human is neither a
# failure of the downloader nor an unreachable bridge, and a caller reading
# the code has to be able to tell "fix your setup" from "go click the box".
EXIT_CAPTCHA = 4

# Resolved from this file rather than from the working directory, so the
# default is the same catalog no matter where the command is run from. It is
# deliberately the same path idx_watcher writes by default -- two defaults
# that disagree would make "the command from the README found nothing" the
# normal outcome.
DEFAULT_CATALOG = Path(__file__).resolve().parent.parent.parent / "db" / "instance_catalog.json"


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
        "--catalog",
        type=str,
        default=str(DEFAULT_CATALOG),
        help=(
            "Path to the instance catalog JSON produced by idx_watcher "
            "(default: db/instance_catalog.json). Every archive this run "
            "attempts is read from it -- the URL is never constructed, so a "
            "404 means the file vanished from IDX rather than that our guess "
            "was wrong."
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


def _load_catalog(args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[tuple[str, str]]:
    """Return ``(code, url)`` for the requested year, or exit as a usage error.

    Two checks happen here rather than downstream, both for the same reason:
    they are cheaper before a download exists to have to undo.

    The codes become folder names, so a catalog carrying ``../`` is rejected
    at the door -- the rule ``parse_stock_codes`` used to apply to the command
    line now applies to the file, which is just as attacker-adjacent if it
    was fetched by hand and never inspected.

    The drift report is a report, not a gate: the catalog wins, because it is
    the source of truth. It says out loud when it has stopped matching the
    URL shape this program has always reproduced, which is what a stale or
    truncated fetch looks like.
    """
    try:
        entries = load_entries(args.catalog, args.year)
    except CatalogError as error:
        parser.error(str(error))

    for code, _url in entries:
        try:
            normalize_stock_code(code)
        except ValidationError as error:
            parser.error(f"Kode di katalog tidak valid: {error}")

    drift = divergent(entries, args.year)
    if drift:
        notice(
            f"PERINGATAN: {len(drift)} entri katalog berbeda dari pola URL "
            f"yang diharapkan: {', '.join(drift[:10])}"
            f"{' ...' if len(drift) > 10 else ''}; URL katalog tetap dipakai",
            drifted=len(drift),
        )

    progress(
        f"STEP 0: katalog {args.catalog}: {len(entries)} entri tahun {args.year}",
        catalog=str(args.catalog),
        entries=len(entries),
        year=args.year,
    )
    return entries


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


def _failure_reason(
    session: IdxSession, stock: str, year: int, failure: DownloaderError,
) -> str:
    """Return the diagnosis for a failed download, probing only if nobody has.

    The retry loop asks the archive URL whether another attempt could differ,
    and hangs the answer on the error only when it ended the retrying there.
    That answer is worth reusing: it is final by definition, so re-asking would
    buy a second navigation and could disagree with the reason the run stopped
    for. Anything else -- a failure with no session to ask, a non-timeout, a
    probe the loop declined to repeat -- is diagnosed here, fresh, after the
    last attempt rather than two attempts earlier.
    """
    carried = getattr(failure, "archive_reason", None)
    if carried:
        return str(carried)
    try:
        return session.probe_archive_reason(stock, year)
    except Exception as probe_error:  # noqa: BLE001 - diagnosis never aborts a run
        return f"probe gagal ({type(probe_error).__name__})"


def _dry_run(entries: list[tuple[str, str]], year: int, download_dir: Path | None) -> int:
    """Report what a real run would do, changing nothing.

    Reads the history and hashes the files already on disk -- exactly what the
    real path does before it starts -- and stops. No browser, no bridge.

    Takes ``(code, url)`` pairs rather than codes, because the question a
    dry-run answers is "what would be fetched", and that question is only
    well-formed against a URL that actually came from somewhere.
    """
    from firefox_bridge.downloader.history import history_entry, load_download_history
    from firefox_bridge.downloader.integrity import file_sha256
    from firefox_bridge.instance.urls import validate_instance_url

    root = instance_download_dir(download_dir)
    history = load_download_history(root)
    would_download = 0
    skipped = 0

    for stock, raw_href in entries:
        href = validate_instance_url(raw_href, stock, year)
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

    entries = _load_catalog(args, parser)
    download_dir = Path(args.download_dir) if args.download_dir else None
    root = instance_download_dir(download_dir)
    delay = _stock_delay(args)

    progress(
        f"ROOT: {root} ({SAHAM_FOLDER}/...); terpisah dari program berbasis halaman",
        download_dir=str(root),
    )

    if args.dry_run:
        return _dry_run(entries, args.year, download_dir)

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
    session = IdxSession(client, prompt=prompt_to_solve)
    captcha: str | None = None
    # Tracked separately from RunSummary because the catalog deserves its own
    # two numbers: an entry it promised that never arrived, and an entry it
    # promised that IDX answered404. The second is the impossible one -- it can
    # only mean the catalog and IDX disagree, which is worth a line nobody has
    # to go digging for.
    failed_codes: list[str] = []
    not_found_codes: list[str] = []
    try:
        session.ensure()
        for stock, href in entries:
            # The pause separates requests to IDX; it is not a tax on
            # iterations that make none. True by default, so a failure --
            # which followed a request -- and a real download are both paced;
            # cleared only for the local-disk skip, which returned before any
            # network call existed to pace. Paying it across ~715 no-op skips
            # is what turned a resume into a 45-minute walk, at 3.5s each to
            # tell IDX nothing at all.
            pace = True
            try:
                result = download_instance(
                    client, stock, args.year, href, root, session=session,
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
                # tomorrow, and a recorded reason beats guessing. A reason the
                # retry loop already probed (the 404 that stopped it) is reused
                # instead of re-asking. Diagnosis is best-effort: if it cannot
                # run, the original error text is kept verbatim, so this can
                # never abort a run.
                reason = _failure_reason(session, stock, args.year, failure)
                detail = f"{failure} | alasan: {reason}"
                summary.failures.append(f"{stock}: [{kind}] {detail}")
                failed_codes.append(stock)
                if NOT_FOUND_REASON in reason:
                    not_found_codes.append(stock)
                # Persist the failure with its reason next to the successes:
                # the JSON history is the resume mechanism and the debugging
                # record in one. A failure entry carries no file keys, so the
                # skip logic (file must exist on disk with matching hash) can
                # never mistake it for a completed download -- the next run
                # retries it exactly like a stock never attempted.
                #
                # The URL recorded is the catalog's, not one this program
                # built: the 404 it captures has to be reproducible against
                # the same address the catalog gave, or the next run would
                # compare a verdict about one URL with a different URL and
                # ask the question all over again.
                try:
                    hist = load_download_history(root)
                    record_failure_history(
                        hist, stock, args.year, AUDITED_QUARTER,
                        href, kind, reason, root,
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
                pace = not result.skipped
            if pace:
                sleep_between_stocks(delay, args.delay_max)
    except CaptchaRequired as error:
        # Reached only after the operator declined or the box stayed put:
        # `CaptchaRequired` is a BaseException, so the per-stock
        # `except Exception` handlers above and below this loop pass it
        # straight through rather than recording it as one more failure.
        captcha = str(error)
    finally:
        if captcha is None:
            session.close()

    # The two numbers the catalog exists to make meaningful, printed whether or
    # not they are zero -- a report that only speaks up on trouble is one you
    # stop reading.
    succeeded = {result.stock for result in summary.results}
    unfinished = [
        code for code, _ in entries if code not in succeeded and code not in set(failed_codes)
    ]
    progress(
        f"KATALOG: {len(entries)} entri diminta, {len(succeeded)} beres, "
        f"{len(failed_codes)} gagal"
        + (f", {len(unfinished)} belum sempat dicoba" if unfinished else ""),
        requested=len(entries),
        succeeded=len(succeeded),
        failed=len(failed_codes),
        unfinished=len(unfinished),
    )
    if unfinished:
        notice(f"  -> belum beres: {', '.join(unfinished[:20])}", unfinished=len(unfinished))
    if not_found_codes:
        problem(
            f"ANOMALI: {len(not_found_codes)} entri katalog dijawab "
            f"{NOT_FOUND_REASON} -- katalog bilang ada, IDX bilang tidak: "
            f"{', '.join(not_found_codes)}",
            not_found=len(not_found_codes),
        )
        problem("  -> kemungkinan katalog basi; jalankan ulang idx_watcher")
    else:
        progress(
            f"KATALOG: 0 entri dijawab {NOT_FOUND_REASON} -- tidak ada yang "
            "tercatat ada lalu hilang",
            not_found=0,
        )

    print_run_summary(summary)
    if captcha is not None:
        # The tab is deliberately left open: it is showing the very box to
        # click, and closing it would take the answer off the screen.
        problem(f"CAPTCHA: {captcha}")
        problem("  -> run DIHENTIKAN; tab IDX sengaja dibiarkan terbuka.")
        problem('     Centang "Verify you are human" di tab itu, lalu jalankan')
        problem("     ulang perintah yang sama; download_history.json jadi dasar resume.")
        return EXIT_CAPTCHA
    if summary.fatal_error:
        return EXIT_BRIDGE_UNAVAILABLE
    return summary.exit_code


def main(argv: Sequence[str] | None = None) -> int:
    """Console script wrapper."""
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
