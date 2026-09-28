"""The three untrusted inputs on the way from a web page to a file on disk.

Each test here corresponds to a place where text becomes a real filesystem
operation. They are written as attack cases rather than happy paths, because
the happy path was already covered and it is the unhappy one that writes files
where nobody expected them.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from firefox_bridge.downloader.paths import final_report_path, staging_relative_filename
from firefox_bridge.idx.link_parser import is_report_link, quarter_from_report_href
from firefox_bridge.validation import (
    ValidationError,
    normalize_stock_code,
    validate_archive,
    validate_report_url,
)

# --- SEC-003: stock code -------------------------------------------------

REAL_CODES = ["NCKL", "BBCA", "BBRI", "TLKM", "ASII", "ANTM", "ITMG", "PGAS", "SMGR"]


@pytest.mark.parametrize("code", REAL_CODES)
def test_real_stock_codes_are_accepted(code: str) -> None:
    assert normalize_stock_code(code) == code


def test_lowercase_input_is_normalized_not_rejected() -> None:
    assert normalize_stock_code("nckl") == "NCKL"
    assert normalize_stock_code("  nckl  ") == "NCKL"


@pytest.mark.parametrize(
    "hostile",
    [
        "..",
        "../..",
        "NCKL/../../Windows",
        "NCKL\\..\\..\\evil",
        "NC KL",
        "NCKL;rm -rf",
        "NCKL\x00",
        "NC.KL",
        "NC-KL",
        "saham/NCKL",
        "/absolute",
        "C:\\Windows",
        "NCKL%2F..%2F",
    ],
)
def test_codes_that_could_escape_the_download_root_are_rejected(hostile: str) -> None:
    with pytest.raises(ValidationError):
        normalize_stock_code(hostile)


@pytest.mark.parametrize("name", ["CON", "PRN", "AUX", "NUL", "COM1", "LPT9"])
def test_windows_device_names_are_rejected(name: str) -> None:
    """These fail at the OS level, long after we chose the folder name."""
    with pytest.raises(ValidationError, match="Windows"):
        normalize_stock_code(name)


def test_empty_and_overlong_codes_are_rejected() -> None:
    with pytest.raises(ValidationError):
        normalize_stock_code("")
    with pytest.raises(ValidationError, match="terlalu panjang"):
        normalize_stock_code("A" * 11)


def test_paths_module_refuses_to_build_a_traversing_path(tmp_path: Path) -> None:
    """`paths` is the choke point every download goes through, not just the CLI."""
    with pytest.raises(ValidationError):
        final_report_path("../../pwned", 2025, 1, tmp_path)
    with pytest.raises(ValidationError):
        staging_relative_filename("../escape", 2025, 1)


# --- SEC-004: IDX URL allowlist -----------------------------------------

IDX = "https://www.idx.co.id/Portals/0/StaticData/ListedCompanies/x"


def valid_url(quarter: int = 1, stock: str = "NCKL", year: int = 2025) -> str:
    segment = "Audit" if quarter == 4 else f"TW{quarter}"
    return f"{IDX}/Laporan%20Keuangan%20Tahun%20{year}/{segment}/{stock}/inlineXBRL.zip"


def test_the_real_idx_url_is_accepted() -> None:
    for quarter in (1, 2, 3, 4):
        assert validate_report_url(valid_url(quarter), "NCKL", 2025, quarter)


@pytest.mark.parametrize(
    "hostile",
    [
        "https://evil.example/Portals/0/Laporan%20Keuangan%20Tahun%202025/TW1/NCKL/inlineXBRL.zip",
        "http://idx.co.id.evil.example/Laporan%20Keuangan%20Tahun%202025/TW1/NCKL/inlineXBRL.zip",
        "https://www.idx.co.id.evil.example/TW1/NCKL/inlineXBRL.zip",
        "//www.idx.co.id/TW1/NCKL/inlineXBRL.zip",
        "file:///C:/Windows/System32/inlineXBRL.zip",
        "ftp://www.idx.co.id/Laporan%20Keuangan%20Tahun%202025/TW1/NCKL/inlineXBRL.zip",
        "javascript:alert(1)//inlineXBRL.zip",
        "data:text/plain,inlineXBRL.zip",
    ],
)
def test_off_idx_urls_are_rejected(hostile: str) -> None:
    with pytest.raises(ValidationError):
        validate_report_url(hostile, "NCKL", 2025, 1)


def test_url_for_another_stock_is_rejected() -> None:
    """IDX hosts every company's reports, so the stock segment must match."""
    with pytest.raises(ValidationError, match="saham lain"):
        validate_report_url(valid_url(1, stock="BBCA"), "NCKL", 2025, 1)


def test_url_for_another_year_is_rejected() -> None:
    with pytest.raises(ValidationError, match="tahun"):
        validate_report_url(valid_url(1, year=2024), "NCKL", 2025, 1)


def test_quarter_must_match_the_requested_one() -> None:
    with pytest.raises(ValidationError):
        validate_report_url(valid_url(1), "NCKL", 2025, 3)
    with pytest.raises(ValidationError, match="Audit"):
        validate_report_url(valid_url(1), "NCKL", 2025, 4)


def test_tw4_must_come_from_the_audit_path() -> None:
    """TW4 is only ever published under /Audit/, so a /TW4/ URL is not IDX's."""
    forged = f"{IDX}/Laporan%20Keuangan%20Tahun%202025/TW4/NCKL/inlineXBRL.zip"
    with pytest.raises(ValidationError, match="Audit"):
        validate_report_url(forged, "NCKL", 2025, 4)


def test_non_zip_urls_are_rejected() -> None:
    with pytest.raises(ValidationError):
        validate_report_url(f"{IDX}/Laporan%20Keuangan%20Tahun%202025/TW1/NCKL/report.pdf", "NCKL", 2025, 1)


def test_empty_url_is_rejected() -> None:
    with pytest.raises(ValidationError):
        validate_report_url("", "NCKL", 2025, 1)


def test_link_parser_no_longer_recognizes_off_host_links() -> None:
    """Recognition is a decision about where a link points, not what it is named.

    The name-based fragments were all present in this URL; only the host differs.
    """
    lookalike = (
        "https://evil.example/Portals/0/StaticData/"
        "Laporan%20Keuangan%20Tahun%202025/TW1/NCKL/inlineXBRL.zip"
    )
    assert "tahun%202025" in lookalike.lower()
    assert "inlinexbrl.zip" in lookalike.lower()
    assert is_report_link(lookalike, 2025) is False
    assert is_report_link(valid_url(1), 2025) is True


def test_quarter_parsing_still_works_for_real_urls() -> None:
    assert quarter_from_report_href(valid_url(1)) == 1
    assert quarter_from_report_href(valid_url(4)) == 4


# --- SEC-005: archive validation ----------------------------------------


def real_zip(entries: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        # `entries is not None`, not `entries or ...`: an empty dict is the case
        # one of these tests is trying to build, and a falsy default would
        # silently hand it a populated archive instead.
        for name, payload in (entries if entries is not None else {"a.txt": b"payload"}).items():
            info = zipfile.ZipInfo(name, date_time=(2025, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, payload)
    return buffer.getvalue()


def test_a_real_archive_passes_and_reports_its_size(tmp_path: Path) -> None:
    path = tmp_path / "report.zip"
    payload = real_zip()
    path.write_bytes(payload)

    assert validate_archive(path) == len(payload)


def test_a_non_zip_payload_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "report.zip"
    path.write_bytes(b"<html>404 Not Found</html>")

    with pytest.raises(ValidationError, match="bukan arsip ZIP"):
        validate_archive(path)


def test_a_truncated_archive_is_rejected(tmp_path: Path) -> None:
    """The realistic failure: a download that stopped halfway."""
    path = tmp_path / "report.zip"
    payload = real_zip()
    path.write_bytes(payload[: len(payload) // 2])

    with pytest.raises(ValidationError):
        validate_archive(path)


def test_an_empty_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "report.zip"
    path.write_bytes(b"")

    with pytest.raises(ValidationError, match="kosong"):
        validate_archive(path)


def test_a_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="tidak ditemukan"):
        validate_archive(tmp_path / "absent.zip")


def test_an_oversized_file_is_rejected_before_it_is_read(tmp_path: Path) -> None:
    path = tmp_path / "report.zip"
    path.write_bytes(real_zip())

    with pytest.raises(ValidationError, match="terlalu besar"):
        validate_archive(path, max_bytes=10)


def test_a_zip_bomb_is_rejected(tmp_path: Path) -> None:
    """Highly compressible content expands to orders of magnitude on disk."""
    path = tmp_path / "bomb.zip"
    path.write_bytes(real_zip({"big.txt": b"\0" * (8 * 1024 * 1024)}))

    with pytest.raises(ValidationError, match="kompresi"):
        validate_archive(path)


def test_an_empty_archive_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "report.zip"
    path.write_bytes(real_zip({}))

    with pytest.raises(ValidationError, match="tanpa entri"):
        validate_archive(path)
