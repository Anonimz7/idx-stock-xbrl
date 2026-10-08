"""Tes katalog instance.zip: parser halaman, merge, dan paginasi.

Semua yang diuji di sini murni murni -- tanpa peramban, tanpa jembatan.
Data contoh diambil dari bentuk halaman asli (filter Tahun 2025 / Tahunan).
"""

from __future__ import annotations

import argparse
import json

import pytest
from idx_watcher.instance_catalog import (
    CatalogError,
    entries_from_api,
    find_element,
    import_from_api,
    main,
    merge_entries,
    next_page_button,
    parse_instance_hrefs,
    parse_stamps,
    year_labels,
)

HREF = (
    "https://www.idx.co.id/Portals/0/StaticData/ListedCompanies/Corporate_Actions/"
    "New_Info_JSX/Jenis_Informasi/01_Laporan_Keuangan/02_Soft_Copy_Laporan_Keuangan//"
    "Laporan%20Keuangan%20Tahun%202025/Audit/AADI/instance.zip"
)
INLINE_XBRL = HREF.rsplit("/", 1)[0] + "/inlineXBRL.zip"
PDF = HREF.rsplit("/", 1)[0] + "/FinancialStatement-2025-Tahunan-AADI.pdf"

PAGE_TEXT = (
    "Laporan Keuangan dan Tahunan 12 A-Z Filter Jenis Laporan "
    "AADI 06 Maret 2026 | 15:57 Nama : PT Adaro Andalan Indonesia Tbk "
    "Tahun : 2025 Periode : Audit "
    "FinancialStatement-2025-Tahunan-AADI.pdf instance.zip "
    "AALI 21 Februari 2026 | 18:40 Nama : Astra Agro Lestari Tbk "
    "Tahun : 2025 Periode : Audit AALI LK Ta instance.zip "
    "ABDA 30 April 2026 | 16:05 Tahun : 2025 Periode : Audit instance.zip "
    "dari 71"
)


def _element(ref: str, role: str, name: str, *, disabled: bool = False, href: str = "") -> dict:
    return {
        "ref": ref,
        "role": role,
        "name": name,
        "state": {"disabled": disabled},
        **({"href": href} if href else {}),
    }


class TestParseInstanceHrefs:
    def test_extracts_year_and_ticker_from_url(self) -> None:
        found = parse_instance_hrefs([_element("e1", "link", "", href=HREF)])
        assert found == {
            "2025|AADI": {
                "ticker": "AADI",
                "year": 2025,
                "period": "audit",
                "url": HREF,
            }
        }

    def test_ignores_inline_xbrl_and_other_attachments(self) -> None:
        elements = [
            _element("e1", "link", "", href=INLINE_XBRL),
            _element("e2", "link", "", href=PDF),
            _element("e3", "link", "", href=HREF),
        ]
        found = parse_instance_hrefs(elements)
        assert list(found) == ["2025|AADI"]

    def test_links_without_href_are_skipped(self) -> None:
        assert parse_instance_hrefs([_element("e1", "button", "Terapkan")]) == {}

    def test_accepts_unencoded_spaces(self) -> None:
        plain = HREF.replace("%20", " ")
        found = parse_instance_hrefs([_element("e1", "link", "", href=plain)])
        assert "2025|AADI" in found


class TestParseStamps:
    def test_reads_full_indonesian_month(self) -> None:
        assert parse_stamps(PAGE_TEXT)["AADI"] == "2026-03-06T15:57"

    def test_reads_february_spelled_out(self) -> None:
        assert parse_stamps(PAGE_TEXT)["AALI"] == "2026-02-21T18:40"

    def test_month_alternation_prefers_long_form(self) -> None:
        # "feb" tidak boleh memotong "februari" menjadi sisa yang tak terbaca.
        assert parse_stamps("AALI 21 Februari 2026 | 18:40")["AALI"] == "2026-02-21T18:40"

    def test_pads_single_digit_day(self) -> None:
        assert parse_stamps("ADES 1 Mei 2026 | 09:05")["ADES"] == "2026-05-01T09:05"

    def test_ignores_filename_without_timestamp(self) -> None:
        text = "FinancialStatement-2025-Tahunan-AADI.pdf instance.zip"
        assert parse_stamps(text) == {}

    def test_no_match_returns_empty(self) -> None:
        assert parse_stamps("") == {}


class TestFindElement:
    def test_matches_role_and_label_exactly(self) -> None:
        elements = [
            _element("e1", "radio", "2026"),
            _element("e2", "radio", "2025"),
            _element("e3", "button", "Terapkan"),
        ]
        year_radio = find_element(elements, role="radio", name="2025")
        apply_button = find_element(elements, role="button", name="Terapkan")
        assert year_radio is not None
        assert apply_button is not None
        assert year_radio["ref"] == "e2"
        assert apply_button["ref"] == "e3"

    def test_returns_none_when_absent(self) -> None:
        assert find_element([], role="radio", name="2025") is None

    def test_label_must_match_whole_string(self) -> None:
        elements = [_element("e1", "button", "Go to next page")]
        assert find_element(elements, role="button", name="next") is None


class TestNextPageButton:
    def test_reports_enabled_button(self) -> None:
        ref, done = next_page_button([_element("e9", "button", "Go to next page")])
        assert (ref, done) == ("e9", False)

    def test_reports_disabled_button_as_finished(self) -> None:
        ref, done = next_page_button(
            [_element("e9", "button", "Go to next page", disabled=True)]
        )
        assert (ref, done) == ("e9", True)

    def test_missing_button_is_finished(self) -> None:
        assert next_page_button([]) == (None, False)


class TestYearLabels:
    def test_collects_only_four_digit_radios_in_descending_order(self) -> None:
        elements = [
            _element("e1", "radio", "2026"),
            _element("e2", "radio", "2025"),
            _element("e3", "radio", "Triwulan 1"),
            _element("e4", "radio", "Saham"),
            _element("e5", "radio", "2022"),
        ]
        assert year_labels(elements) == ["2026", "2025", "2022"]

    def test_empty_when_no_year_radios(self) -> None:
        assert year_labels([_element("e1", "radio", "Saham")]) == []


class TestMergeEntries:
    def test_records_upload_time_and_witnessed_at(self) -> None:
        catalog: dict = {}
        found = parse_instance_hrefs([_element("e1", "link", "", href=HREF)])
        added, changed = merge_entries(catalog, found, {"AADI": "2026-03-06T15:57"}, "2026-10-07T10:00:00+07:00")
        entry = catalog["entries"]["2025|AADI"]
        assert (added, changed) == (1, 0)
        assert entry["uploaded_at"] == "2026-03-06T15:57"
        assert entry["first_seen"] == "2026-10-07T10:00:00+07:00"
        assert entry["last_seen"] == "2026-10-07T10:00:00+07:00"

    def test_repeat_scan_only_touches_last_seen(self) -> None:
        catalog: dict = {}
        found = parse_instance_hrefs([_element("e1", "link", "", href=HREF)])
        merge_entries(catalog, found, {"AADI": "2026-03-06T15:57"}, "2026-10-07T10:00:00+07:00")
        added, changed = merge_entries(catalog, found, {"AADI": "2026-03-06T15:57"}, "2026-10-08T10:00:00+07:00")
        entry = catalog["entries"]["2025|AADI"]
        assert (added, changed) == (0, 0)
        assert entry["first_seen"] == "2026-10-07T10:00:00+07:00"
        assert entry["last_seen"] == "2026-10-08T10:00:00+07:00"

    def test_counts_entry_when_upload_time_appears_later(self) -> None:
        catalog: dict = {}
        found = parse_instance_hrefs([_element("e1", "link", "", href=HREF)])
        merge_entries(catalog, found, {}, "2026-10-07T10:00:00+07:00")
        assert "uploaded_at" not in catalog["entries"]["2025|AADI"]
        added, changed = merge_entries(catalog, found, {"AADI": "2026-03-06T15:57"}, "2026-10-08T10:00:00+07:00")
        assert (added, changed) == (0, 1)
        assert catalog["entries"]["2025|AADI"]["uploaded_at"] == "2026-03-06T15:57"

    def test_never_lowers_a_recorded_upload_time(self) -> None:
        catalog: dict = {}
        found = parse_instance_hrefs([_element("e1", "link", "", href=HREF)])
        merge_entries(catalog, found, {"AADI": "2026-03-06T15:57"}, "2026-10-07T10:00:00+07:00")
        merge_entries(catalog, found, {}, "2026-10-08T10:00:00+07:00")
        assert catalog["entries"]["2025|AADI"]["uploaded_at"] == "2026-03-06T15:57"


def test_catalog_error_is_a_runtime_error() -> None:
    with pytest.raises(RuntimeError):
        raise CatalogError("x")


def _api_row(
    code: str = "AADI",
    year: str = "2025",
    *,
    modified: str = "2026-03-06T15:57:00.003",
    period: str = "Audit",
    path: str | None = None,
    attachments: list | None = None,
) -> dict:
    """Satu baris respons GetFinancialReport, sesuai bentuk aslinya."""
    if path is None:
        path = (
            "/Portals/0/StaticData/ListedCompanies/Corporate_Actions/New_Info_JSX/"
            "Jenis_Informasi/01_Laporan_Keuangan/02_Soft_Copy_Laporan_Keuangan//"
            f"Laporan Keuangan Tahun {year}/Audit/{code}/instance.zip"
        )
    if attachments is None:
        attachments = [
            {
                "File_Name": "FinancialStatement-2025-Tahunan-AADI.pdf",
                "File_Path": "/Portals/0/x/FinancialStatement-2025-Tahunan-AADI.pdf",
            },
            {"File_Name": "instance.zip", "File_Path": path},
        ]
    return {
        "KodeEmiten": code,
        "File_Modified": modified,
        "Report_Period": period,
        "Report_Year": year,
        "NamaEmiten": "PT Contoh Tbk",
        "Attachments": attachments,
    }


def _api_doc(*rows: dict) -> dict:
    return {"ResultCount": len(rows), "Results": list(rows)}


class TestEntriesFromApi:
    def test_extracts_key_url_and_upload_time(self) -> None:
        found, _ = entries_from_api(_api_doc(_api_row()))
        entry = found["2025|AADI"]
        assert entry["ticker"] == "AADI"
        assert entry["year"] == 2025
        assert entry["period"] == "audit"
        assert entry["uploaded_at"] == "2026-03-06T15:57"
        assert entry["url"].endswith("/Laporan%20Keuangan%20Tahun%202025/Audit/AADI/instance.zip")

    def test_spaces_are_percent_encoded_like_the_page(self) -> None:
        found, _ = entries_from_api(_api_doc(_api_row()))
        # bentuk halaman memakai %20, bukan spasi mentah
        assert " " not in found["2025|AADI"]["url"]
        assert "%20" in found["2025|AADI"]["url"]

    def test_picks_instance_zip_and_ignores_the_rest(self) -> None:
        found, _ = entries_from_api(
            _api_doc(
                _api_row(
                    attachments=[
                        {"File_Name": "FinancialStatement-2025-Tahunan-AADI.pdf", "File_Path": "/p.pdf"},
                        {"File_Name": "inlineXBRL.zip", "File_Path": "/x.zip"},
                        {"File_Name": "instance.zip", "File_Path": "/wanted/instance.zip"},
                    ]
                )
            )
        )
        assert found["2025|AADI"]["url"] == "https://www.idx.co.id/wanted/instance.zip"

    def test_row_without_instance_zip_is_skipped_not_fatal(self) -> None:
        """Satu baris tanpa arsip tidak berhak mematikan baris lain.

        BINA 2024 hanya punya PDF di IDX; 884 baris lain file itu lengkap.
        """
        found, skipped = entries_from_api(
            _api_doc(
                _api_row("AADI"),
                _api_row("BINA", attachments=[{"File_Name": "a.pdf", "File_Path": "/x"}]),
            )
        )
        assert set(found) == {"2025|AADI"}
        assert skipped == ["BINA 2025"]

    def test_a_document_without_any_instance_zip_is_rejected(self) -> None:
        """Nol entri berarti file ini bukan respons GetFinancialReport."""
        with pytest.raises(CatalogError, match="instance.zip"):
            entries_from_api(
                _api_doc(_api_row(attachments=[{"File_Name": "a.pdf", "File_Path": "/x"}]))
            )

    def test_readies_several_rows(self) -> None:
        found, _ = entries_from_api(
            _api_doc(_api_row("AADI"), _api_row("ZONE"), _api_row("ZYRX"))
        )
        assert set(found) == {"2025|AADI", "2025|ZONE", "2025|ZYRX"}

    def test_period_comes_from_data_not_from_a_label(self) -> None:
        found, _ = entries_from_api(_api_doc(_api_row(period="TW1")))
        assert found["2025|AADI"]["period"] == "tw1"

    def test_document_that_is_not_an_object_is_rejected(self) -> None:
        with pytest.raises(CatalogError, match="objek JSON"):
            entries_from_api([1, 2, 3])

    def test_document_without_results_is_rejected(self) -> None:
        with pytest.raises(CatalogError, match="Results"):
            entries_from_api({"ResultCount": 0})

    def test_empty_results_is_rejected(self) -> None:
        with pytest.raises(CatalogError, match="kosong"):
            entries_from_api(_api_doc())

    def test_row_without_ticker_is_rejected(self) -> None:
        with pytest.raises(CatalogError, match="KodeEmiten"):
            entries_from_api(_api_doc(_api_row(code="")))

    def test_non_numeric_year_is_rejected(self) -> None:
        with pytest.raises(CatalogError, match="Report_Year"):
            entries_from_api(_api_doc(_api_row(year="dua ribu")))

    def test_duplicate_key_is_rejected(self) -> None:
        with pytest.raises(CatalogError, match="ganda"):
            entries_from_api(_api_doc(_api_row(), _api_row()))


class TestImportFromApi:
    @staticmethod
    def _args(tmp_path, body: dict | str, *, years: str = "") -> argparse.Namespace:
        text = json.dumps(body) if isinstance(body, dict) else str(body)
        handle = tmp_path / "GetFinancialReport.json"
        handle.write_text(text, encoding="utf-8")
        return argparse.Namespace(api_file=str(handle), years=years)

    def test_merges_into_catalog_and_counts(self, tmp_path) -> None:
        catalog: dict = {}
        args = self._args(tmp_path, _api_doc(_api_row()))
        totals, added, changed = import_from_api(catalog, args, "2026-10-08T10:00:00+07:00")
        assert (totals, added, changed) == (1, 1, 0)
        assert catalog["entries"]["2025|AADI"]["first_seen"] == "2026-10-08T10:00:00+07:00"

    def test_a_skipped_row_is_reported_in_the_log(self, tmp_path, capsys) -> None:
        """Baris yang dilewati harus terlihat, bukan hilang diam-diam."""
        catalog: dict = {}
        args = self._args(
            tmp_path,
            _api_doc(
                _api_row("AADI"),
                _api_row("BINA", attachments=[{"File_Name": "a.pdf", "File_Path": "/x"}]),
            ),
        )
        totals, added, _ = import_from_api(catalog, args, "2026-10-08T10:00:00+07:00")
        assert (totals, added) == (1, 1)
        assert set(catalog["entries"]) == {"2025|AADI"}
        out = capsys.readouterr().out
        assert "lewati BINA 2025: tidak ada instance.zip di Attachments" in out

    def test_second_import_only_touches_last_seen(self, tmp_path) -> None:
        catalog: dict = {}
        args = self._args(tmp_path, _api_doc(_api_row()))
        import_from_api(catalog, args, "2026-10-08T10:00:00+07:00")
        totals, added, changed = import_from_api(catalog, args, "2026-10-09T10:00:00+07:00")
        assert (totals, added, changed) == (1, 0, 0)
        entry = catalog["entries"]["2025|AADI"]
        assert entry["first_seen"] == "2026-10-08T10:00:00+07:00"
        assert entry["last_seen"] == "2026-10-09T10:00:00+07:00"

    def test_years_filter_keeps_only_requested_year(self, tmp_path) -> None:
        catalog: dict = {}
        args = self._args(
            tmp_path,
            _api_doc(_api_row("AADI", "2025"), _api_row("BBCA", "2024")),
            years="2024",
        )
        totals, added, _ = import_from_api(catalog, args, "2026-10-08T10:00:00+07:00")
        assert (totals, added) == (1, 1)
        assert set(catalog["entries"]) == {"2024|BBCA"}

    def test_years_filter_with_no_match_is_rejected(self, tmp_path) -> None:
        args = self._args(tmp_path, _api_doc(_api_row()), years="1999")
        with pytest.raises(CatalogError, match="1999"):
            import_from_api({}, args, "2026-10-08T10:00:00+07:00")

    def test_missing_file_is_rejected_with_download_hint(self) -> None:
        args = argparse.Namespace(api_file="/does/not/exist.json", years="")
        with pytest.raises(CatalogError, match="GetFinancialReport"):
            import_from_api({}, args, "2026-10-08T10:00:00+07:00")

    def test_invalid_json_is_rejected(self, tmp_path) -> None:
        args = self._args(tmp_path, "{bukan json")
        with pytest.raises(CatalogError, match="JSON valid"):
            import_from_api({}, args, "2026-10-08T10:00:00+07:00")


def test_source_api_without_api_file_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--source", "api"])
    assert excinfo.value.code == 2
