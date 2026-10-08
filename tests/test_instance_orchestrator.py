"""The direct-download program's resume and anti-duplicate behaviour.

These tests exist because the requirement is that the new program *shares* the
existing skip rule rather than restating it. What is asserted below is the rule's
observable behaviour on a separate download root: skip only on a recorded entry
plus an on-disk file plus a matching SHA-256, and re-download for each of the
three ways that can stop being true.
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path
from typing import Any

import pytest
from firefox_bridge.downloader.errors import DownloadTimeout
from firefox_bridge.downloader.history import load_download_history
from firefox_bridge.downloader.integrity import file_sha256
from firefox_bridge.downloader.paths import download_root, report_filename
from firefox_bridge.downloader.retry import RETRY_ATTEMPTS, is_retryable
from firefox_bridge.instance import (
    instance_download_dir,
    instance_final_path,
    instance_report_filename,
    instance_staging_relative_filename,
    instance_url,
)
from firefox_bridge.instance import orchestrator as instance_orchestrator
from firefox_bridge.instance.orchestrator import (
    IntegrityError,
    _await_archive,
    _resolve_staging_path,
    download_instance,
)

YEAR = 2025


def _zip_bytes(payload: str) -> bytes:
    """Return a real, valid ZIP.

    The fixture must be a genuine archive: `validate_archive` checks the ZIP
    signature and the end-of-central-directory record, and a fake byte string is
    rejected -- correctly -- before it can ever be recorded as a report.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("report.txt", payload)
    return buffer.getvalue()


def _write_report(root: Path, stock: str, payload: str = "original") -> Path:
    path = instance_final_path(stock, YEAR, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_zip_bytes(payload))
    return path


def _record(root: Path, stock: str, path: Path, href: str | None = None) -> None:
    """Write the history entry a completed download would have produced."""
    history = load_download_history(root)
    history["downloads"].setdefault(stock, {}).setdefault(str(YEAR), {})["4"] = {
        "url": href or instance_url(stock, YEAR),
        "file": path.relative_to(root).as_posix(),
        "size": path.stat().st_size,
        "sha256": file_sha256(path),
        "duplicate_of": None,
        "integrity_status": "verified",
        "completed_at": "2025-01-01T00:00:00+00:00",
    }
    target = root / "saham" / "download_history.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(history), encoding="utf-8")


def _recorded_hash(root: Path, stock: str) -> str:
    entry = load_download_history(root)["downloads"][stock][str(YEAR)]["4"]
    return str(entry["sha256"])


def _record_failure(
    root: Path, stock: str, reason: str, href: str | None = None
) -> None:
    """Write the history entry a failed attempt leaves behind.

    Deliberately carries no ``file``/``sha256``: that absence is what makes the
    shared skip rule treat the stock as never attempted, so every failure
    reaches the download point again. These tests pin the half of the decision
    that used to be missing -- the recorded ``reason`` deciding whether that
    second attempt is worth making.
    """
    history = load_download_history(root)
    history["downloads"].setdefault(stock, {}).setdefault(str(YEAR), {})["4"] = {
        "url": href or instance_url(stock, YEAR),
        "status": "failed",
        "error_type": "DownloadTimeout",
        "reason": reason,
        "fail_count": 1,
        "failed_at": "2026-10-07T00:00:00+00:00",
    }
    target = root / "saham" / "download_history.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(history), encoding="utf-8")


class _RefusingClient:
    """Any call is a test failure: the code under test must not reach the network."""

    def download_url(self, url: str, filename: str) -> Any:  # noqa: ARG002
        raise AssertionError(f"download attempted unexpectedly: {url} -> {filename}")


class _ServingClient:
    """Serve one valid archive into the path Firefox was asked to write.

    The file lands under ``download_root()``, not under the instance root: that
    is where ``browser.downloads.download`` puts a relative ``filename``, so a
    double writing anywhere else would not model the browser and the wait loop
    would time out on a file that exists elsewhere.
    """

    def __init__(self, payload: str = "fresh") -> None:
        self.payload = payload
        self.calls: list[str] = []

    def download_url(self, url: str, filename: str) -> Any:
        self.calls.append(url)
        target = download_root() / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_zip_bytes(self.payload))
        return {"downloaded": True, "url": url, "filename": str(target)}


@pytest.fixture
def instance_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point both programs at temp roots, with this one kept separate.

    The page flow's root is set too, so that a regression which drops the
    separation and writes to the shared default is caught rather than silently
    passing against the developer's real download folder.
    """
    monkeypatch.setenv("FIREFOX_BRIDGE_DOWNLOAD_DIR", str(tmp_path / "page-flow"))
    monkeypatch.delenv("FIREFOX_BRIDGE_INSTANCE_DIR", raising=False)
    root = instance_download_dir()
    assert root == tmp_path / "page-flow" / "instance"
    return root


def test_valid_entry_is_skipped_without_touching_the_client(instance_root: Path) -> None:
    path = _write_report(instance_root, "NCKL")
    _record(instance_root, "NCKL", path)

    result = download_instance(_RefusingClient(), "NCKL", YEAR)

    assert result.skipped
    assert result.quarter == 4
    assert result.sha256 == file_sha256(path)


def test_missing_file_with_a_recorded_entry_downloads(
    instance_root: Path, no_sleep: None
) -> None:
    """Recorded-but-gone is the case a naive "is it in JSON" check gets wrong."""
    path = _write_report(instance_root, "NCKL")
    _record(instance_root, "NCKL", path)
    path.unlink()

    result = download_instance(_ServingClient(), "NCKL", YEAR)

    assert result.downloaded
    assert instance_final_path("NCKL", YEAR, instance_root).is_file()


def test_recorded_404_for_the_same_url_is_not_retried(instance_root: Path) -> None:
    """A verdict the URL already gave must not be asked a second time.

    `_RefusingClient` fails the test on any network call, so the skip is
    asserted directly rather than inferred from a returned status.
    """
    _record_failure(instance_root, "NCKL", "404 Not Found")

    result = download_instance(_RefusingClient(), "NCKL", YEAR)

    assert result.skipped
    assert not instance_final_path("NCKL", YEAR, instance_root).is_file()


def test_recorded_404_for_a_different_url_is_retried(
    instance_root: Path, no_sleep: None
) -> None:
    """Fixing the URL construction voids the verdict that was based on it.

    Without this, a wrong pattern would be recorded as 404 once and then
    skipped forever -- the verdict outliving the thing it judged, with no
    reset step to recover from.
    """
    _record_failure(
        instance_root,
        "NCKL",
        "404 Not Found",
        href="https://example.invalid/wrong/NCKL.zip",
    )

    client = _ServingClient(payload="repaired")
    result = download_instance(client, "NCKL", YEAR)

    assert result.downloaded
    assert len(client.calls) == 1


def test_a_condition_is_retried_even_when_it_is_already_recorded(
    instance_root: Path, no_sleep: None
) -> None:
    """The CAPTCHA-damaged answer must be asked again, unlike the 404.

    `200 (file exists; downloads.download failed)` is a condition: the URL
    served and only the transfer failed. Skipping it would strand exactly the
    six stocks the interactive challenge broke on 2026-10-07.
    """
    _record_failure(instance_root, "NCKL", "200 (file exists; downloads.download failed)")

    client = _ServingClient()
    result = download_instance(client, "NCKL", YEAR)

    assert result.downloaded
    assert len(client.calls) == 1


def test_hash_mismatch_triggers_a_redownload(
    instance_root: Path, no_sleep: None
) -> None:
    path = _write_report(instance_root, "NCKL", "original")
    _record(instance_root, "NCKL", path)
    path.write_bytes(_zip_bytes("tampered"))
    assert file_sha256(path) != _recorded_hash(instance_root, "NCKL")

    client = _ServingClient(payload="repaired")
    result = download_instance(client, "NCKL", YEAR)

    assert result.downloaded
    assert len(client.calls) == 1
    # Recorded and on-disk agree again, so the next run will skip.
    assert _recorded_hash(instance_root, "NCKL") == file_sha256(
        instance_final_path("NCKL", YEAR, instance_root)
    )


def test_unrecorded_file_is_skipped_and_the_history_is_completed(
    instance_root: Path,
) -> None:
    """A file on disk with no JSON entry must not be fetched again.

    It is topped up instead -- the entry the next run needs in order to skip --
    because re-downloading a file that is already correct is exactly the
    duplication the resume mechanism prevents.
    """
    path = _write_report(instance_root, "NCKL")
    assert not (instance_root / "saham" / "download_history.json").exists()

    result = download_instance(_RefusingClient(), "NCKL", YEAR)

    assert result.skipped
    entry = load_download_history(instance_root)["downloads"]["NCKL"][str(YEAR)]["4"]
    assert entry["url"] == instance_url("NCKL", YEAR)
    assert entry["sha256"] == file_sha256(path)


def test_history_lives_under_the_instance_root_not_the_page_flow_root(
    instance_root: Path,
) -> None:
    """The whole separation is pointless if the JSON still lands in one place."""
    path = _write_report(instance_root, "NCKL")
    _record(instance_root, "NCKL", path)

    page_flow = instance_root.parent
    assert (instance_root / "saham" / "download_history.json").is_file()
    assert not (page_flow / "saham" / "download_history.json").exists()


def test_a_second_run_skips_instead_of_downloading_twice(
    instance_root: Path, no_sleep: None
) -> None:
    """The anti-duplicate property, end to end."""
    first_client = _ServingClient(payload="once")

    first = download_instance(first_client, "NCKL", YEAR)
    second = download_instance(_RefusingClient(), "NCKL", YEAR)

    assert first.downloaded
    assert second.skipped
    assert len(first_client.calls) == 1


def test_the_two_programs_use_different_filenames() -> None:
    """Same folder, two archives: the names must not collide.

    History is keyed without an archive column, so if both programs aimed at the
    same filename they would also be aiming at the same file -- one would
    overwrite the other and both would see a hash mismatch on every later run.
    """
    mine = instance_report_filename("NCKL", YEAR)
    theirs = report_filename("NCKL", YEAR, 4)

    assert mine == "NCKL_instance_T4_2025.zip"
    assert theirs == "NCKL_inlineXBRL_T4_2025.zip"
    assert mine != theirs


def test_the_fallback_staging_path_uses_firefoxs_root(instance_root: Path) -> None:
    """The path Firefox reports wins; the path we asked for is only a fallback.

    Both branches must agree with each other, because the wait loop watches the
    reported path while ``scan_staging`` sweeps the asked-for one. Joining either
    to the instance root would send the wait to a folder Firefox never writes and
    time it out on a download that had already succeeded.
    """
    page_flow = instance_root.parent
    rel = instance_staging_relative_filename("NCKL", YEAR)

    asked = _resolve_staging_path({}, rel)
    returned = _resolve_staging_path(
        {"filename": str(page_flow / rel)}, rel
    )
    ambiguous = _resolve_staging_path({"filename": rel}, rel)

    assert asked == download_root() / rel
    assert asked.is_relative_to(page_flow)
    assert returned == page_flow / rel
    assert not asked.is_relative_to(instance_root)
    assert ambiguous == download_root() / rel


@pytest.fixture
def fast_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the wait budgets so the loop's decisions can be watched directly."""
    monkeypatch.setattr(instance_orchestrator, "POLL_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(instance_orchestrator, "DEAD_WINDOW_SECONDS", 0.05)
    monkeypatch.setattr(instance_orchestrator, "DEFAULT_TIMEOUT_SECONDS", 0.3)


def test_a_finished_download_is_returned(fast_wait: None, tmp_path: Path) -> None:
    path = tmp_path / "NCKL_instance_T4_2025.zip"
    path.write_bytes(b"x" * 128)

    assert _await_archive(path) == 128


def test_a_download_that_never_starts_fails_on_the_dead_window(
    fast_wait: None, tmp_path: Path
) -> None:
    """The whole point: absence is reported as failure, not as a 180s wait.

    A URL with no archive behind it writes nothing. Waiting out the completion
    budget would cost three attempts at three minutes each, for every ticker
    that never filed -- and it would still reach the same verdict.
    """
    path = tmp_path / "GONE.zip"
    started = time.monotonic()

    with pytest.raises(DownloadTimeout, match="tidak pernah menulis apa pun") as caught:
        _await_archive(path)

    # Dead window, not the completion budget: this branch fires first.
    assert time.monotonic() - started < instance_orchestrator.DEFAULT_TIMEOUT_SECONDS
    assert is_retryable(caught.value)


def test_a_file_that_appears_and_is_then_withdrawn_is_dead(
    fast_wait: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shape Firefox actually produces for a refused URL: seen, then gone.

    The shared completion check samples size twice a second apart, so a file
    that vanishes between samples never settles and would be watched for the
    full budget. Here the same withdrawal is read as the failure it is.
    """
    path = tmp_path / "VANISH.zip"
    path.write_bytes(b"")
    first_sight = {"done": False}

    def withdraw(_seconds: float) -> None:
        if not first_sight["done"]:
            first_sight["done"] = True
            path.unlink(missing_ok=True)

    monkeypatch.setattr(instance_orchestrator.time, "sleep", withdraw)

    with pytest.raises(DownloadTimeout, match="tidak pernah menulis apa pun"):
        _await_archive(path)


def test_a_download_that_has_begun_gets_the_whole_budget(
    fast_wait: None, tmp_path: Path
) -> None:
    """A temp sibling proves Firefox is writing: absence is then not absence."""
    path = tmp_path / "SLOW.zip"
    Path(f"{path}.crdownload").write_bytes(b"")

    with pytest.raises(DownloadTimeout, match="tidak selesai dalam"):
        _await_archive(path)


class _StalledDownload:
    """Report success but write nothing -- the signature of a challenged request."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def download_url(self, url: str, filename: str) -> Any:
        self.calls.append(url)
        return {"downloaded": True, "url": url, "filename": filename}


class _GarbageDownload:
    """Write a corrupt body so the download completes but validation rejects it."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def download_url(self, url: str, filename: str) -> Any:
        self.calls.append(url)
        target = download_root() / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"corrupt zip")
        return {"downloaded": True, "url": url, "filename": str(target)}


class _RecordingSession:
    """The warm-up tab, reduced to what the retry loop can observe.

    ``probe_reason`` is the verdict the archive URL gives when asked; the
    default is a challenge, which is the transient case the remaining attempts
    exist for. Passing ``"404 Not Found"`` makes it the final case.
    """

    def __init__(self, probe_reason: str = "Cloudflare challenge") -> None:
        self.refreshes = 0
        self.probe_reason = probe_reason
        self.probes: list[str] = []

    def probe_archive_reason(self, stock: str, year: int) -> str:
        self.probes.append(f"{stock} {year}")
        return self.probe_reason

    def refresh(self) -> None:
        self.refreshes += 1

    def close(self) -> None:
        pass


def test_a_blocked_download_refreshes_the_session(
    monkeypatch: pytest.MonkeyPatch, no_sleep: None, instance_root: Path
) -> None:
    """A download that never writes is read as Cloudflare refusal and refreshed.

    This is the recovery path the whole warm-up tab exists for: the bare request
    was answered with a 403 challenge, so before retrying the session is re-pointed
    at IDX to give any pending challenge a place to resolve. The probe's answer
    here is that same challenge -- transient -- which is what keeps the retries
    running instead of ending them after the first attempt.
    """
    monkeypatch.setattr(instance_orchestrator, "DEAD_WINDOW_SECONDS", 0.05)
    monkeypatch.setattr(instance_orchestrator, "POLL_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(instance_orchestrator, "DEFAULT_TIMEOUT_SECONDS", 1.0)

    client = _StalledDownload()
    session = _RecordingSession()

    with pytest.raises(DownloadTimeout) as caught:
        download_instance(client, "NCKL", YEAR, session=session)

    assert client.calls  # tried at least once
    assert session.refreshes == 2  # on_retry fires before attempts 2 and 3
    # Asked once, not once per retry: the URL is the same both times, so an
    # answer that is still ambiguous buys nothing by being asked again.
    assert session.probes == ["NCKL 2025"]
    # And no ambiguous answer was hung on the error -- the caller diagnoses
    # that one itself, after the last attempt rather than before the second.
    assert caught.value.archive_reason is None


def test_a_404_answer_ends_the_retries_after_a_single_attempt(
    fast_wait: None, no_sleep: None, instance_root: Path
) -> None:
    """The requirement: a final answer is not retried, however retryable it looks.

    From the inside a 404 is indistinguishable from a lapsed clearance or a
    stalled handshake, and each of the two extra attempts buys a dead window, a
    session refresh and a backoff -- all to be told a second and a third time
    that the ticker has no 2025 audited archive. Asking the URL is what tells
    them apart, and the answer is obeyed.
    """
    client = _StalledDownload()
    session = _RecordingSession(probe_reason="404 Not Found")

    with pytest.raises(DownloadTimeout):
        download_instance(client, "NCKL", YEAR, session=session)

    assert len(client.calls) == 1, "a definitive 404 was asked for again"
    assert session.refreshes == 0, "the session was warmed for a URL that is gone"
    assert session.probes == ["NCKL 2025"]


def test_the_reason_that_ended_the_retries_rides_on_the_error(
    fast_wait: None, no_sleep: None, instance_root: Path
) -> None:
    """The diagnosis must survive to the caller, or it gets bought twice.

    The CLI reports ``... | alasan: 404 Not Found`` and records it in history.
    Re-probing for that line would cost another navigation and could disagree
    with the very answer the run stopped on -- so the answer rides the error.
    """
    client = _StalledDownload()
    session = _RecordingSession(probe_reason="404 Not Found")

    with pytest.raises(DownloadTimeout) as caught:
        download_instance(client, "NCKL", YEAR, session=session)

    assert caught.value.archive_reason == "404 Not Found"


def test_a_probe_that_cannot_run_never_stops_the_retries(
    fast_wait: None, no_sleep: None, instance_root: Path
) -> None:
    """A "we do not know" is the one diagnosis that must never stop a run.

    Diagnosis is best effort everywhere else in this program, and here it is
    load-bearing: a bridge hiccup during the probe must not be mistaken for the
    archive being gone, or a healthy ticker loses its retries to a gap in the
    evidence.
    """

    class _BlindSession(_RecordingSession):
        def probe_archive_reason(self, stock: str, year: int) -> str:
            self.probes.append(f"{stock} {year}")
            raise RuntimeError("bridge mati")

    client = _StalledDownload()
    session = _BlindSession()

    with pytest.raises(DownloadTimeout) as caught:
        download_instance(client, "NCKL", YEAR, session=session)

    assert len(client.calls) == RETRY_ATTEMPTS, "an unread probe stopped the retries"
    assert session.refreshes == RETRY_ATTEMPTS - 1
    # Asked once and believed once -- but a "we do not know" is not an answer
    # to hang on the error, so the caller still has to diagnose the failure.
    assert session.probes == ["NCKL 2025"]
    assert caught.value.archive_reason is None


def test_a_corrupt_download_is_not_treated_as_a_blocked_one(
    monkeypatch: pytest.MonkeyPatch, no_sleep: None, instance_root: Path
) -> None:
    """An IntegrityError is a bad archive, not a challenge -- don't refresh."""
    client = _GarbageDownload()
    session = _RecordingSession()

    with pytest.raises(IntegrityError):
        download_instance(client, "NCKL", YEAR, session=session)

    assert session.refreshes == 0
    # A file that arrived (badly) already answers "is the URL there?" -- the
    # probe exists for the case where nothing arrived, so it is not spent here.
    assert session.probes == []

