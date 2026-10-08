"""Reading the catalog the downloader now takes its list from.

The catalog replaced a command-line list of stock codes, which means it sits
where a bad value used to cost a usage error and now costs a run of 890. What
is pinned here is therefore mostly the refusal side: a file that is not a
catalog, an entry with no address, a year the file has never heard of -- each
of which has to stop before anything is fetched, with a message that says
which of the three went wrong.

The acceptance side is deliberately thin. Whether a URL is fetchable is
``validate_instance_url``'s verdict and is asserted there; repeating it here
would mean two tests that have to be kept in agreement about what "valid"
means, and only one of them would be right about why.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from firefox_bridge.instance.catalog import CatalogError, divergent, load_entries
from firefox_bridge.instance.urls import instance_url

YEAR = 2025


def _write(tmp_path: Path, entries: dict, name: str = "catalog.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps({"entries": entries}), encoding="utf-8")
    return path


def _entry(code: str, year: int = YEAR, url: str | None = None) -> dict:
    return {
        "ticker": code,
        "year": year,
        "url": url if url is not None else instance_url(code, year),
    }


class TestLoadEntries:
    def test_returns_pairs_sorted_by_code(self, tmp_path: Path) -> None:
        """Sorted, because the file is a dict and its order is whoever merged it.

        A run that walks codes in a stable order reports the same way every
        time, which is what makes a resumed run comparable to the one it
        resumed -- and an unsorted read would change between scans without
        anything in the scan having changed.
        """
        path = _write(
            tmp_path,
            {"2025|TLKM": _entry("TLKM"), "2025|AADI": _entry("AADI")},
        )

        assert load_entries(path, YEAR) == [
            ("AADI", instance_url("AADI", YEAR)),
            ("TLKM", instance_url("TLKM", YEAR)),
        ]

    def test_other_years_are_left_out(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            {
                "2024|NCKL": _entry("NCKL", 2024),
                "2025|NCKL": _entry("NCKL", YEAR),
            },
        )

        assert load_entries(path, YEAR) == [("NCKL", instance_url("NCKL", YEAR))]

    def test_a_missing_file_says_how_to_make_one(self, tmp_path: Path) -> None:
        """The file is produced by a different program, so "not found" alone
        is a dead end -- the reader has to name the command that builds it."""
        missing = tmp_path / "nope.json"

        with pytest.raises(CatalogError) as caught:
            load_entries(missing, YEAR)

        assert "idx_watcher" in str(caught.value)

    def test_invalid_json_is_reported_as_json(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")

        with pytest.raises(CatalogError, match="bukan JSON valid"):
            load_entries(path, YEAR)

    def test_a_file_without_entries_is_not_a_catalog(self, tmp_path: Path) -> None:
        path = tmp_path / "other.json"
        path.write_text(json.dumps({"results": []}), encoding="utf-8")

        with pytest.raises(CatalogError, match="bukan katalog"):
            load_entries(path, YEAR)

    def test_an_empty_year_names_the_years_it_does_have(self, tmp_path: Path) -> None:
        """"Nothing here" is useless; "here is what is here" is an answer.

        A stale fetch and a wrong ``--year`` look identical from the outside,
        and only one of them is fixed by rebuilding the file.
        """
        path = _write(tmp_path, {"2024|NCKL": _entry("NCKL", 2024)})

        with pytest.raises(CatalogError) as caught:
            load_entries(path, YEAR)

        assert "2024" in str(caught.value)

    def test_an_entry_without_a_url_is_refused(self, tmp_path: Path) -> None:
        path = _write(tmp_path, {"2025|NCKL": {"ticker": "NCKL", "year": YEAR}})

        with pytest.raises(CatalogError, match="tanpa ticker atau url"):
            load_entries(path, YEAR)

    def test_a_repeated_ticker_is_refused(self, tmp_path: Path) -> None:
        """Two records for one code cannot both be right, and picking one
        silently would decide the run's scope on the reader's behalf."""
        path = _write(
            tmp_path,
            {"2025|NCKL": _entry("NCKL"), "2025|NCKL ": _entry("NCKL")},
        )

        with pytest.raises(CatalogError, match="ganda"):
            load_entries(path, YEAR)

    def test_a_record_that_is_not_an_object_is_refused(self, tmp_path: Path) -> None:
        path = _write(tmp_path, {"2025|NCKL": "NCKL"})

        with pytest.raises(CatalogError, match="bukan objek"):
            load_entries(path, YEAR)


class TestDivergent:
    def test_a_catalog_matching_the_pattern_reports_nothing(
        self, tmp_path: Path
    ) -> None:
        assert divergent(load_entries(_write(tmp_path, {"2025|NCKL": _entry("NCKL")}), YEAR), YEAR) == []

    def test_a_url_that_no_longer_matches_is_named(self, tmp_path: Path) -> None:
        """The catalog is fetched by hand, so a truncated or edited file is a
        live possibility -- and a URL that stopped matching the shape this
        program has always reproduced is worth a line before anything moves.

        Named, not refused: the catalog is the source of truth, so the run
        proceeds with its URL. This only says out loud that it drifted.
        """
        path = _write(
            tmp_path,
            {"2025|NCKL": _entry("NCKL", url="https://www.idx.co.id/other/NCKL/instance.zip")},
        )

        assert divergent(load_entries(path, YEAR), YEAR) == ["NCKL"]
