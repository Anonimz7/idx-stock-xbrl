"""Orchestration of one instance.zip download, from URL to recorded history.

This is the same pipeline as :mod:`firefox_bridge.downloader.orchestrator`, with
one difference that is the entire point of the module: the URL is *constructed*
rather than *found*, so there is no profile page, no Laporan Keuangan panel, no
year dropdown and no element ref. What survives unchanged is everything after the
URL exists -- skip decision, staging, archive validation, retry, move, history.

Reusing those is not convenience, it is the requirement. Anti-duplicate and
resume are the same code in both programs, so they cannot drift apart: a report
is skipped only when the JSON entry exists, the file is on disk, and its SHA-256
still matches. Nothing here re-implements that rule; it passes a different
``download_dir`` to it.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from firefox_bridge.client import FirefoxBridgeClient
from firefox_bridge.pacing import wait_before_step

from ..downloader.errors import DownloaderError, DownloadTimeout, IntegrityError
from ..downloader.filesystem import (
    DEFAULT_TIMEOUT_SECONDS,
    TEMPORARY_SUFFIXES,
    is_download_complete,
    move_completed_download,
)
from ..downloader.history import history_entry, load_download_history, record_download_history
from ..downloader.integrity import file_sha256
from ..downloader.models import (
    SHA256_KEY,
    STATUS_DOWNLOADED,
    STATUS_SKIPPED,
    DownloadResult,
)
from ..downloader.paths import download_root
from ..downloader.retry import RETRY_ATTEMPTS, discard_staged, run_with_retry
from ..progress import notice, progress
from ..validation import ValidationError, normalize_stock_code, validate_archive
from .paths import (
    instance_download_dir,
    instance_final_path,
    instance_staging_relative_filename,
)
from .session import IdxSession, is_definitive_reason
from .urls import AUDITED_QUARTER, instance_url, validate_instance_url

# How long an absent staging path may stay absent before the download is judged
# dead. See `_await_archive` for why absence is treated as proof rather than as
# an unanswered question -- the observed behaviour is a file that appears within
# a second and is withdrawn again just as quickly when the URL resolves to
# nothing. Generous beyond the two seconds a real transfer needs here, so only a
# stalled handshake can be misread, and that case is worth retrying.
DEAD_WINDOW_SECONDS = 8.0
POLL_INTERVAL_SECONDS = 1.0


def _fetch_archive(
    client: FirefoxBridgeClient,
    stock: str,
    year: int,
    href: str,
    staging_relative: str,
    staged: list[Path],
) -> tuple[int, Path]:
    """Download one instance archive into staging and return size and path.

    Idempotent by construction: reading nothing mutable, clicking nothing, and
    resolving no ref. A failed attempt costs a partial file and a pause, which is
    why the staged path is appended to ``staged`` rather than returned -- Firefox
    may ignore the requested name, and a retry that cleaned up a guessed path
    would leave the real one behind for the completion check to mistake for a
    finished download.
    """
    wait_before_step(f"jeda sebelum download instance {stock} {year}")
    progress(
        f"STEP DOWNLOAD: instance {stock} {year} -> staging: {staging_relative}",
        stock=stock,
        year=year,
        quarter=AUDITED_QUARTER,
    )
    result = client.download_url(href, staging_relative)
    if not isinstance(result, dict) or not result.get("downloaded"):
        # Refused rather than interrupted: not a timeout and not an integrity
        # problem, so a plain non-fatal per-stock failure.
        raise DownloaderError(
            f"Download instance {stock} {year} gagal dimulai: {result}",
        )

    staging_path = _resolve_staging_path(result, staging_relative)
    staged.append(staging_path)

    wait_before_step("jeda sebelum menunggu selesai download")
    size = _await_archive(staging_path)

    # Still in staging: a truncated or non-ZIP download is rejected before it is
    # ever recorded as a report, and discarding it costs nothing.
    try:
        size = validate_archive(staging_path)
    except ValidationError as error:
        raise IntegrityError(f"Arsip instance {stock} {year} ditolak: {error}") from error
    return size, staging_path


def _staging_present(path: Path) -> bool:
    """Return True while anything at all belongs to this download on disk."""
    return path.exists() or any(
        Path(f"{path}{suffix}").exists() for suffix in TEMPORARY_SUFFIXES
    )


def _await_archive(path: Path) -> int:
    """Wait for a staged download, but not for one that will never arrive.

    The shared :func:`wait_for_completed_download` waits the full budget for any
    path, which is right for a download known to have started and wrong for this
    one. This program builds its URLs rather than reading them off a page, so a
    ticker with no audited archive is an ordinary outcome rather than an
    unexpected one -- measured on a sample of 30 active stocks, roughly 3%.

    What makes the two distinguishable is that Firefox writes the file as soon as
    it begins and **removes it again when the server refuses the URL**. So a
    path that has been absent for a while is positive evidence of failure, not
    merely the absence of evidence. Waiting the full budget for it would cost
    three attempts at 180 seconds -- nine minutes per missing ticker, over four
    hours across the active list -- while a download that has genuinely begun
    keeps a temp sibling on disk and is given the whole budget it deserves.

    The dead window is generous on purpose: real transfers here land in about two
    seconds, so only a stalled handshake can be mistaken for a refusal, and that
    is retried.
    """
    started = time.monotonic()
    last_seen = started
    previous_size = -1

    while time.monotonic() - started < DEFAULT_TIMEOUT_SECONDS:
        if _staging_present(path):
            last_seen = time.monotonic()
            if is_download_complete(path):
                size = path.stat().st_size
                if size > 0 and size == previous_size:
                    return size
                previous_size = size
            else:
                # A temp sibling means Firefox is still writing; a zero-byte file
                # means it has not finished either. Neither resets the clock for
                # the dead window, but both keep the completion budget running.
                previous_size = -1
        else:
            previous_size = -1
            if time.monotonic() - last_seen >= DEAD_WINDOW_SECONDS:
                raise DownloadTimeout(
                    f"Unduhan tidak pernah menulis apa pun selama "
                    f"{DEAD_WINDOW_SECONDS:.0f} detik: {path} "
                    f"(arsip kemungkinan tidak tersedia)"
                )
        time.sleep(POLL_INTERVAL_SECONDS)

    raise DownloadTimeout(
        f"Download tidak selesai dalam {DEFAULT_TIMEOUT_SECONDS:.0f} detik: {path}"
    )


def _resolve_staging_path(
    result: dict[str, Any],
    staging_relative: str,
) -> Path:
    """Resolve the absolute staged path reported by Firefox.

    The requested relative path is the fallback, not the first choice: Firefox
    may have honoured a name of its own, and the completion check has to watch
    the file that was actually written.

    Both branches anchor to ``download_root()`` -- where Firefox writes -- and not
    to this program's root. ``staging_relative`` is written from Firefox's point of
    view, so joining it to ``<root>/instance`` would point the wait loop at a path
    that never exists and let it time out on a download that had already
    succeeded.
    """
    returned = str(result.get("filename") or "")
    if returned:
        path = Path(returned)
        if path.is_absolute():
            return path
        return download_root() / path
    # Nothing usable came back: fall back to the path that was asked for, which
    # is still valid as long as Firefox wrote where it was told.
    return download_root() / staging_relative


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
    href: str,
    final_path: Path,
    history: dict[str, Any],
    status: str,
    attempts: int = 1,
) -> DownloadResult:
    """Build a result, reading the recorded hash back rather than re-hashing.

    The history write has already hashed the file. Hashing it again just to fill
    in a report field would double the IO of every download for no gain.
    """
    entry = history_entry(history, stock, year, AUDITED_QUARTER) or {}
    return DownloadResult(
        stock=stock,
        href=href,
        filename=str(final_path),
        status=status,
        year=year,
        quarter=AUDITED_QUARTER,
        sha256=str(entry.get(SHA256_KEY) or ""),
        bytes=final_path.stat().st_size if final_path.is_file() else 0,
        attempts=attempts,
    )


def download_instance(
    client: FirefoxBridgeClient,
    stock: str,
    year: int,
    download_dir: Path | None = None,
    session: IdxSession | None = None,
) -> DownloadResult:
    """Download one audited instance.zip, or skip it when it is already valid.

    A recorded-but-missing file and a hash mismatch both lead to a fresh
    download; only an existing file whose hash still matches is skipped.

    ``session``, when supplied, is refreshed right before a retried download:
    a download that never wrote anything is the signature of Cloudflare
    refusing the bare request, and re-pointing the kept IDX tab at the site
    gives any pending challenge a place to resolve instead of failing the
    retry the same way. It is also asked, once, what the archive URL itself
    answered while retries remain: a 404 ends the loop there, so a ticker with
    no audited archive costs one attempt rather than three, and that reason
    rides on the raised error for the caller to report without asking twice.
    """
    code = normalize_stock_code(stock)
    root = instance_download_dir(download_dir)
    href = validate_instance_url(instance_url(code, year), code, year)

    history = load_download_history(root)
    final_path = instance_final_path(code, year, root)
    recorded = history_entry(history, code, year, AUDITED_QUARTER)
    integrity_failed = False

    if final_path.is_file() and final_path.stat().st_size > 0:
        current_hash = file_sha256(final_path)
        recorded_hash = str((recorded or {}).get(SHA256_KEY) or "")
        if recorded_hash and recorded_hash != current_hash:
            integrity_failed = True
            progress(
                f"HASH MISMATCH: instance {code} {year} gagal integritas; mengunduh ulang",
                stock=code,
                year=year,
            )
        else:
            if recorded is None or recorded.get("url") != href or not recorded_hash:
                history_path = record_download_history(
                    history, code, year, AUDITED_QUARTER, href, final_path, root,
                )
                progress(
                    f"STEP SKIP: instance {code} {year} sudah ada; "
                    f"JSON diperbarui di {history_path}",
                    stock=code,
                    year=year,
                )
            else:
                progress(
                    f"STEP SKIP: instance {code} {year} sudah tercatat dan hash valid",
                    stock=code,
                    year=year,
                )
            return _make_result(
                code, year, href, final_path, history, STATUS_SKIPPED,
            )

    if recorded is not None and not integrity_failed:
        notice(
            f"WARN: JSON mencatat instance {code} {year}, tetapi file hilang; mengunduh ulang",
            stock=code,
            year=year,
        )

    # Last point before the URL is actually fetched. There is no page in between
    # here -- unlike the ref-based flow, which must re-read to outrun a re-render.
    href = validate_instance_url(href, code, year)
    staging_relative = instance_staging_relative_filename(code, year)
    staged: list[Path] = []
    attempts = {"count": 0}
    # The archive URL's answer, kept across attempts: each attempt raises its
    # own exception, so there is nothing to hang the diagnosis on until one of
    # them actually ends the loop.
    diagnosis: list[str] = []

    def _attempt() -> tuple[int, Path]:
        attempts["count"] += 1
        return _fetch_archive(
            client, code, year, href, staging_relative, staged,
        )

    def _on_retry(attempt: int, error: Exception) -> None:
        # The signature of a blocked request is a staging path that stays empty;
        # pointing the tab back at IDX gives Cloudflare a page to settle in before
        # the next attempt fires.
        if session is not None and isinstance(error, DownloadTimeout):
            session.refresh()
        notice(
            f"RETRY {attempt}/{RETRY_ATTEMPTS - 1}: instance {code} {year} "
            f"{type(error).__name__}: {error}",
            stock=code,
            year=year,
            attempt=attempt,
        )

    def _give_up(error: Exception) -> bool:
        """Ask the archive URL whether another attempt could answer differently.

        A dead-window timeout is the failure worth asking about: a 404, a
        clearance that has since lapsed and a stalled handshake are all
        indistinguishable from here, and only the first is final. Anything else
        -- an archive that failed validation, a stale ref -- is already known
        to be retryable, so it is not worth a navigation to confirm.

        Asked at most once per download. The URL is the same every time, so
        re-asking a still-ambiguous answer buys nothing; only a probe that
        never ran is left for the caller to make at the end.

        A definitive answer rides on the error, because that answer is what
        ended the retries and reporting it must not cost a second probe that
        could disagree. An ambiguous one deliberately does not: the caller
        re-probes after the last attempt for a diagnosis taken then, not one
        taken two attempts earlier. Best effort throughout -- a probe that
        cannot run is recorded as such and the retries continue, because "we do
        not know" is the one answer that must never stop a run.
        """
        if session is None or not isinstance(error, DownloadTimeout):
            return False
        if diagnosis:
            reason = diagnosis[0]
        else:
            try:
                reason = session.probe_archive_reason(code, year)
            except Exception as probe_error:  # noqa: BLE001
                reason = f"probe gagal ({type(probe_error).__name__})"
            diagnosis.append(reason)
        if not is_definitive_reason(reason):
            return False
        error.archive_reason = reason
        notice(
            f"STOP: instance {code} {year} menjawab {reason}; "
            f"percobaan dihentikan, tidak diulang",
            stock=code,
            year=year,
        )
        return True

    size, staging_path = run_with_retry(
        _attempt,
        discard=lambda: _discard_all(staged),
        on_retry=_on_retry,
        give_up=_give_up,
    )

    wait_before_step("jeda sebelum memindahkan file")
    move_completed_download(staging_path, final_path, replace=integrity_failed)
    wait_before_step("jeda sebelum menulis JSON")
    history = load_download_history(root)
    history_path = record_download_history(
        history, code, year, AUDITED_QUARTER, href, final_path, root,
    )
    progress(
        f"STEP DOWNLOAD OK: instance {code} {year} dipindahkan ke {final_path} "
        f"({size} byte)",
        stock=code,
        year=year,
        bytes=size,
    )
    progress(f"STEP JSON OK: {history_path}")
    return _make_result(
        code, year, href, final_path, history, STATUS_DOWNLOADED,
        attempts=attempts["count"],
    )


__all__ = ["download_instance"]
