"""Direct instance.zip URL construction and validation.

The four URLs in the module docstring of the source data are the specification:
this program exists to reproduce them for any ticker, so the exact strings are
asserted rather than re-derived from the template. A template that agrees with
itself proves nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from firefox_bridge.instance import (
    AUDITED_QUARTER,
    instance_download_dir,
    instance_final_path,
    instance_report_filename,
    instance_staging_path,
    instance_staging_relative_filename,
    instance_staging_root,
    instance_url,
    validate_instance_url,
)
from firefox_bridge.validation import ValidationError

BASE = (
    "https://www.idx.co.id/Portals/0/StaticData/ListedCompanies/"
    "Corporate_Actions/New_Info_JSX/Jenis_Informasi/"
    "01_Laporan_Keuangan/02_Soft_Copy_Laporan_Keuangan//"
)


def _expected(stock: str, year: int) -> str:
    return f"{BASE}Laporan%20Keuangan%20Tahun%20{year}/Audit/{stock}/instance.zip"


@pytest.mark.parametrize("stock", ["NCKL", "BBCA", "UNTR", "DADA", "BBRI"])
def test_instance_url_matches_the_published_pattern(stock: str) -> None:
    assert instance_url(stock, 2025) == _expected(stock, 2025)


@pytest.mark.parametrize("stock", ["NCKL", "BBCA", "UNTR", "DADA"])
def test_instance_url_reproduces_the_supplied_links_verbatim(stock: str) -> None:
    """The exact strings from the request, not a reformatted equivalent."""
    supplied = (
        "https://www.idx.co.id/Portals/0/StaticData/ListedCompanies/"
        "Corporate_Actions/New_Info_JSX/Jenis_Informasi/"
        "01_Laporan_Keuangan/02_Soft_Copy_Laporan_Keuangan//"
        f"Laporan%20Keuangan%20Tahun%202025/Audit/{stock}/instance.zip"
    )
    assert instance_url(stock, 2025) == supplied


def test_instance_url_uppercases_the_stock_folder() -> None:
    assert instance_url("nckl", 2025) == _expected("NCKL", 2025)


def test_audited_quarter_is_four() -> None:
    """Both programs must agree that the audited annual period is quarter 4."""
    assert AUDITED_QUARTER == 4


def test_validate_accepts_what_instance_url_builds() -> None:
    href = instance_url("NCKL", 2025)
    assert validate_instance_url(href, "NCKL", 2025) == href


@pytest.mark.parametrize(
    ("href", "stock", "year"),
    [
        ("", "NCKL", 2025),
        ("   ", "NCKL", 2025),
        ("javascript:alert(1)", "NCKL", 2025),
        ("https://evil.example/Audit/NCKL/instance.zip", "NCKL", 2025),
        ("https://idx.co.id.evil.example/Audit/NCKL/instance.zip", "NCKL", 2025),
        # Right host, wrong content: the page flow's archive must not be claimed
        # by this program.
        (_expected("NCKL", 2025).replace("instance.zip", "inlineXBRL.zip"), "NCKL", 2025),
        # Interim periods are not published as instance.zip.
        (_expected("NCKL", 2025).replace("/Audit/", "/TW1/"), "NCKL", 2025),
        # Right file, wrong year: must not be accepted for a different request.
        (_expected("NCKL", 2025), "NCKL", 2024),
        # Right file, wrong issuer: must not be filed under the wrong folder.
        (_expected("NCKL", 2025), "BBCA", 2025),
        # Not a ZIP at all.
        (_expected("NCKL", 2025).replace(".zip", ".pdf"), "NCKL", 2025),
    ],
)
def test_validate_rejects_urls_this_program_must_not_fetch(
    href: str, stock: str, year: int
) -> None:
    with pytest.raises(ValidationError):
        validate_instance_url(href, stock, year)


def test_validate_allows_the_bare_idx_host() -> None:
    href = instance_url("NCKL", 2025).replace("www.idx.co.id", "idx.co.id")
    assert validate_instance_url(href, "NCKL", 2025) == href


def test_report_filename_is_not_mistaken_for_inline_xbrl() -> None:
    """The two archives must not produce the same path.

    A shared name would let one program overwrite the other's file while both
    believed they were looking at their own download.
    """
    name = instance_report_filename("NCKL", 2025)
    assert name == "NCKL_instance_T4_2025.zip"
    assert "inlineXBRL" not in name


def test_final_path_sits_under_the_saham_layout() -> None:
    path = instance_final_path("NCKL", 2025, "C:/root")
    assert path.as_posix().endswith("saham/NCKL/2025/NCKL_instance_T4_2025.zip")


def test_staging_path_is_relative_to_the_root_not_absolute() -> None:
    rel = instance_staging_relative_filename("NCKL", 2025)
    assert rel == "saham/staging/NCKL/2025/NCKL_instance_T4_2025.zip"
    assert not rel.startswith("/")


def test_staging_is_anchored_to_firefoxs_root_not_the_instance_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Staging must follow where the browser writes, not where this program reads.

    ``browser.downloads.download`` resolves ``filename`` against Firefox's own
    download directory, which is the page flow's root. Anchoring staging to
    ``<root>/instance`` would aim ``scan_staging`` at a folder Firefox never
    creates, so it would report "bersih" while a partial file waited elsewhere
    for the completion check to mistake for a finished download. Measured on a
    real run: ``<root>/instance/saham/staging`` did not exist at all.
    """
    monkeypatch.setenv("FIREFOX_BRIDGE_DOWNLOAD_DIR", str(tmp_path / "page-flow"))
    monkeypatch.delenv("FIREFOX_BRIDGE_INSTANCE_DIR", raising=False)

    root = instance_staging_root()

    assert root == tmp_path / "page-flow" / "saham" / "staging"
    assert instance_staging_path("NCKL", 2025).is_relative_to(tmp_path / "page-flow")
    assert not root.is_relative_to(instance_download_dir())


def test_default_download_dir_is_separate_from_the_page_flow(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A shared root would let the two histories overwrite each other.

    History is keyed by (stock, year, quarter) with no archive column, so the
    second program to write a key would leave the first with a hash it cannot
    reproduce -- and re-downloading on every subsequent run.
    """
    monkeypatch.delenv("FIREFOX_BRIDGE_INSTANCE_DIR", raising=False)
    monkeypatch.setenv("FIREFOX_BRIDGE_DOWNLOAD_DIR", str(tmp_path))

    assert instance_download_dir() == tmp_path / "instance"


def test_explicit_download_dir_wins_over_everything(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("FIREFOX_BRIDGE_INSTANCE_DIR", str(tmp_path / "env"))
    monkeypatch.setenv("FIREFOX_BRIDGE_DOWNLOAD_DIR", str(tmp_path / "other"))

    assert instance_download_dir(tmp_path / "explicit") == tmp_path / "explicit"


def test_env_override_wins_over_the_composed_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("FIREFOX_BRIDGE_INSTANCE_DIR", str(tmp_path / "chosen"))
    monkeypatch.setenv("FIREFOX_BRIDGE_DOWNLOAD_DIR", str(tmp_path / "base"))

    assert instance_download_dir() == tmp_path / "chosen"
