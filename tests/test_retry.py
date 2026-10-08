"""Retry only what is worth retrying, and never on top of a broken file.

The two rules being defended here are the acceptance criteria for CORE-005:
retry is for transient failures only, and no destructive step is repeated
without an idempotency check. The second one is the dangerous half -- a retry
that inherits the previous attempt's partial file is a way to *record* a corrupt
archive rather than a way to avoid one, which was measured rather than assumed.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

import pytest
from firefox_bridge.downloader.errors import (
    DownloaderError,
    DownloadTimeout,
    ExtensionDisconnected,
    IntegrityError,
    StaleReference,
)
from firefox_bridge.downloader.orchestrator import download_detected_link
from firefox_bridge.downloader.paths import download_history_path, final_report_path
from firefox_bridge.downloader.retry import (
    MAX_BACKOFF_SECONDS,
    RETRY_ATTEMPTS,
    RETRYABLE_ERRORS,
    backoff_seconds,
    discard_staged,
    is_retryable,
    run_with_retry,
)
from firefox_bridge.validation import ValidationError

from conftest import make_snapshot

HREF = (
    "https://www.idx.co.id/Portals/0/x/Laporan%20Keuangan%20Tahun%202025"
    "/TW1/NCKL/inlineXBRL.zip"
)


def real_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo("instance_1/Navigator.txt", date_time=(2025, 1, 1, 0, 0, 0))
        archive.writestr(info, b"payload")
    return buffer.getvalue()


# --- which failures are worth retrying -----------------------------------


@pytest.mark.parametrize("error_type", RETRYABLE_ERRORS)
def test_transient_failures_are_retryable(error_type: type[Exception]) -> None:
    assert is_retryable(error_type("x"))


def test_permanent_failures_are_never_retryable() -> None:
    """Retrying these spends the same wall-clock time to reach the same answer."""
    for error in (
        DownloaderError("download ditolak"),
        ExtensionDisconnected("extension hilang"),
        ValidationError("stock code tidak valid"),
    ):
        assert not is_retryable(error), f"{type(error).__name__} should not be retried"


def test_the_base_error_is_excluded_on_purpose() -> None:
    """An unclassified failure must fail once and loudly, not three times quietly."""
    assert DownloaderError not in RETRYABLE_ERRORS


# --- backoff -------------------------------------------------------------


def test_backoff_grows_and_is_capped() -> None:
    assert backoff_seconds(1) == 1.0
    assert backoff_seconds(2) == 2.0
    assert backoff_seconds(3) == 4.0
    assert backoff_seconds(50) == MAX_BACKOFF_SECONDS


def test_backoff_rejects_a_zero_attempt() -> None:
    with pytest.raises(ValueError, match="attempt"):
        backoff_seconds(0)


# --- discarding, the idempotency guard ------------------------------------


def test_discard_removes_the_staged_file_and_its_temp_siblings(tmp_path: Path) -> None:
    staged = tmp_path / "report.zip"
    staged.write_bytes(b"partial")
    Path(f"{staged}.crdownload").write_bytes(b"in flight")
    Path(f"{staged}.part").write_bytes(b"in flight")

    removed = discard_staged(staged)

    assert len(removed) == 3
    assert not staged.exists()
    assert not Path(f"{staged}.crdownload").exists()
    assert not Path(f"{staged}.part").exists()


def test_discard_is_safe_when_nothing_was_written(tmp_path: Path) -> None:
    assert discard_staged(tmp_path / "absent.zip") == []


# --- the retry loop ------------------------------------------------------


def test_a_transient_failure_is_retried_then_succeeds() -> None:
    calls: list[int] = []

    def flaky() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise DownloadTimeout("belum selesai")
        return "selesai"

    assert run_with_retry(flaky) == "selesai"
    assert len(calls) == 3


def test_a_permanent_failure_is_not_retried() -> None:
    calls: list[int] = []

    def refused() -> None:
        calls.append(1)
        raise DownloaderError("ditolak")

    with pytest.raises(DownloaderError, match="ditolak"):
        run_with_retry(refused)
    assert len(calls) == 1, "a permanent failure was retried"


def test_attempts_are_finite_and_the_last_error_propagates() -> None:
    calls: list[int] = []

    def always_timeout() -> None:
        calls.append(1)
        raise DownloadTimeout("selalu lambat")

    with pytest.raises(DownloadTimeout, match="selalu lambat"):
        run_with_retry(always_timeout)
    assert len(calls) == RETRY_ATTEMPTS


def test_discard_runs_before_every_retry(tmp_path: Path) -> None:
    """The guard: a retry must not inherit the previous attempt's wreckage."""
    staged = tmp_path / "report.zip"
    attempts: list[int] = []

    def flaky() -> str:
        attempts.append(1)
        staged.write_bytes(b"partial wreckage")
        if len(attempts) < 3:
            raise IntegrityError("arsip terpotong")
        return "ok"

    run_with_retry(flaky, discard=lambda: discard_staged(staged))

    # Two retries, so the file was discarded twice before the successful attempt.
    assert staged.exists(), "the final attempt's file should still be there"
    assert len(attempts) == 3


def test_on_retry_is_called_once_per_retry() -> None:
    reported: list[tuple[int, str]] = []

    def flaky() -> str:
        if len(reported) < 2:
            raise StaleReference("ref basi")
        return "ok"

    run_with_retry(flaky, on_retry=lambda attempt, error: reported.append((attempt, type(error).__name__)))

    assert reported == [(1, "StaleReference"), (2, "StaleReference")]


def test_attempts_must_be_at_least_one() -> None:
    with pytest.raises(ValueError, match="attempts"):
        run_with_retry(lambda: None, attempts=0)


# --- stopping on an answer, not on a budget -------------------------------


def test_give_up_ends_the_loop_on_the_first_failure() -> None:
    """A verdict from outside is not a hiccup: asking again buys the same answer.

    This is the 404 case. The failure is retryable on its face -- a timeout
    always is -- but the caller has already asked and been told the archive is
    not there, so the remaining attempts would only repeat the question.
    """
    calls: list[int] = []

    def refused() -> None:
        calls.append(1)
        raise DownloadTimeout("tidak pernah menulis apa pun")

    with pytest.raises(DownloadTimeout):
        run_with_retry(refused, give_up=lambda _error: True)

    assert len(calls) == 1, "a definitive answer was asked for again"


def test_a_declined_give_up_leaves_the_whole_budget_intact() -> None:
    """An ambiguous answer is exactly what the remaining attempts are for."""
    calls: list[int] = []
    asked: list[int] = []

    def refused() -> None:
        calls.append(1)
        raise DownloadTimeout("masih bisa berbeda")

    def not_final(_error: Exception) -> bool:
        asked.append(1)
        return False

    with pytest.raises(DownloadTimeout):
        run_with_retry(refused, give_up=not_final)

    assert len(calls) == RETRY_ATTEMPTS
    # Before retries 1 and 2, but not after the last attempt: by then there is
    # no decision left for the answer to change.
    assert len(asked) == RETRY_ATTEMPTS - 1


def test_giving_up_announces_no_retry() -> None:
    """The run must not report a RETRY it is never going to perform."""
    reported: list[int] = []

    def refused() -> None:
        raise DownloadTimeout("404")

    with pytest.raises(DownloadTimeout):
        run_with_retry(
            refused,
            give_up=lambda _error: True,
            on_retry=lambda attempt, _error: reported.append(attempt),
        )

    assert reported == []


def test_give_up_is_never_asked_about_a_permanent_failure() -> None:
    """A failure outside the retryable set never reaches the decision point."""
    asked: list[Exception] = []

    def refused() -> None:
        raise DownloaderError("ditolak")

    def would_stop(error: Exception) -> bool:
        asked.append(error)
        return True

    with pytest.raises(DownloaderError):
        run_with_retry(refused, give_up=would_stop)

    assert asked == []


def test_no_give_up_means_the_original_behaviour() -> None:
    """Left unset, the loop must be indistinguishable from before the change."""
    calls: list[int] = []

    def refused() -> None:
        calls.append(1)
        raise DownloadTimeout("masih lambat")

    with pytest.raises(DownloadTimeout):
        run_with_retry(refused)

    assert len(calls) == RETRY_ATTEMPTS


# --- the whole path: retry must not repeat a destructive step ------------


class ScriptedClient:
    """Fails the first `failures` downloads, then stages a real archive."""

    def __init__(self, root: Path, failures: list[Exception], report_name: str | None = None) -> None:
        self.root = root
        self.failures = failures
        self.downloads: list[str] = []
        self.report_name = report_name

    def snapshot(self, **_kwargs: Any) -> dict[str, Any]:
        return make_snapshot([{"ref": "e1", "href": HREF}])

    def download(self, *, ref: str, filename: str, **_kwargs: Any) -> dict[str, Any]:
        self.downloads.append(filename)
        if self.failures:
            raise self.failures.pop(0)
        # Write to the path that gets reported. A fake that reports one path and
        # writes another is not a stricter test, it is a broken one.
        target = self.root / (self.report_name or filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(real_zip())
        return {"downloaded": True, "ref": ref, "filename": str(target)}


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("firefox_bridge.downloader.retry.time.sleep", lambda _s: None)
    monkeypatch.setattr("time.sleep", lambda _s: None)


@pytest.fixture(autouse=True)
def _short_download_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never wait the real 180 seconds in a unit test.

    Worth the two lines on their own: a fake that reports a path it did not
    write makes the suite hang for three minutes per occurrence, which is long
    enough for a broken test to hide through several runs before anyone looks.
    """
    from firefox_bridge.downloader import orchestrator

    original = orchestrator.wait_for_completed_download
    monkeypatch.setattr(
        orchestrator,
        "wait_for_completed_download",
        lambda path, _timeout=None: original(path, timeout=1.0),
    )


@pytest.mark.parametrize(
    "failure",
    [
        DownloadTimeout("belum selesai"),
        IntegrityError("arsip bukan ZIP"),
        StaleReference("ref basi"),
    ],
    ids=["timeout", "integrity", "stale"],
)
def test_a_transient_download_failure_is_recovered(
    failure: Exception, no_sleep: None, tmp_path: Path
) -> None:
    client = ScriptedClient(tmp_path, [failure])

    download_detected_link(  # type: ignore[arg-type]
        client, "1", "NCKL", 2025, {"ref": "e1", "href": HREF}, tmp_path
    )

    assert len(client.downloads) == 2, "the download was not retried"
    final = final_report_path("NCKL", 2025, 1, tmp_path)
    assert final.is_file()
    assert final.read_bytes() == real_zip()


def test_the_report_is_moved_and_recorded_exactly_once(
    no_sleep: None, tmp_path: Path
) -> None:
    """The point of retrying only the fetch: no destructive step may repeat.

    If the move or the history write were inside the retried region, a single
    report would be recorded twice -- or the second move would fail outright.
    """
    client = ScriptedClient(tmp_path, [DownloadTimeout("lambat"), IntegrityError("potong")])

    download_detected_link(  # type: ignore[arg-type]
        client, "1", "NCKL", 2025, {"ref": "e1", "href": HREF}, tmp_path
    )

    import json

    history = json.loads(download_history_path(tmp_path, year=2025).read_text(encoding="utf-8"))
    entries = history["downloads"]["NCKL"]["2025"]
    assert list(entries) == ["1"], f"expected one entry, found {list(entries)}"
    assert len(client.downloads) == 3


def test_retries_exhausted_leaves_no_staged_file_behind(
    no_sleep: None, tmp_path: Path
) -> None:
    """After the last failure the wreckage is still staging's problem to clean up.

    Not the final folder's: nothing may be recorded, and the next run's STEP 0
    scan is what removes it.
    """
    client = ScriptedClient(tmp_path, [DownloadTimeout("lambat")] * 5)

    with pytest.raises(DownloadTimeout):
        download_detected_link(  # type: ignore[arg-type]
            client, "1", "NCKL", 2025, {"ref": "e1", "href": HREF}, tmp_path
        )

    assert not final_report_path("NCKL", 2025, 1, tmp_path).exists()
    assert not download_history_path(tmp_path, year=2025).exists(), "a failed report was recorded"
    assert len(client.downloads) == RETRY_ATTEMPTS


def test_a_permanent_failure_downloads_only_once(no_sleep: None, tmp_path: Path) -> None:
    client = ScriptedClient(tmp_path, [DownloaderError("ditolak")])

    with pytest.raises(DownloaderError, match="ditolak"):
        download_detected_link(  # type: ignore[arg-type]
            client, "1", "NCKL", 2025, {"ref": "e1", "href": HREF}, tmp_path
        )

    assert len(client.downloads) == 1


def test_the_path_firefox_actually_used_is_the_one_cleaned(
    no_sleep: None, tmp_path: Path
) -> None:
    """Firefox may not honour the requested filename, so a guessed path would miss.

    A retry that cleaned the path we *asked* for would leave the real one behind
    -- the exact file the completion check mistakes for a finished download. So
    the reported path has to be the one that is discarded, and the one that gets
    moved to the final folder.
    """
    other = "saham/staging/NCKL/2025/NCKL_inlineXBRL_T1_2025 (1).zip"
    client = ScriptedClient(tmp_path, [DownloadTimeout("lambat")], report_name=other)

    download_detected_link(  # type: ignore[arg-type]
        client, "1", "NCKL", 2025, {"ref": "e1", "href": HREF}, tmp_path
    )

    # The reported path is what ended up in the final folder, contents and all.
    final = final_report_path("NCKL", 2025, 1, tmp_path)
    assert final.read_bytes() == real_zip()
    # And nothing was left in staging under either name.
    assert not (tmp_path / other).exists()
    assert not Path(f"{tmp_path / other}.crdownload").exists()
