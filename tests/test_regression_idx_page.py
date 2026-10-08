"""Regression guard for the captured IDX Laporan Keuangan page.

This fixture locks the validated behavior: one reusable profile tab, the
year-specific searchbox, and the four report links of one reporting year where
the fourth report lives under the IDX ``Audit`` path.
"""

from __future__ import annotations

from pathlib import Path

from firefox_bridge.downloader.paths import final_report_path, staging_relative_filename
from firefox_bridge.idx.link_parser import find_inline_xbrl_links, report_links
from firefox_bridge.idx.selectors import (
    find_laporan_keuangan_ref,
    find_year_dropdown_ref,
    selected_year,
)

REPORT_BASE = (
    "https://www.idx.co.id/Portals/0/StaticData/ListedCompanies/Corporate_Actions/"
    "New_Info_JSX/Jenis_Informasi/01_Laporan_Keuangan/02_Soft_Copy_Laporan_Keuangan"
    "//Laporan%20Keuangan%20Tahun%202025"
)
EXPECTED_HREFS = [
    f"{REPORT_BASE}/{period}/NCKL/inlineXBRL.zip"
    for period in ("TW1", "TW2", "TW3", "Audit")
]


def test_year_control_is_never_the_company_code_or_period(
    idx_snapshot: dict,
) -> None:
    year_control = find_year_dropdown_ref(idx_snapshot)

    assert year_control is not None
    assert year_control["name"].startswith("20")
    assert "Company Code" not in year_control["name"]
    assert not year_control["name"].startswith("TW")
    assert selected_year(year_control) == 2026


def test_laporan_keuangan_button_is_found_by_exact_label(
    idx_snapshot: dict,
) -> None:
    assert find_laporan_keuangan_ref(idx_snapshot) == "e27"


def test_captured_page_yields_four_reports_in_quarter_order(
    idx_snapshot: dict,
) -> None:
    links = report_links(idx_snapshot, 2025)

    assert [link.quarter for link in links] == [1, 2, 3, 4]
    assert [link.period_label for link in links] == ["TW1", "TW2", "TW3", "Audit"]
    assert [link.href for link in links] == EXPECTED_HREFS
    assert all(link.ref for link in links)


def test_pdf_links_are_never_treated_as_reports(
    idx_snapshot: dict,
) -> None:
    hrefs = [link["href"] for link in find_inline_xbrl_links(idx_snapshot, 2025)]

    assert not any(href.endswith(".pdf") for href in hrefs)


def test_captured_page_has_no_reports_for_another_year(
    idx_snapshot: dict,
) -> None:
    assert find_inline_xbrl_links(idx_snapshot, 2024) == []


def test_final_and_staging_paths_are_derived_per_quarter(tmp_path: Path) -> None:
    assert final_report_path("NCKL", 2025, 4, tmp_path) == (
        tmp_path / "saham" / "2025" / "NCKL" / "NCKL_inlineXBRL_T4_2025.zip"
    )
    assert staging_relative_filename("NCKL", 2025, 4) == (
        "saham/staging/2025/NCKL/NCKL_inlineXBRL_T4_2025.zip"
    )
