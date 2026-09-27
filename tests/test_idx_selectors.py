"""Semantic selectors must never target the wrong IDX control."""

from __future__ import annotations

from typing import Any

from firefox_bridge.idx.selectors import (
    find_dropdown_option_ref,
    find_laporan_keuangan_ref,
    find_year_dropdown_ref,
    selected_year,
    snapshot_elements,
)

from conftest import make_snapshot


def test_find_year_dropdown_ref_ignores_company_and_period_controls() -> None:
    snapshot = make_snapshot(
        [
            {"ref": "e19", "role": "combobox", "name": "Search for option"},
            {"ref": "e20", "role": "searchbox", "name": "Search Company Code"},
            {"ref": "e32", "role": "combobox", "name": "Search for option"},
            {"ref": "e33", "role": "searchbox", "name": "TW2 Loading..."},
            {"ref": "e34", "role": "combobox", "name": "Search for option"},
            {"ref": "e35", "role": "searchbox", "name": "2026 Loading..."},
        ]
    )

    assert find_year_dropdown_ref(snapshot) == {
        "ref": "e35",
        "role": "searchbox",
        "name": "2026 Loading...",
    }


def test_find_year_dropdown_ref_has_no_generic_combobox_fallback() -> None:
    snapshot = make_snapshot(
        [{"ref": "e19", "role": "combobox", "name": "Search for option"}]
    )

    assert find_year_dropdown_ref(snapshot) is None


def test_find_year_dropdown_ref_ignores_malformed_snapshot() -> None:
    assert find_year_dropdown_ref({"elements": None}) is None
    assert snapshot_elements({"elements": [None, "x", {"ref": "e1"}]}) == [{"ref": "e1"}]


def test_find_dropdown_option_ref_rejects_ambiguous_options() -> None:
    snapshot = make_snapshot(
        [
            {"ref": "e20", "role": "option", "name": "2025"},
            {"ref": "e37", "role": "option", "name": "2025"},
        ]
    )

    assert find_dropdown_option_ref(snapshot, "2025") is None


def test_find_dropdown_option_ref_returns_single_exact_match() -> None:
    snapshot = make_snapshot(
        [
            {"ref": "e36", "role": "option", "name": "2024"},
            {"ref": "e38", "role": "option", "name": " 2025 "},
        ]
    )

    assert find_dropdown_option_ref(snapshot, "2025") == {
        "ref": "e38",
        "role": "option",
        "name": " 2025 ",
    }


def test_find_laporan_keuangan_ref_requires_exact_label() -> None:
    snapshot = make_snapshot(
        [
            {"ref": "e26", "role": "button", "name": "Laporan Keuangan 2024"},
            {"ref": "e27", "role": "link", "name": "Laporan Keuangan"},
            {"ref": "e28", "role": "button", "name": " Laporan Keuangan "},
        ]
    )

    assert find_laporan_keuangan_ref(snapshot) == "e28"


def test_find_laporan_keuangan_ref_returns_none_when_absent() -> None:
    assert find_laporan_keuangan_ref(make_snapshot([])) is None


def test_selected_year_reads_only_year_searchboxes() -> None:
    assert selected_year({"name": "2025 Loading..."}) == 2025
    assert selected_year({"name": "TW2 Loading..."}) is None
    assert selected_year(None) is None


def test_selected_year_requires_a_four_digit_year() -> None:
    element: dict[str, Any] = {"name": "199 Loading..."}
    assert selected_year(element) is None
