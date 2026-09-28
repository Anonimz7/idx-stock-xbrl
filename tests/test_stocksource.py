"""Reading a stock list from a file, minus the delisted ones.

The filter is the point of the module. A delisted code still resolves to an IDX
profile page and IDX still serves its old reports, so without this a batch
quietly spends a full paced browser workflow on a company that no longer trades
and produces reports nobody asked for.

The dump used here is written the way HeidiSQL writes one, because the real file
came from HeidiSQL and the parser has to survive its habits: backtick-quoted
identifiers, `NO_AUTO_VALUE_ON_ZERO`, values wrapped in strings, and tuples that
run across many lines.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from firefox_bridge.stocksource import (
    StockListError,
    read_delimited,
    read_sql_dump,
    read_stock_list,
)

HEADER = """-- Host: 127.0.0.1
/*!40101 SET NAMES utf8 */;
/*!40101 SET SQL_MODE=@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
"""


def dump(
    rows: str,
    table: str = "datasaham",
    columns: str = "`kode`, `nama`, `delisted`",
) -> str:
    return f"{HEADER}\nINSERT INTO `{table}` ({columns}) VALUES {rows};\n"


ACTIVE = "('NCKL','NICKEL INDONESIA',0),('BBCA','BANK RAKYAT INDONESIA',0)"
DELISTED = "('BNI','BANK NEGARA INDONESIA',1),('BLBI','BANK LIQUIDITAS',1)"


# --- SQL dumps ------------------------------------------------------------


def test_an_heidisql_dump_yields_the_active_codes() -> None:
    result = read_sql_dump(dump(ACTIVE))

    assert result.codes == ("NCKL", "BBCA")
    assert result.skipped_delisted == 0


def test_delisted_rows_are_excluded() -> None:
    result = read_sql_dump(dump(ACTIVE + "," + DELISTED))

    assert result.codes == ("NCKL", "BBCA")
    assert result.skipped_delisted == 2


def test_a_row_spanning_many_lines_is_parsed() -> None:
    """HeidiSQL wraps long VALUES lists, so rows are not line-delimited."""
    rows = (
        "('NCKL','NICKEL INDONESIA',0),\n"
        "  ('BBCA','BANK RAKYAT INDONESIA',0),\n"
        "  ('TLKM','TELKOM INDONESIA',0)"
    )
    assert read_sql_dump(dump(rows)).codes == ("NCKL", "BBCA", "TLKM")


def test_a_name_containing_a_comma_does_not_split_the_row() -> None:
    rows = "('AGRO','BANK AGRINA UTAMA, TBK',0),('BBCA','BANK RAKYAT INDONESIA',1)"
    result = read_sql_dump(dump(rows))

    assert result.codes == ("AGRO",)
    assert result.skipped_delisted == 1


def test_a_name_containing_an_escaped_quote_is_handled() -> None:
    rows = "('X','PERNUSAHAAN \\'ANEKA\\' MAKMUR',0)"
    assert read_sql_dump(dump(rows)).codes == ("X",)


def test_a_name_containing_a_semicolon_does_not_end_the_statement() -> None:
    rows = "('X','ACME; PABRIK',0),('Y','BETA',1)"
    result = read_sql_dump(dump(rows))

    assert result.codes == ("X",)
    assert result.skipped_delisted == 1


def test_codes_are_uppercased() -> None:
    assert read_sql_dump(dump("('nckl','NICKEL',0)")).codes == ("NCKL",)


def test_duplicates_are_counted_not_repeated() -> None:
    rows = "('NCKL','A',0),('NCKL','B',0),('BBCA','C',0)"
    result = read_sql_dump(dump(rows))

    assert result.codes == ("NCKL", "BBCA")
    assert result.skipped_duplicate == 1


def test_several_insert_statements_are_combined() -> None:
    text = dump("('NCKL','A',0)") + dump("('BBCA','B',0)", table="datasaham2")
    result = read_sql_dump(text)

    assert result.codes == ("NCKL", "BBCA")


def test_a_dump_without_data_is_rejected_with_an_actionable_message() -> None:
    """The real file the user has looks exactly like this.

    The error has to name the cause, because "no data" and "wrong parser" are
    very different problems and the reader should not leave the reader guessing.
    """
    empty = "-- Data exporting was unselected.\n/*!40101 SET NAMES utf8 */;\n"

    with pytest.raises(StockListError, match="Ekspor harus menyertakan data"):
        read_sql_dump(empty)


def test_a_missing_code_column_is_rejected_and_lists_what_was_found() -> None:
    with pytest.raises(StockListError) as error:
        read_sql_dump(dump("('A',0)", columns="`nama`, `delisted`"))

    assert "Kolom kode saham tidak ditemukan" in str(error.value)
    assert "nama" in str(error.value), "the error should show the columns it did see"


def test_a_missing_delisted_column_is_rejected_rather_than_ignored() -> None:
    """Silently returning every stock would look exactly like success.

    The whole purpose of the file is the filter, so a file that cannot express
    it is an error, not a pass.
    """
    with pytest.raises(StockListError, match="status delisted"):
        read_sql_dump(dump("('NCKL','A',0)", columns="`kode`, `nama`"))


def test_ambiguous_code_columns_are_rejected() -> None:
    rows = "('NCKL','A',0)"
    with pytest.raises(StockListError, match="Lebih dari satu kolom kode"):
        read_sql_dump(dump(rows, columns="`kode`, `symbol`, `delisted`"))


@pytest.mark.parametrize("column", ["delisted", "is_delisted", "status_delisted", "delist"])
def test_common_spellings_of_the_delisted_column_are_accepted(column: str) -> None:
    result = read_sql_dump(dump("('NCKL','A',0),('BNI','B',1)", columns=f"`kode`, `nama`, `{column}`"))

    assert result.codes == ("NCKL",)
    assert result.skipped_delisted == 1


@pytest.mark.parametrize("column", ["kode", "kode_saham", "Kode Saham", "code", "symbol"])
def test_common_spellings_of_the_code_column_are_accepted(column: str) -> None:
    result = read_sql_dump(dump("('NCKL','A',0)", columns=f"`{column}`, `nama`, `delisted`"))

    assert result.codes == ("NCKL",)


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("'1'", True),
    ("0", False), ("", False), ("NULL", False), ("2", False),
])
def test_only_an_explicit_yes_means_delisted(value: str, expected: bool) -> None:
    """An unknown cell is not a yes.

    Treating blank as delisted would silently drop real stocks, and nobody would
    find out until a report was missing.
    """
    result = read_sql_dump(dump(f"('NCKL','A',{value})"))

    assert bool(result.codes) is (not expected)


def test_a_large_dump_stays_linear(tmp_path: Path) -> None:
    """Thousands of rows must not turn the reader quadratic.

    Slicing to the end of the file per INSERT would copy the whole dump once per
    statement, which is exactly the shape of a real listing table.
    """
    rows = ",".join(f"('S{index:04d}','NAME {index}',0)" for index in range(5000))
    text = dump(rows)

    import time

    start = time.monotonic()
    result = read_sql_dump(text)
    elapsed = time.monotonic() - start

    assert len(result.codes) == 5000
    assert elapsed < 5.0, f"parsing 5000 rows took {elapsed:.1f}s"


# --- CSV ------------------------------------------------------------------


def test_a_csv_with_a_header_is_read() -> None:
    csv_text = "kode,nama,delisted\nNCKL,NICKEL,0\nBNI,BANK NEGARA,1\n"
    result = read_delimited(csv_text)

    assert result.codes == ("NCKL",)
    assert result.skipped_delisted == 1


def test_a_csv_without_a_delisted_column_is_rejected() -> None:
    with pytest.raises(StockListError, match="status delisted"):
        read_delimited("kode,nama\nNCKL,NICKEL\n")


def test_an_empty_csv_is_rejected() -> None:
    with pytest.raises(StockListError, match="kosong"):
        read_delimited("\n\n")


# --- choosing the format --------------------------------------------------


def test_a_sql_path_is_read_as_sql(tmp_path: Path) -> None:
    path = tmp_path / "datasaham.sql"
    path.write_text(dump(ACTIVE + "," + DELISTED), encoding="utf-8")

    result = read_stock_list(path)

    assert result.codes == ("NCKL", "BBCA")
    assert result.skipped_delisted == 2


def test_a_csv_path_is_read_as_csv(tmp_path: Path) -> None:
    path = tmp_path / "saham.csv"
    path.write_text("kode,delisted\nNCKL,0\nBNI,1\n", encoding="utf-8")

    assert read_stock_list(path).codes == ("NCKL",)


def test_a_missing_file_is_rejected() -> None:
    with pytest.raises(StockListError, match="tidak ditemukan"):
        read_stock_list(Path("C:/tidak/ada.sql"))


def test_a_row_whose_value_count_disagrees_with_the_columns_is_rejected() -> None:
    """A malformed dump must fail loudly, not yield the wrong stocks.

    When the counts disagree there is no way to know which value is the code.
    Guessing would put a wrong stock into a batch that takes half an hour, where
    it surfaces as "NCKL 2025 not found" a long way from its cause.
    """
    columns = "`id`, `kode`, `nama`, `delisted`"
    rows = "('NCKL','A',0)"  # three values against four columns

    with pytest.raises(StockListError, match="tidak konsisten"):
        read_sql_dump(dump(rows, columns=columns))
