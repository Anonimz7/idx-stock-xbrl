"""Asking what a run would do, without letting it do any of it.

`--dry-run` is the one mode whose entire value is being trustworthy. If it
disagreed with the real path in the reassuring direction it would be worse than
having no dry run at all, because that is the direction nobody checks.

Two things make it agree: the skip-or-fetch decision lives in one function that
both paths call, and the dry run stops before the staging scan -- because that
scan deletes files, and a dry run that quietly cleaned up wreckage would not be
one.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from firefox_bridge.cli import EXIT_FAILURES, EXIT_SUCCESS, run
from firefox_bridge.downloader.history import (
    empty_history,
    record_download_history,
    save_download_history,
)
from firefox_bridge.downloader.paths import download_history_path, final_report_path
from firefox_bridge.downloader.planning import (
    ACTION_DOWNLOAD,
    ACTION_REDOWNLOAD,
    ACTION_SKIP,
    plan_for,
)
from firefox_bridge.downloader.staging import staging_root
from firefox_bridge.validation import ValidationError

HREF = (
    "https://www.idx.co.id/Portals/0/x/Laporan%20Keuangan%20Tahun%202025"
    "/TW1/NCKL/inlineXBRL.zip"
)
HREF_T2 = HREF.replace("/TW1/", "/TW2/")
HREF_T4 = HREF.replace("/TW1/", "/Audit/")


def real_zip(payload: bytes = b"payload") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo("instance_1/Navigator.txt", date_time=(2025, 1, 1, 0, 0, 0))
        archive.writestr(info, payload)
    return buffer.getvalue()


def recorded(tmp_path: Path, quarter: int = 1, href: str = HREF, payload: bytes = b"payload") -> Path:
    path = final_report_path("NCKL", 2025, quarter, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(real_zip(payload))
    history = empty_history()
    record_download_history(history, "NCKL", 2025, quarter, href, path, tmp_path)
    save_download_history(history, tmp_path, year=2025)
    return path


# --- the decision itself -------------------------------------------------


def test_a_report_never_downloaded_would_be_fetched(tmp_path: Path) -> None:
    plan = plan_for("NCKL", 2025, 1, HREF, tmp_path)

    assert plan.action == ACTION_DOWNLOAD
    assert plan.would_download is True
    assert "belum pernah" in plan.reason


def test_a_recorded_and_matching_report_would_be_skipped(tmp_path: Path) -> None:
    recorded(tmp_path)

    plan = plan_for("NCKL", 2025, 1, HREF, tmp_path)

    assert plan.action == ACTION_SKIP
    assert plan.would_skip is True
    assert plan.would_download is False


def test_a_recorded_report_whose_file_is_gone_would_be_fetched_again(tmp_path: Path) -> None:
    """The JSON is not evidence on its own; the file has to be there too."""
    recorded(tmp_path).unlink()

    plan = plan_for("NCKL", 2025, 1, HREF, tmp_path)

    assert plan.action == ACTION_DOWNLOAD
    assert "file tidak ada" in plan.reason


def test_a_modified_report_would_be_fetched_again(tmp_path: Path) -> None:
    recorded(tmp_path)
    final_report_path("NCKL", 2025, 1, tmp_path).write_bytes(real_zip(b"other content"))

    plan = plan_for("NCKL", 2025, 1, HREF, tmp_path)

    assert plan.action == ACTION_REDOWNLOAD
    assert plan.recorded_sha256 != plan.current_sha256


def test_an_incomplete_history_entry_is_flagged_as_needing_an_update(tmp_path: Path) -> None:
    """A dry run claiming "nothing changes" when the JSON is rewritten would lie.

    The file and the hash agree, but the entry has no URL, so the real run tops
    it up.
    """
    path = recorded(tmp_path)
    history = empty_history()
    history["downloads"]["NCKL"] = {"2025": {"1": {
        "url": None, "file": "saham/2025/NCKL/NCKL_inlineXBRL_T1_2025.zip",
        "size": path.stat().st_size, "sha256": "", "duplicate_of": None,
        "integrity_status": "verified", "completed_at": "whenever",
    }}}
    save_download_history(history, tmp_path, year=2025)

    plan = plan_for("NCKL", 2025, 1, HREF, tmp_path)

    assert plan.action == ACTION_SKIP
    assert plan.history_needs_update is True


def test_planning_writes_nothing(tmp_path: Path) -> None:
    recorded(tmp_path)
    before = download_history_path(tmp_path, year=2025).read_text(encoding="utf-8")
    listing = sorted(str(path) for path in tmp_path.rglob("*"))

    plan_for("NCKL", 2025, 1, HREF, tmp_path)
    plan_for("NCKL", 2025, 2, HREF_T2, tmp_path)

    assert download_history_path(tmp_path, year=2025).read_text(encoding="utf-8") == before
    assert sorted(str(path) for path in tmp_path.rglob("*")) == listing


def test_planning_rejects_an_off_idx_url(tmp_path: Path) -> None:
    """A dry run must not report "fine" for something the run would reject."""
    with pytest.raises(ValidationError):
        plan_for("NCKL", 2025, 1, HREF.replace("www.idx.co.id", "evil.example"), tmp_path)


def test_planning_rejects_a_url_for_another_stock(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        plan_for("NCKL", 2025, 1, HREF.replace("/NCKL/", "/BBCA/"), tmp_path)


def test_tw4_is_planned_from_the_audit_path(tmp_path: Path) -> None:
    assert plan_for("NCKL", 2025, 4, HREF_T4, tmp_path).quarter == 4


# --- the CLI mode --------------------------------------------------------


class FakeClient:
    base_url = "http://127.0.0.1:8765"

    def __init__(self) -> None:
        self.snapshots = 0

    def status(self) -> dict[str, Any]:
        return {"connected": True, "extension": {"version": "0.1.8"}}

    def snapshot(self, **_kwargs: Any) -> dict[str, Any]:
        self.snapshots += 1
        return {
            "elements": [
                {"ref": "e1", "href": HREF, "role": "link", "name": "TW1"},
                {"ref": "e2", "href": HREF_T4, "role": "link", "name": "Audit"},
            ]
        }


@pytest.fixture
def _patched(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Wire the CLI to a browser stub, but leave the decision real.

    `plan_stock_year` does the browser work; the decision it returns comes from
    `plan_for`, which is the same function the real path uses. Stubbing the
    browser here keeps these tests about the CLI's behaviour, while the decision
    itself stays exercised against real files and a real history.
    """
    import firefox_bridge.cli as cli

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(cli, "FirefoxBridgeClient", lambda *_a, **_k: FakeClient())
    monkeypatch.setattr("time.sleep", lambda _s: None)
    monkeypatch.setattr(cli, "download_all_detected", lambda *_a, **_k: [])

    from firefox_bridge.downloader.planning import plan_for as real_plan

    def fake_detect(_client: Any, stock: str, year: int, download_dir: Any = None) -> list[Any]:
        calls.append({"stock": stock, "year": year})
        return [
            real_plan(stock, year, 1, HREF, download_dir),
            real_plan(stock, year, 4, HREF_T4, download_dir),
        ]

    monkeypatch.setattr(cli, "plan_stock_year", fake_detect)
    return calls


def test_dry_run_reports_without_downloading(
    _patched: list[dict[str, Any]], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    recorded(tmp_path, quarter=1)

    code = run(["--stocks", "NCKL", "--year", "2025", "--all-detected", "--dry-run",
                "--download-dir", str(tmp_path)])

    out = capsys.readouterr().out
    assert code == EXIT_SUCCESS
    assert "DRY-RUN NCKL 2025 TW1 -> LEWATI" in out
    assert "DRY-RUN NCKL 2025 TW4 -> UNDUH" in out
    assert "Tidak ada file yang diubah" in out


def test_dry_run_never_deletes_staging(
    _patched: list[dict[str, Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The staging scan removes files, so a dry run must stop before it.

    Reporting honestly while quietly tidying up is not a dry run, and it is the
    kind of surprise that only shows up when a partial download someone cared
    about has vanished.
    """
    monkeypatch.setattr("firefox_bridge.cli.scan_staging", _explode)

    run(["--stocks", "NCKL", "--year", "2025", "--all-detected", "--dry-run",
         "--download-dir", str(tmp_path)])

    capsys.readouterr()


def _explode(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("dry-run must not run the staging scan")


def test_dry_run_leaves_the_staging_folder_alone(
    _patched: list[dict[str, Any]], tmp_path: Path
) -> None:
    stale = staging_root(tmp_path) / "2025" / "NCKL" / "NCKL_inlineXBRL_T1_2025.zip"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"partial wreckage")

    run(["--stocks", "NCKL", "--year", "2025", "--all-detected", "--dry-run",
         "--download-dir", str(tmp_path)])

    assert stale.is_file(), "the dry run deleted a staged file"


def test_dry_run_does_not_touch_the_history(
    _patched: list[dict[str, Any]], tmp_path: Path
) -> None:
    recorded(tmp_path, quarter=1)
    before = download_history_path(tmp_path, year=2025).read_text(encoding="utf-8")

    run(["--stocks", "NCKL", "--year", "2025", "--all-detected", "--dry-run",
         "--download-dir", str(tmp_path)])

    assert download_history_path(tmp_path, year=2025).read_text(encoding="utf-8") == before


def test_dry_run_still_writes_a_report_when_asked(
    _patched: list[dict[str, Any]], tmp_path: Path
) -> None:
    target = tmp_path / "dry.json"

    run(["--stocks", "NCKL", "--year", "2025", "--all-detected", "--dry-run",
         "--report", str(target), "--download-dir", str(tmp_path)])

    report = json.loads(target.read_text(encoding="utf-8"))
    assert report["counts"]["processed"] == 2
    assert report["command"]["all_detected"] is True


def test_a_failed_dry_run_exits_1(
    _patched: list[dict[str, Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import firefox_bridge.cli as cli

    def explode(*_a: Any, **_k: Any) -> list[Any]:
        raise RuntimeError("halaman tidak terbuka")

    monkeypatch.setattr(cli, "plan_stock_year", explode)

    code = run(["--stocks", "NCKL", "--year", "2025", "--all-detected", "--dry-run",
                "--download-dir", str(tmp_path)])

    assert code == EXIT_FAILURES
    assert "DRY-RUN GAGAL" in capsys.readouterr().err
