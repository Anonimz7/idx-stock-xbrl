"""Report link parsing rules for IDX report URLs."""

from __future__ import annotations

import pytest
from firefox_bridge.idx.link_parser import (
    find_inline_xbrl_link,
    find_inline_xbrl_links,
    is_report_link,
    quarter_from_report_href,
    report_links,
    year_path_markers,
)

from conftest import make_snapshot

BASE = (
    "https://www.idx.co.id/Portals/0/StaticData/ListedCompanies/"
    "Corporate_Actions/New_Info_JSX/Jenis_Informasi/"
    "01_Laporan_Keuangan/02_Soft_Copy_Laporan_Keuangan/"
    "//Laporan%20Keuangan%20Tahun%202025"
)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/TW1/NCKL/inlineXBRL.zip", 1),
        ("/TW2/NCKL/inlineXBRL.zip", 2),
        ("/TW3/NCKL/inlineXBRL.zip", 3),
        ("/TW4/NCKL/inlineXBRL.zip", 4),
        ("/Audit/NCKL/inlineXBRL.zip", 4),
        ("/audit/nckl/inlineXBRL.zip", 4),
        ("/TW5/NCKL/inlineXBRL.zip", None),
        ("/NCKL/inlineXBRL.zip", None),
    ],
)
def test_quarter_from_report_href(path: str, expected: int | None) -> None:
    assert quarter_from_report_href(f"{BASE}{path}") == expected


def test_year_path_markers_accept_encoded_and_plain_spacing() -> None:
    encoded, plain = year_path_markers(2025)

    assert encoded == "tahun%202025"
    assert plain == "tahun 2025"
    assert is_report_link(f"{BASE}/TW1/NCKL/inlineXBRL.zip", 2025)
    assert not is_report_link(f"{BASE}/TW1/NCKL/laporan.pdf", 2025)
    assert not is_report_link(f"{BASE}/TW1/NCKL/inlineXBRL.zip", 2024)


def test_find_inline_xbrl_links_returns_all_periods_including_audit() -> None:
    snapshot = make_snapshot(
        [
            {"ref": "e3", "href": f"{BASE}/TW3/NCKL/inlineXBRL.zip"},
            {"ref": "e1", "href": f"{BASE}/TW1/NCKL/inlineXBRL.zip"},
            {"ref": "e4", "href": f"{BASE}/Audit/NCKL/inlineXBRL.zip"},
            {"ref": "e2", "href": f"{BASE}/TW2/NCKL/inlineXBRL.zip"},
            {"ref": "e5", "href": f"{BASE}/TW1/NCKL/laporan.pdf"},
        ]
    )

    links = find_inline_xbrl_links(snapshot, 2025)

    assert [link["ref"] for link in links] == ["e1", "e2", "e3", "e4"]
    assert quarter_from_report_href(links[3]["href"]) == 4


def test_find_inline_xbrl_links_keeps_first_node_per_quarter() -> None:
    snapshot = make_snapshot(
        [
            {"ref": "e1", "href": f"{BASE}/TW1/NCKL/inlineXBRL.zip"},
            {"ref": "e9", "href": f"{BASE}/TW1/NCKL/inlineXBRL.zip"},
        ]
    )

    assert [link["ref"] for link in find_inline_xbrl_links(snapshot, 2025)] == ["e1"]


def test_find_inline_xbrl_links_ignores_other_years() -> None:
    other = BASE.replace("2025", "2024")
    snapshot = make_snapshot([{"ref": "e1", "href": f"{other}/TW1/NCKL/inlineXBRL.zip"}])

    assert find_inline_xbrl_links(snapshot, 2025) == []


def test_find_inline_xbrl_link_selects_one_quarter() -> None:
    snapshot = make_snapshot(
        [
            {"ref": "e1", "href": f"{BASE}/TW1/NCKL/inlineXBRL.zip"},
            {"ref": "e4", "href": f"{BASE}/Audit/NCKL/inlineXBRL.zip"},
        ]
    )

    assert find_inline_xbrl_link(snapshot, 2025, 4) == {
        "ref": "e4",
        "href": f"{BASE}/Audit/NCKL/inlineXBRL.zip",
    }
    assert find_inline_xbrl_link(snapshot, 2025, 3) is None


def test_report_links_returns_typed_models_in_quarter_order() -> None:
    snapshot = make_snapshot(
        [
            {"ref": "e4", "href": f"{BASE}/Audit/NCKL/inlineXBRL.zip"},
            {"ref": "e1", "href": f"{BASE}/TW1/NCKL/inlineXBRL.zip"},
        ]
    )

    links = report_links(snapshot, 2025)

    assert [link.quarter for link in links] == [1, 4]
    assert [link.period_label for link in links] == ["TW1", "Audit"]
    assert links[0].element["ref"] == "e1"
