"""Staging is scratch space, and scratch space gets cleared on the next run.

The failure these tests exist for was measured, not imagined: a partial file
left by a killed run sits at exactly the path the next run downloads to, has no
temp sibling, and is reported as a finished download by a check that only
requires the size to hold steady across two samples. A file nobody is writing
always holds steady.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from firefox_bridge.downloader.filesystem import is_download_complete, wait_for_completed_download
from firefox_bridge.downloader.staging import (
    describe,
    scan_staging,
    staging_root,
)


def partial_report(path: Path, payload: int = 4096) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"PK\x03\x04" + b"\x00" * payload)
    return path


def test_a_missing_staging_folder_is_not_an_error(tmp_path: Path) -> None:
    scan = scan_staging(tmp_path / "staging")

    assert scan.examined == 0
    assert scan.clean is True
    assert describe(scan) == "staging bersih"


def test_staging_root_sits_under_the_saham_folder(tmp_path: Path) -> None:
    assert staging_root(tmp_path) == tmp_path / "saham" / "staging"


def test_a_leftover_partial_file_is_removed(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    leftover = partial_report(staging / "NCKL" / "2025" / "NCKL_inlineXBRL_T1_2025.zip")

    scan = scan_staging(staging)

    assert scan.removed == [leftover]
    assert not leftover.exists()


def test_the_measured_race_is_closed_by_scanning_first(tmp_path: Path) -> None:
    """Before the scan, the leftover reads as a finished download. After, it is gone.

    This is the whole point of DATA-003, written as the exact sequence that used
    to be a problem: a killed run leaves a partial file, and the next run asks
    the completion check about it.
    """
    staging = tmp_path / "staging"
    leftover = partial_report(staging / "NCKL" / "2025" / "NCKL_inlineXBRL_T1_2025.zip")

    # The hazard, demonstrated: no temp sibling, so the check says "complete".
    assert is_download_complete(leftover) is True
    assert wait_for_completed_download(leftover, timeout=5) == leftover.stat().st_size

    # With the scan, the next run cannot be fooled by it.
    scan_staging(staging)
    assert is_download_complete(leftover) is False


def test_an_in_flight_download_is_never_touched(tmp_path: Path) -> None:
    """Firefox keeps a temp sibling for the whole transfer; that means 'working'.

    Deleting these would abort a download the user may have started by hand.
    """
    staging = tmp_path / "staging"
    target = partial_report(staging / "NCKL" / "2025" / "NCKL_inlineXBRL_T1_2025.zip")
    temp = Path(f"{target}.crdownload")
    temp.write_bytes(b"\x00" * 100)

    scan = scan_staging(staging)

    assert scan.kept_in_flight == [target]
    assert not scan.removed
    assert target.exists(), "an in-flight download was deleted"
    assert temp.exists(), "a Firefox temp file was deleted"


@pytest.mark.parametrize("suffix", [".crdownload", ".part"])
def test_both_firefox_temp_suffixes_mark_in_flight(tmp_path: Path, suffix: str) -> None:
    staging = tmp_path / "staging"
    target = partial_report(staging / "NCKL" / "2025" / "NCKL_inlineXBRL_T1_2025.zip")
    Path(f"{target}{suffix}").write_bytes(b"\x00")

    assert scan_staging(staging).kept_in_flight == [target]


def test_a_finished_staged_archive_is_also_discarded(tmp_path: Path) -> None:
    """Nothing in staging has been recorded in history yet, so a re-download is cheap.

    Keeping it would only risk it being mistaken for the file this run is about
    to fetch.
    """
    staging = tmp_path / "staging"
    finished = partial_report(staging / "NCKL" / "2025" / "NCKL_inlineXBRL_T1_2025.zip", 9000)

    scan = scan_staging(staging)

    assert scan.removed == [finished]
    assert not finished.exists()


def test_empty_directories_are_pruned_but_the_root_stays(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    partial_report(staging / "NCKL" / "2025" / "NCKL_inlineXBRL_T1_2025.zip")
    partial_report(staging / "ITMG" / "2025" / "ITMG_inlineXBRL_T1_2025.zip")
    (staging / "BBCA" / "2025").mkdir(parents=True)

    scan = scan_staging(staging)

    assert staging.is_dir(), "the staging root itself was removed"
    assert not (staging / "NCKL").exists()
    assert not (staging / "ITMG").exists()
    assert not (staging / "BBCA").exists(), "an already empty folder should be pruned"
    # Three stocks, each with a year folder: 6 directories in total.
    assert len(scan.empty_dirs_removed) == 6


def test_an_in_flight_download_keeps_its_directories(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    target = partial_report(staging / "NCKL" / "2025" / "NCKL_inlineXBRL_T1_2025.zip")
    Path(f"{target}.crdownload").write_bytes(b"\x00")

    scan_staging(staging)

    assert (staging / "NCKL" / "2025").is_dir(), "pruning ate a directory still in use"


def test_dry_run_reports_without_touching_anything(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    leftover = partial_report(staging / "NCKL" / "2025" / "NCKL_inlineXBRL_T1_2025.zip")

    scan = scan_staging(staging, dry_run=True)

    assert scan.removed == [leftover]
    assert scan.empty_dirs_removed == [], "dry_run must not claim to have removed folders"
    assert leftover.exists(), "dry_run deleted a file"
    assert (staging / "NCKL" / "2025").is_dir()


def test_the_summary_line_names_what_happened(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    assert describe(scan_staging(staging)) == "staging bersih"

    partial_report(staging / "NCKL" / "2025" / "NCKL_inlineXBRL_T1_2025.zip")
    target = partial_report(staging / "ITMG" / "2025" / "ITMG_inlineXBRL_T1_2025.zip")
    Path(f"{target}.crdownload").write_bytes(b"\x00")

    summary = describe(scan_staging(staging, dry_run=True))

    assert "1 file basi dibuang" in summary
    assert "1 unduhan berjalan dipertahankan" in summary


def test_a_second_scan_of_a_cleaned_folder_finds_nothing(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    partial_report(staging / "NCKL" / "2025" / "NCKL_inlineXBRL_T1_2025.zip")

    first = scan_staging(staging)
    second = scan_staging(staging)

    assert len(first.removed) == 1
    assert second.clean is True
