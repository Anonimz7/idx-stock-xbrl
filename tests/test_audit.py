"""Auditing the history against the files, in both directions.

`verify` exists because the history and the disk are two halves of an agreement
and either can drift. The direction that is easy to forget is the second one: an
archive that a killed run already moved, with no record of it. Looking only for
problems the history knows about would miss exactly those files, and the next
run would download them all again.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from firefox_bridge.cli import EXIT_FAILURES, EXIT_INVALID_INPUT, EXIT_SUCCESS, run
from firefox_bridge.downloader.audit import (
    STATUS_CORRUPT,
    STATUS_MISMATCH,
    STATUS_MISSING,
    STATUS_RECOVERED,
    rebuild_history,
    verify_history,
)
from firefox_bridge.downloader.history import (
    empty_history,
    load_download_history,
    record_download_history,
    save_download_history,
)
from firefox_bridge.downloader.paths import final_report_path

HREF = (
    "https://www.idx.co.id/Portals/0/x/Laporan%20Keuangan%20Tahun%202025"
    "/TW1/NCKL/inlineXBRL.zip"
)


def real_zip(payload: bytes = b"payload") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo("instance_1/Navigator.txt", date_time=(2025, 1, 1, 0, 0, 0))
        archive.writestr(info, payload)
    return buffer.getvalue()


def recorded(tmp_path: Path, quarter: int = 1, payload: bytes = b"payload") -> Path:
    """Create one fully recorded report, the way a successful run leaves it."""
    path = final_report_path("NCKL", 2025, quarter, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(real_zip(payload))
    history = empty_history()
    record_download_history(history, "NCKL", 2025, quarter, HREF, path, tmp_path)
    save_download_history(history, tmp_path)
    return path


# --- verify --------------------------------------------------------------


def test_an_empty_folder_verifies_cleanly(tmp_path: Path) -> None:
    report = verify_history(empty_history(), tmp_path)

    assert report.findings == ()
    assert report.healthy is True


def test_a_matching_file_verifies(tmp_path: Path) -> None:
    recorded(tmp_path)
    report = verify_history(load_download_history(tmp_path), tmp_path)

    assert len(report.ok) == 1
    assert report.healthy is True


def test_a_deleted_file_is_reported_missing(tmp_path: Path) -> None:
    recorded(tmp_path).unlink()
    report = verify_history(load_download_history(tmp_path), tmp_path)

    assert len(report.by_status(STATUS_MISSING)) == 1
    assert report.healthy is False


def test_a_modified_file_is_reported_as_a_mismatch(tmp_path: Path) -> None:
    path = recorded(tmp_path)
    path.write_bytes(real_zip(b"a completely different payload"))
    report = verify_history(load_download_history(tmp_path), tmp_path)

    assert len(report.by_status(STATUS_MISMATCH)) == 1
    finding = report.by_status(STATUS_MISMATCH)[0]
    assert finding.expected_sha256 != finding.actual_sha256
    assert both_present(finding.expected_sha256, finding.actual_sha256)


def both_present(expected: str, actual: str) -> bool:
    return bool(expected) and bool(actual)


def test_a_file_matching_its_hash_but_not_a_zip_is_corrupt(tmp_path: Path) -> None:
    """A hash is only as meaningful as the thing it was taken from."""
    path = final_report_path("NCKL", 2025, 1, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"PK\x03\x04 truncated but hashed on purpose")

    history = empty_history()
    import hashlib

    relative = "saham/NCKL/2025/NCKL_inlineXBRL_T1_2025.zip"
    history["downloads"]["NCKL"] = {"2025": {"1": {
        "url": HREF, "file": relative, "size": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "duplicate_of": None, "integrity_status": "verified", "completed_at": "now",
    }}}

    report = verify_history(history, tmp_path)

    assert len(report.by_status(STATUS_CORRUPT)) == 1
    assert report.healthy is False


def test_an_orphan_is_reported(tmp_path: Path) -> None:
    """A run killed between the move and the history write leaves exactly this.

    The archive is real, but nothing records it, so the next run would fetch it
    again for no reason.
    """
    path = final_report_path("NCKL", 2025, 1, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(real_zip())

    report = verify_history(empty_history(), tmp_path)

    assert len(report.orphans) == 1
    assert report.orphans[0].stock == "NCKL"
    assert report.orphans[0].quarter == 1


def test_an_orphan_does_not_make_the_report_unhealthy(tmp_path: Path) -> None:
    """An unrecorded archive is something to adopt, not something broken."""
    path = final_report_path("NCKL", 2025, 1, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(real_zip())

    report = verify_history(empty_history(), tmp_path)

    assert report.orphans
    assert report.problems == ()
    assert report.healthy is True


def test_a_recorded_file_is_not_also_reported_as_an_orphan(tmp_path: Path) -> None:
    recorded(tmp_path)
    report = verify_history(load_download_history(tmp_path), tmp_path)

    assert report.orphans == ()


def test_verify_writes_nothing(tmp_path: Path) -> None:
    recorded(tmp_path)
    before = json.dumps(load_download_history(tmp_path), sort_keys=True)

    verify_history(load_download_history(tmp_path), tmp_path)

    assert json.dumps(load_download_history(tmp_path), sort_keys=True) == before


def test_a_file_named_like_nothing_recognisable_is_ignored(tmp_path: Path) -> None:
    """A stray zip must not be filed under a guessed stock code."""
    stray = tmp_path / "saham" / "NCKL" / "2025" / "notes.zip"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(real_zip())

    report = verify_history(empty_history(), tmp_path)

    assert report.orphans == ()
    assert report.findings == ()


# --- rebuild -------------------------------------------------------------


def test_rebuild_recovers_entries_from_the_files_on_disk(tmp_path: Path) -> None:
    recorded(tmp_path, quarter=1)
    recorded(tmp_path, quarter=4, payload=b"audit payload")

    history, report = rebuild_history(tmp_path)

    assert len(report.findings) == 2
    assert all(finding.status == STATUS_RECOVERED for finding in report.findings)
    assert set(history["downloads"]["NCKL"]["2025"]) == {"1", "4"}


def test_a_rebuilt_entry_does_not_invent_a_url(tmp_path: Path) -> None:
    """The filename cannot say which IDX path it came from, and guessing is worse."""
    recorded(tmp_path)

    history, _ = rebuild_history(tmp_path)

    entry = history["downloads"]["NCKL"]["2025"]["1"]
    assert entry["url"] is None
    assert entry["url_recovered"] is False


def test_a_rebuilt_entry_carries_a_real_hash_and_size(tmp_path: Path) -> None:
    import hashlib

    path = recorded(tmp_path)

    history, _ = rebuild_history(tmp_path)

    entry = history["downloads"]["NCKL"]["2025"]["1"]
    assert entry["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert entry["size"] == path.stat().st_size


def test_rebuild_keeps_urls_the_old_history_already_knew(tmp_path: Path) -> None:
    """Rebuild is a repair. It must not throw away what is already recorded.

    Losing every URL to fix one missing entry would be a reset wearing a repair
    tool's name -- and the URLs are the one thing a file name cannot rebuild.
    """
    recorded(tmp_path, quarter=1)
    recorded(tmp_path, quarter=2, payload=b"second")

    history, _ = rebuild_history(tmp_path, load_download_history(tmp_path))

    quarter_two = history["downloads"]["NCKL"]["2025"]["2"]
    assert quarter_two["url"] == HREF
    assert quarter_two["url_recovered"] is True


def test_rebuild_fills_the_gap_for_an_entry_the_history_never_knew(tmp_path: Path) -> None:
    recorded(tmp_path, quarter=1)
    path = final_report_path("NCKL", 2025, 2, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(real_zip(b"recovered"))

    history, report = rebuild_history(tmp_path, load_download_history(tmp_path))

    quarter_two = history["downloads"]["NCKL"]["2025"]["2"]
    assert quarter_two["url"] is None
    assert quarter_two["url_recovered"] is False
    assert any("tidak dapat direkonstruksi" in item.detail for item in report.findings)


def test_rebuild_refuses_to_record_an_unreadable_archive(tmp_path: Path) -> None:
    path = final_report_path("NCKL", 2025, 1, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"<html>404</html>")

    history, report = rebuild_history(tmp_path)

    assert history["downloads"] == {}
    assert len(report.problems) == 1
    assert report.problems[0].status == STATUS_CORRUPT


def test_a_rebuilt_history_verifies_cleanly(tmp_path: Path) -> None:
    """The point of rebuild: the result is something verify agrees with."""
    recorded(tmp_path, quarter=1)
    recorded(tmp_path, quarter=2, payload=b"second")

    history, _ = rebuild_history(tmp_path)
    save_download_history(history, tmp_path)

    report = verify_history(load_download_history(tmp_path), tmp_path)

    assert report.healthy is True
    assert len(report.ok) == 2
    assert report.orphans == ()


# --- the CLI surface -----------------------------------------------------


def test_the_cli_still_requires_stocks_without_history(capsys: pytest.CaptureFixture[str]) -> None:
    assert run([]) == EXIT_INVALID_INPUT
    assert "--stocks" in capsys.readouterr().err


def test_history_verify_on_an_empty_library_says_so(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A library folder that exists but holds nothing is a real state.

    A fresh install has one. The tool must say it looked and found nothing,
    because the alternative -- printing "0 ok" and exiting cleanly -- is
    indistinguishable from a fully verified library.
    """
    (tmp_path / "saham").mkdir()

    code = run(["--history", "verify", "--download-dir", str(tmp_path)])

    assert code == EXIT_SUCCESS
    # stdout, not stderr: an empty library is a state, not a failure.
    assert "tidak ada laporan untuk diverifikasi" in capsys.readouterr().out


def test_verify_refuses_to_all_clear_a_root_that_does_not_exist(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """`0 ok, 0 problems` from a typo'd path is a false all-clear.

    The earlier version printed exactly that for `--download-dir ...\\saham`,
    because that folder has no `saham` inside it. It looked identical to a
    clean, fully verified library.
    """
    code = run(["--history", "verify", "--download-dir", str(tmp_path / "tidak-ada")])

    assert code == EXIT_INVALID_INPUT
    assert "tidak ada yang bisa diverifikasi" in capsys.readouterr().err


def test_verify_points_the_saham_itself_out_as_a_wrong_root(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The mistake this guard exists for: passing `...\\saham` instead of its parent."""
    (tmp_path / "saham" / "NCKL" / "2025").mkdir(parents=True)

    code = run(["--history", "verify", "--download-dir", str(tmp_path / "saham")])

    assert code == EXIT_INVALID_INPUT
    error = capsys.readouterr().err
    assert "saham/" in error
    assert "folder induk" in error, "the message should say which level to pass"


def test_verify_still_reports_a_real_library(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The guard must not swallow the normal case."""
    recorded(tmp_path)

    code = run(["--history", "verify", "--download-dir", str(tmp_path)])

    assert code == EXIT_SUCCESS
    assert "1 ok" in capsys.readouterr().out


def test_history_verify_reports_a_missing_file_and_exits_1(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    recorded(tmp_path).unlink()

    code = run(["--history", "verify", "--download-dir", str(tmp_path)])

    assert code == EXIT_FAILURES
    assert "MISSING" in capsys.readouterr().err


def test_history_verify_does_not_need_a_bridge(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Offline by design: the tool you reach for because you distrust the state."""
    recorded(tmp_path)

    code = run(["--history", "verify", "--download-dir", str(tmp_path)])

    assert code == EXIT_SUCCESS
    assert "extension" not in capsys.readouterr().out.lower()


def test_history_rebuild_writes_the_json(tmp_path: Path) -> None:
    recorded(tmp_path)
    (tmp_path / "saham" / "download_history.json").unlink()

    code = run(["--history", "rebuild", "--download-dir", str(tmp_path)])

    assert code == EXIT_SUCCESS
    assert "NCKL" in load_download_history(tmp_path)["downloads"]


def test_history_rebuild_refuses_when_an_archive_is_broken(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    recorded(tmp_path)
    (tmp_path / "saham" / "download_history.json").unlink()
    final_report_path("NCKL", 2025, 1, tmp_path).write_bytes(b"<html>404</html>")

    code = run(["--history", "rebuild", "--download-dir", str(tmp_path)])

    assert code == EXIT_FAILURES
    assert not (tmp_path / "saham" / "download_history.json").exists(), (
        "a broken archive must not produce a history file"
    )
