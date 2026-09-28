"""The machine-readable record of a run, for something that was not watching.

The console output is written for a person. The report is written for a scheduler
that polls a file, a dashboard, or a second tool deciding whether it needs to act.
The two are kept apart on purpose: mixing them would make both unreadable, and
the console is not a log format.

The failure case is the one that matters most. A report that is missing exactly
when a run went wrong is the one anybody would have wanted it for.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from firefox_bridge.cli import EXIT_BRIDGE_UNAVAILABLE, EXIT_FAILURES, run
from firefox_bridge.downloader.models import (
    STATUS_DOWNLOADED,
    STATUS_SKIPPED,
    DownloadResult,
    RunSummary,
)
from firefox_bridge.downloader.run_report import (
    REPORT_SCHEMA_VERSION,
    build_run_report,
    write_run_report,
)

HREF = "https://www.idx.co.id/Portals/0/x/Laporan%20Keuangan%20Tahun%202025/TW1/NCKL/inlineXBRL.zip"


class FakeClient:
    def __init__(self) -> None:
        self.base_url = "http://127.0.0.1:8765"

    def status(self) -> dict[str, Any]:
        return {"connected": True, "extension": {"version": "0.1.8"}}


def _patch(monkeypatch: pytest.MonkeyPatch, results: list[DownloadResult]) -> None:
    import firefox_bridge.cli as cli

    monkeypatch.setattr(cli, "FirefoxBridgeClient", lambda *_a, **_k: FakeClient())
    monkeypatch.setattr("time.sleep", lambda _s: None)
    monkeypatch.setattr(cli, "download_all_detected", lambda *_a, **_k: list(results))


def _result(quarter: int = 1, status: str = STATUS_DOWNLOADED) -> DownloadResult:
    return DownloadResult(
        stock="NCKL", href=HREF, filename=f"C:/saham/NCKL/2025/NCKL_T{quarter}_2025.zip",
        status=status, year=2025, quarter=quarter,
        sha256="a" * 64, bytes=243904, attempts=1,
    )


# --- the report document -------------------------------------------------


def test_the_report_is_versioned() -> None:
    """A program reading reports must be able to tell what it is looking at."""
    report = build_run_report(
        summary=RunSummary(), failures=[], started_at="t0", finished_at="t1",
        command={}, environment={},
    )
    assert report["schema_version"] == REPORT_SCHEMA_VERSION


def test_the_report_describes_what_was_requested() -> None:
    """A report saying what succeeded but not what was asked cannot answer
    "was BBCA supposed to be in this run?"."""
    report = build_run_report(
        summary=RunSummary(), failures=[], started_at="t0", finished_at="t1",
        command={"stocks": ["NCKL", "BBCA"], "year": 2025}, environment={},
    )
    assert report["command"]["stocks"] == ["NCKL", "BBCA"]
    assert report["command"]["year"] == 2025


def test_counts_separate_downloads_from_skips() -> None:
    """"4 ok" hides the only question that matters: did anything actually download."""
    summary = RunSummary(results=[_result(1), _result(2, STATUS_SKIPPED), _result(3, STATUS_SKIPPED)])
    report = build_run_report(
        summary=summary, failures=[], started_at="t0", finished_at="t1",
        command={}, environment={},
    )

    assert report["counts"]["processed"] == 3
    assert report["counts"]["downloaded"] == 1
    assert report["counts"]["skipped"] == 2


def test_each_result_carries_its_own_hash_and_attempts() -> None:
    result = _result(2)
    result.attempts = 3
    summary = RunSummary(results=[result])
    report = build_run_report(
        summary=summary, failures=[], started_at="t0", finished_at="t1",
        command={}, environment={},
    )

    entry = report["results"][0]
    assert entry["sha256"] == "a" * 64
    assert entry["attempts"] == 3
    assert entry["quarter"] == 2
    assert entry["bytes"] == 243904


def test_a_failure_carries_its_type_so_a_consumer_need_not_parse_a_message() -> None:
    report = build_run_report(
        summary=RunSummary(), failures=[{
            "stock": "BBCA", "year": 2025, "quarter": None,
            "error_type": "ExtensionDisconnected", "message": "gone", "fatal": True,
        }],
        started_at="t0", finished_at="t1", command={}, environment={},
    )

    failure = report["failures"][0]
    assert failure["error_type"] == "ExtensionDisconnected"
    assert failure["fatal"] is True


def test_writing_is_atomic(tmp_path: Path) -> None:
    """A monitor polling this file must never catch it half-written.

    Truncated JSON would read as a failed run, which is a lie about the run.
    """
    report = build_run_report(
        summary=RunSummary(), failures=[], started_at="t0", finished_at="t1",
        command={}, environment={},
    )
    path = write_run_report(report, tmp_path / "nested" / "run.json")

    assert json.loads(path.read_text(encoding="utf-8")) == report
    assert not list(tmp_path.rglob("*.tmp"))


# --- the CLI surface -----------------------------------------------------


def test_a_report_is_written_when_asked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch(monkeypatch, [_result(1), _result(2, STATUS_SKIPPED)])
    target = tmp_path / "run.json"

    code = run([
        "--stocks", "NCKL", "--year", "2025", "--all-detected",
        "--report", str(target), "--download-dir", str(tmp_path),
    ])

    assert code == 0
    report = json.loads(target.read_text(encoding="utf-8"))
    assert report["counts"]["downloaded"] == 1
    assert report["environment"]["extension_version"] == "0.1.8"


def test_no_report_file_means_no_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Omitting the flag must not quietly drop a file somewhere."""
    _patch(monkeypatch, [_result(1)])

    run(["--stocks", "NCKL", "--year", "2025", "--all-detected",
         "--download-dir", str(tmp_path)])

    assert not list(tmp_path.rglob("*.json"))


def test_the_report_never_appears_in_the_progress_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mixing the two would make both unreadable. The console is not a log format."""
    _patch(monkeypatch, [_result(1)])
    target = tmp_path / "run.json"

    run(["--stocks", "NCKL", "--year", "2025", "--all-detected",
         "--report", str(target), "--download-dir", str(tmp_path)])

    captured = capsys.readouterr()
    assert "schema_version" not in captured.out
    assert "schema_version" not in captured.err


def test_a_report_is_written_even_when_the_extension_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The case anybody would want it for: the run went wrong before doing anything."""
    import firefox_bridge.cli as cli

    class Disconnected:
        base_url = "http://127.0.0.1:8765"

        def status(self) -> dict[str, Any]:
            return {"connected": False}

    monkeypatch.setattr(cli, "FirefoxBridgeClient", lambda *_a, **_k: Disconnected())
    target = tmp_path / "run.json"

    code = run(["--stocks", "NCKL", "--year", "2025", "--all-detected",
                "--report", str(target), "--download-dir", str(tmp_path)])

    assert code == EXIT_BRIDGE_UNAVAILABLE
    report = json.loads(target.read_text(encoding="utf-8"))
    assert report["environment"]["extension_version"] == ""
    assert report["counts"]["failed"] == 0


def test_a_failure_reaches_the_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import firefox_bridge.cli as cli
    from firefox_bridge.downloader.errors import StaleReference

    def explode(*_args: Any, **_kwargs: Any) -> list[DownloadResult]:
        raise StaleReference("ref basi")

    _patch(monkeypatch, [])
    monkeypatch.setattr(cli, "download_all_detected", explode)
    target = tmp_path / "run.json"

    code = run(["--stocks", "NCKL", "--year", "2025", "--all-detected",
                "--report", str(target), "--download-dir", str(tmp_path)])

    assert code == EXIT_FAILURES
    report = json.loads(target.read_text(encoding="utf-8"))
    assert report["failures"][0]["error_type"] == "StaleReference"
    assert report["failures"][0]["fatal"] is False
    assert report["counts"]["failed"] == 1


def test_an_unwritable_report_does_not_fail_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Downloads already completed. A report that cannot be written is a warning,
    not a reason to lose them."""
    _patch(monkeypatch, [_result(1)])
    # A path whose parent is a file, not a directory.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")

    code = run(["--stocks", "NCKL", "--year", "2025", "--all-detected",
                "--report", str(blocker / "sub" / "run.json"),
                "--download-dir", str(tmp_path)])

    assert code == 0
    assert "laporan tidak bisa ditulis" in capsys.readouterr().err


@pytest.mark.parametrize("status", [STATUS_DOWNLOADED, STATUS_SKIPPED])
def test_result_status_helpers_agree_with_the_field(status: str) -> None:
    result = _result(1, status)
    assert (result.downloaded, result.skipped) == (status == STATUS_DOWNLOADED, status == STATUS_SKIPPED)
