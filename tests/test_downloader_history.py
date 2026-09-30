"""History JSON, filesystem layout, and integrity verification."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from firefox_bridge.downloader import paths
from firefox_bridge.downloader.filesystem import (
    is_download_complete,
    move_completed_download,
    wait_for_completed_download,
)
from firefox_bridge.downloader.hashing import file_sha256, hash_matches_record
from firefox_bridge.downloader.history import (
    empty_history,
    history_entry,
    history_record,
    load_download_history,
    record_download_history,
    save_download_history,
)
from firefox_bridge.downloader.integrity import audit_stock_year_hashes
from firefox_bridge.downloader.models import (
    INTEGRITY_MISMATCH,
    INTEGRITY_VERIFIED,
    DownloadRecord,
)
from firefox_bridge.downloader.paths import (
    download_exists,
    download_history_path,
    final_report_path,
    resolve_output_path,
    staging_relative_filename,
    staging_report_path,
)

CONTENT = b"PK\x03\x04test"


def test_resolve_output_path_is_anti_duplicate(tmp_path: Path) -> None:
    folder, relative = resolve_output_path("nckl", 2025, 1, tmp_path)

    assert folder == tmp_path / "saham" / "NCKL" / "2025"
    assert folder.is_dir()
    assert relative == "saham/NCKL/2025/NCKL_inlineXBRL_T1_2025.zip"
    assert (folder / Path(relative).name).name == "NCKL_inlineXBRL_T1_2025.zip"


def test_staging_path_avoids_leading_dot(tmp_path: Path) -> None:
    relative = staging_relative_filename("NCKL", 2025, 4)

    assert relative == "saham/staging/NCKL/2025/NCKL_inlineXBRL_T4_2025.zip"
    assert not relative.startswith(".")
    assert staging_report_path("NCKL", 2025, 4, tmp_path) == tmp_path / relative


def test_download_history_path_and_download_exists(tmp_path: Path) -> None:
    assert download_history_path(tmp_path) == tmp_path / "saham" / "download_history.json"
    assert not download_exists("NCKL", 2025, 1, tmp_path)

    path = final_report_path("NCKL", 2025, 1, tmp_path)
    path.write_bytes(b"")

    assert not download_exists("NCKL", 2025, 1, tmp_path)

    path.write_bytes(CONTENT)

    assert download_exists("NCKL", 2025, 1, tmp_path)


def test_download_root_uses_environment_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(paths.DOWNLOAD_DIR_ENV, str(tmp_path))

    assert paths.default_download_dir() == tmp_path
    assert paths.download_root(None) == tmp_path
    assert paths.download_root(tmp_path / "other") == tmp_path / "other"


def test_empty_history_and_missing_file(tmp_path: Path) -> None:
    assert load_download_history(tmp_path) == empty_history()
    assert not download_history_path(tmp_path).exists()


def test_load_download_history_rejects_broken_json(tmp_path: Path) -> None:
    path = download_history_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"version": 1}', encoding="utf-8")

    with pytest.raises(ValueError):
        load_download_history(tmp_path)


def test_save_download_history_is_atomic(tmp_path: Path) -> None:
    history = empty_history()
    path = save_download_history(history, tmp_path)

    assert path.is_file()
    assert not path.with_suffix(".json.tmp").exists()
    assert json.loads(path.read_text(encoding="utf-8")) == history


def test_history_entry_and_record_typed_views(tmp_path: Path) -> None:
    history = empty_history()
    target = final_report_path("NCKL", 2025, 2, tmp_path)
    target.write_bytes(CONTENT)
    record_download_history(
        history,
        "nckl",
        2025,
        2,
        "https://idx.test/TW2/NCKL/inlineXBRL.zip",
        target,
        tmp_path,
    )

    assert history_entry(history, "NCKL", 2025, 2) is not None
    assert history_entry(history, "NCKL", 2025, 3) is None
    assert history_entry(history, "BBCA", 2025, 2) is None

    record = history_record(history, "NCKL", 2025, 2)
    assert record is not None
    assert record.size == len(CONTENT)
    assert record.integrity_status == INTEGRITY_VERIFIED
    assert record.duplicate_of is None
    assert record.has_hash


def test_download_record_from_entry_tolerates_missing_fields() -> None:
    record = DownloadRecord.from_entry({"url": "u", "duplicate_of": "2"})

    assert record.duplicate_of == 2
    assert record.size == 0
    assert not record.has_hash


def test_file_sha256_matches_hashlib(tmp_path: Path) -> None:
    path = tmp_path / "report.zip"
    path.write_bytes(CONTENT)

    assert file_sha256(path) == hashlib.sha256(CONTENT).hexdigest()
    assert hash_matches_record(path, file_sha256(path))
    assert hash_matches_record(path, "")
    assert not hash_matches_record(path, "0" * 64)


def test_record_download_history_stores_hash_and_size(tmp_path: Path) -> None:
    history = empty_history()
    target = final_report_path("NCKL", 2025, 1, tmp_path)
    target.write_bytes(CONTENT)

    path = record_download_history(
        history,
        "NCKL",
        2025,
        1,
        "https://idx.test/TW1/NCKL/inlineXBRL.zip",
        target,
        tmp_path,
    )
    saved = json.loads(path.read_text(encoding="utf-8"))
    entry = saved["downloads"]["NCKL"]["2025"]["1"]

    assert entry["file"] == "saham/NCKL/2025/NCKL_inlineXBRL_T1_2025.zip"
    assert entry["size"] == len(CONTENT)
    assert entry["sha256"] == hashlib.sha256(CONTENT).hexdigest()
    assert entry["duplicate_of"] is None
    assert entry["integrity_status"] == INTEGRITY_VERIFIED
    assert entry["completed_at"]


def test_record_download_history_keeps_absolute_path_outside_root(
    tmp_path: Path,
) -> None:
    history = empty_history()
    download_root = tmp_path / "root"
    outside = tmp_path / "outside.zip"
    outside.write_bytes(CONTENT)

    record_download_history(history, "NCKL", 2025, 1, "u", outside, download_root)

    assert history["downloads"]["NCKL"]["2025"]["1"]["file"] == str(outside.resolve())


def test_hash_audit_detects_duplicate_quarters(tmp_path: Path) -> None:
    history = load_download_history(tmp_path)
    contents = {
        1: b"same report",
        2: b"same report",
        3: b"different tw3",
        4: b"different audit",
    }
    periods = {1: "TW1", 2: "TW2", 3: "TW3", 4: "Audit"}

    for quarter, content in contents.items():
        path = final_report_path("NCKL", 2025, quarter, tmp_path)
        path.write_bytes(content)
        record_download_history(
            history,
            "NCKL",
            2025,
            quarter,
            f"https://idx.test/{periods[quarter]}/NCKL/inlineXBRL.zip",
            path,
            tmp_path,
        )

    hashes = audit_stock_year_hashes("NCKL", 2025, tmp_path)
    saved = json.loads(download_history_path(tmp_path).read_text(encoding="utf-8"))
    quarters = saved["downloads"]["NCKL"]["2025"]

    assert hashes[1] == hashes[2]
    assert quarters["1"]["duplicate_of"] is None
    assert quarters["2"]["duplicate_of"] == 1
    assert quarters["3"]["duplicate_of"] is None
    assert quarters["4"]["duplicate_of"] is None
    assert all(
        quarters[str(quarter)]["integrity_status"] == INTEGRITY_VERIFIED
        for quarter in range(1, 5)
    )


def test_hash_audit_backfills_a_missing_hash(tmp_path: Path) -> None:
    history = load_download_history(tmp_path)
    path = final_report_path("NCKL", 2025, 1, tmp_path)
    path.write_bytes(CONTENT)
    history["downloads"] = {
        "NCKL": {"2025": {"1": {"url": "u", "file": "f", "size": len(CONTENT)}}}
    }
    save_download_history(history, tmp_path)

    hashes = audit_stock_year_hashes("NCKL", 2025, tmp_path)
    saved = json.loads(download_history_path(tmp_path).read_text(encoding="utf-8"))
    entry = saved["downloads"]["NCKL"]["2025"]["1"]

    assert entry["sha256"] == hashes[1]
    assert entry["integrity_status"] == INTEGRITY_VERIFIED


def test_hash_audit_marks_a_corrupted_file(tmp_path: Path) -> None:
    history = empty_history()
    path = final_report_path("NCKL", 2025, 1, tmp_path)
    path.write_bytes(CONTENT)
    record_download_history(history, "NCKL", 2025, 1, "u", path, tmp_path)
    path.write_bytes(b"corrupted payload")

    audit_stock_year_hashes("NCKL", 2025, tmp_path)
    saved = json.loads(download_history_path(tmp_path).read_text(encoding="utf-8"))

    assert saved["downloads"]["NCKL"]["2025"]["1"]["integrity_status"] == INTEGRITY_MISMATCH


def test_hash_audit_clears_a_stale_duplicate_flag(tmp_path: Path) -> None:
    history = empty_history()
    for quarter, content in ((1, b"one"), (2, b"two")):
        path = final_report_path("NCKL", 2025, quarter, tmp_path)
        path.write_bytes(content)
        record_download_history(history, "NCKL", 2025, quarter, "u", path, tmp_path)
    history["downloads"]["NCKL"]["2025"]["2"]["duplicate_of"] = 1
    save_download_history(history, tmp_path)

    audit_stock_year_hashes("NCKL", 2025, tmp_path)
    saved = json.loads(download_history_path(tmp_path).read_text(encoding="utf-8"))

    assert saved["downloads"]["NCKL"]["2025"]["2"]["duplicate_of"] is None


def test_hash_audit_ignores_files_without_json_entries(tmp_path: Path) -> None:
    final_report_path("NCKL", 2025, 1, tmp_path).write_bytes(CONTENT)

    hashes = audit_stock_year_hashes("NCKL", 2025, tmp_path)

    assert set(hashes) == {1}
    assert not download_history_path(tmp_path).exists()


def test_is_download_complete_requires_a_stable_non_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "report.zip"

    assert not is_download_complete(path)

    path.write_bytes(b"")
    assert not is_download_complete(path)

    path.write_bytes(CONTENT)
    assert is_download_complete(path)

    (tmp_path / "report.zip.crdownload").write_bytes(b"x")
    assert not is_download_complete(path)


def test_wait_for_completed_download_returns_size(no_sleep: None, tmp_path: Path) -> None:
    path = tmp_path / "report.zip"
    path.write_bytes(CONTENT)

    assert wait_for_completed_download(path) == len(CONTENT)


def test_wait_for_completed_download_times_out(no_sleep: None, tmp_path: Path) -> None:
    with pytest.raises(TimeoutError):
        wait_for_completed_download(tmp_path / "missing.zip", timeout=0.01)


def test_move_completed_download_replaces_only_when_asked(tmp_path: Path) -> None:
    source = tmp_path / "staging.zip"
    destination = tmp_path / "final" / "report.zip"
    source.write_bytes(CONTENT)
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"old payload")

    with pytest.raises(FileExistsError):
        move_completed_download(source, destination)

    move_completed_download(source, destination, replace=True)

    assert destination.read_bytes() == CONTENT
    assert not source.exists()


def test_move_completed_download_drops_an_identical_destination(
    tmp_path: Path,
) -> None:
    source = tmp_path / "staging.zip"
    destination = tmp_path / "report.zip"
    source.write_bytes(CONTENT)
    destination.write_bytes(CONTENT)

    move_completed_download(source, destination)

    assert destination.read_bytes() == CONTENT
    assert not source.exists()


def test_move_completed_download_replaces_an_empty_destination(
    tmp_path: Path,
) -> None:
    source = tmp_path / "staging.zip"
    destination = tmp_path / "report.zip"
    source.write_bytes(CONTENT)
    destination.write_bytes(b"")

    move_completed_download(source, destination)

    assert destination.read_bytes() == CONTENT


def test_recorded_entry_survives_a_reload(tmp_path: Path) -> None:
    history = empty_history()
    path = final_report_path("NCKL", 2025, 1, tmp_path)
    path.write_bytes(CONTENT)
    record_download_history(history, "NCKL", 2025, 1, "u", path, tmp_path)

    reloaded: dict[str, Any] = load_download_history(tmp_path)

    assert history_entry(reloaded, "NCKL", 2025, 1) == history_entry(
        history,
        "NCKL",
        2025,
        1,
    )


def _complete_stock(history: dict[str, Any], tmp_path: Path, stock: str = "NCKL") -> None:
    """Record all four quarters of 2025 with real files on disk."""
    for quarter in (1, 2, 3, 4):
        path = final_report_path(stock, 2025, quarter, tmp_path)
        path.write_bytes(CONTENT)
        record_download_history(history, stock, 2025, quarter, "u", path, tmp_path)


def test_stock_year_complete_when_all_quarters_valid(tmp_path: Path) -> None:
    from firefox_bridge.downloader.history import stock_year_complete

    history = empty_history()
    _complete_stock(history, tmp_path)

    assert stock_year_complete(history, "nckl", 2025, tmp_path) is True


def test_stock_year_complete_false_without_any_history(tmp_path: Path) -> None:
    from firefox_bridge.downloader.history import stock_year_complete

    assert stock_year_complete(empty_history(), "NCKL", 2025, tmp_path) is False


def test_stock_year_complete_false_when_a_file_is_missing(tmp_path: Path) -> None:
    from firefox_bridge.downloader.history import stock_year_complete

    history = empty_history()
    _complete_stock(history, tmp_path)
    final_report_path("NCKL", 2025, 3, tmp_path).unlink()

    assert stock_year_complete(history, "NCKL", 2025, tmp_path) is False


def test_stock_year_complete_false_when_a_hash_changed(tmp_path: Path) -> None:
    from firefox_bridge.downloader.history import stock_year_complete

    history = empty_history()
    _complete_stock(history, tmp_path)
    final_report_path("NCKL", 2025, 2, tmp_path).write_bytes(b"corrupted!")

    assert stock_year_complete(history, "NCKL", 2025, tmp_path) is False


def test_stock_year_complete_checks_recorded_quarters_only(tmp_path: Path) -> None:
    from firefox_bridge.downloader.history import stock_year_complete

    history = empty_history()
    path = final_report_path("NCKL", 2025, 1, tmp_path)
    path.write_bytes(CONTENT)
    record_download_history(history, "NCKL", 2025, 1, "u", path, tmp_path)
    # Satu kuartal tercatat dan valid -> lengkap menurut definisi
    # (semua yang tercatat valid); nol kuartal -> tidak lengkap.
    assert stock_year_complete(history, "NCKL", 2025, tmp_path) is True
    assert stock_year_complete(history, "BBCA", 2025, tmp_path) is False
