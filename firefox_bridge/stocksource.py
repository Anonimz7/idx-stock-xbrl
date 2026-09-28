"""Read a list of stock codes from a file, minus the ones that are delisted.

The codes arrive as text written by something else -- a CSV export, or a SQL dump
from HeidiSQL. Both are read here rather than guessed at in the CLI, because
neither format is worth supporting twice.

The delisted filter is the reason this module exists. A delisted code still
resolves to a profile page, and IDX still serves its old reports, so without
this filter a batch quietly spends a full paced browser workflow on a company
that no longer trades -- and produces reports nobody wants.

**Column names are read from the file, never assumed.** A SQL dump carries its
own column list, and a CSV carries its own header, so the code column and the
delisted column are identified from the document in front of us. When the
document does not say clearly which is which, this raises and shows the columns
it did find, rather than picking one and quietly returning the wrong stocks.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

# Candidate names, matched case-insensitively and ignoring `_` and spaces. A
# column called `Kode Saham`, `kode_saham`, or `KODE` is the same column.
_CODE_COLUMNS = ("kode", "code", "kode_saham", "codesaham", "symbol", "stock", "stockcode", "ticker")
_DELISTED_COLUMNS = (
    "delisted",
    "isdelisted",
    "statusdelisted",
    "delist",
    "isdelist",
    "statusdelist",
)

_INSERT_HEAD = re.compile(
    r"INSERT\s+INTO\s+[`\"]?(?P<table>[\w]+)[`\"]?\s*\((?P<columns>[^)]*)\)\s*VALUES\s*",
    re.IGNORECASE,
)


class StockListError(ValueError):
    """Raised when a stock list cannot be read, or says too little to be safe."""


@dataclass(frozen=True, slots=True)
class StockList:
    """The result of reading one file."""

    codes: tuple[str, ...] = ()
    skipped_delisted: int = 0
    skipped_invalid: int = 0
    skipped_duplicate: int = 0
    source: str = ""
    columns_seen: tuple[str, ...] = field(default_factory=tuple)


def _key(name: str) -> str:
    return re.sub(r"[\s_]+", "", name.strip().strip("`\"").lower())


def _match_column(available: list[str], candidates: tuple[str, ...]) -> str | None:
    # Both sides go through `_key`: a column called `Kode Saham` and a candidate
    # written `kode_saham` have to meet in the same normalised space, or the
    # candidate list silently stops matching the very spellings it exists for.
    normalised = {_key(name): name for name in available}
    for candidate in candidates:
        key = _key(candidate)
        if key in normalised:
            return normalised[key]
    return None


def _match_delisted_column(available: list[str], source: str) -> str:
    """Find the delisted flag, accepting any column whose name contains it.

    The real export calls it `label_delisted`. Enumerating spellings is how a
    reader ends up refusing a perfectly good file, so the exact list is tried
    first and a substring match is the fallback. Exact is preferred because a
    table carrying both `delisted` and `is_delisted` should resolve to the plain
    one, and two substring matches with no exact match is a genuine ambiguity
    worth reporting rather than picking from.
    """
    exact = _match_column(available, _DELISTED_COLUMNS)
    if exact is not None:
        return exact

    containing = [name for name in available if "delist" in _key(name)]
    if len(containing) == 1:
        return containing[0]
    if len(containing) > 1:
        raise StockListError(
            f"Lebih dari satu kolom delisted di {source}: {', '.join(containing)}. "
            "Sebutkan mana yang dipakai."
        )
    raise StockListError(
        f"Kolom status delisted tidak ditemukan di {source}, jadi daftar tidak "
        f"bisa difilter. Kolom yang ada: {', '.join(available)}"
    )


def _resolve_columns(
    available: list[str], *, source: str
) -> tuple[str, str | None]:
    """Return the code column and, if present, the delisted column.

    Raises when the code column is missing or ambiguous, because either means we
    would otherwise return a list of the wrong thing with total confidence.
    """
    code_column = _match_column(available, _CODE_COLUMNS)
    if code_column is None:
        raise StockListError(
            f"Kolom kode saham tidak ditemukan di {source}. "
            f"Kolom yang ada: {', '.join(available) or '(tidak ada header)'}"
        )

    exact = [name for name in available if _key(name) in {_key(c) for c in _CODE_COLUMNS}]
    if len(exact) > 1:
        raise StockListError(
            f"Lebih dari satu kolom kode di {source}: {', '.join(exact)}. "
            "Sebutkan mana yang dipakai."
        )

    delisted_column = _match_delisted_column(available, source)
    return code_column, delisted_column


def _check_row_shape(row: list[str], columns: list[str], source: str) -> None:
    """Refuse a row whose value count does not match the column list.

    This is not defensive padding. When the counts disagree there is no way to
    know which value is the code, and guessing puts a wrong stock into a batch
    that takes half an hour to run -- where the mistake surfaces as "NCKL 2025
    not found" a long way from its cause.
    """
    if len(row) != len(columns):
        raise StockListError(
            f"Baris di {source} punya {len(row)} nilai tapi {len(columns)} kolom "
            f"({', '.join(columns)}). Isi dump tidak konsisten dengan daftar kolomnya."
        )


def _truthy_delisted(value: str) -> bool:
    """Whether a delisted cell means "delisted".

    Only `1` and `true` count. A blank or `NULL` cell means the source did not
    say, and treating an unknown as "yes" would silently drop real stocks while
    treating it as "no" only risks including a few that a human can see.
    """
    return value.strip().strip("'\"").lower() in {"1", "true", "yes"}


# --- SQL dumps ------------------------------------------------------------


def _split_rows(values: str) -> list[list[str]]:
    """Split a VALUES tuple list, respecting quotes and backslash escapes.

    The tuple brackets are syntax, not content: `(` must not become part of the
    first value, and the comma that separates two tuples must not become an
    extra empty value on the next one. Both mistakes produce a first code like
    `(NCKL` and an off-by-one column index -- the kind that looks like data
    until you try to download it.
    """
    rows: list[list[str]] = []
    row: list[str] = []
    cell: list[str] = []
    in_string = False
    index = 0

    while index < len(values):
        char = values[index]
        if in_string:
            if char == "\\" and index + 1 < len(values):
                cell.append(values[index + 1])
                index += 2
                continue
            if char == "'":
                in_string = False
            else:
                cell.append(char)
        elif char == "'":
            in_string = True
        elif char == "(":
            pass  # opens a tuple
        elif char == ")":
            if cell or row:
                row.append("".join(cell))
                cell = []
            if row:
                rows.append(row)
            row, cell = [], []
            if values[index + 1 : index + 2] == ",":
                index += 1  # the comma between tuples is not a value
        elif char == ",":
            if cell or row:
                row.append("".join(cell))
                cell = []
        elif char == ";":
            break
        else:
            cell.append(char)
        index += 1
    return rows


def read_sql_dump(text: str, source: str = "<sql>") -> StockList:
    """Read codes and delisted flags out of a SQL dump's INSERT statements."""
    codes: list[str] = []
    seen: set[str] = set()
    skipped_delisted = skipped_invalid = skipped_duplicate = 0
    columns_seen: tuple[str, ...] = ()

    matches = list(_INSERT_HEAD.finditer(text))
    for position, match in enumerate(matches):
        columns = [name.strip().strip("`\"") for name in match.group("columns").split(",")]
        code_column, delisted_column = _resolve_columns(columns, source=source)
        columns_seen = tuple(columns)
        code_index = columns.index(code_column)
        delisted_index = columns.index(delisted_column) if delisted_column else None

        # Bounded to the next statement. Slicing to the end of the file here
        # would copy the whole dump once per INSERT, which on a real listing
        # table means copying tens of megabytes dozens of times over.
        end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
        for row in _split_rows(text[match.end() : end]):
            if not row or row == [""]:
                continue
            _check_row_shape(row, columns, source)
            if delisted_index is not None and _truthy_delisted(row[delisted_index]):
                skipped_delisted += 1
                continue
            code = row[code_index].strip().upper()
            if not code:
                skipped_invalid += 1
                continue
            if code in seen:
                skipped_duplicate += 1
                continue
            seen.add(code)
            codes.append(code)

    if not columns_seen:
        raise StockListError(
            f"Tidak ada INSERT INTO ... VALUES di {source}. "
            "Ekspor harus menyertakan data, bukan hanya skema."
        )

    return StockList(
        codes=tuple(codes),
        skipped_delisted=skipped_delisted,
        skipped_invalid=skipped_invalid,
        skipped_duplicate=skipped_duplicate,
        source=source,
        columns_seen=columns_seen,
    )


# --- CSV and plain text ---------------------------------------------------


def read_delimited(text: str, source: str = "<csv>") -> StockList:
    """Read codes and delisted flags out of a CSV with a header row."""
    reader = csv.reader(io.StringIO(text))
    rows = [row for row in reader if row and any(cell.strip() for cell in row)]
    if not rows:
        raise StockListError(f"{source} kosong")

    columns = [cell.strip() for cell in rows[0]]
    code_column, delisted_column = _resolve_columns(columns, source=source)
    code_index = columns.index(code_column)
    # `_resolve_columns` raises unless it found both, so this is never None.
    delisted_index = columns.index(str(delisted_column))

    codes: list[str] = []
    seen: set[str] = set()
    skipped_delisted = skipped_invalid = skipped_duplicate = 0

    for row in rows[1:]:
        if code_index >= len(row):
            continue
        if delisted_index < len(row) and _truthy_delisted(row[delisted_index]):
            skipped_delisted += 1
            continue
        code = row[code_index].strip().upper()
        if not code:
            skipped_invalid += 1
            continue
        if code in seen:
            skipped_duplicate += 1
            continue
        seen.add(code)
        codes.append(code)

    return StockList(
        codes=tuple(codes),
        skipped_delisted=skipped_delisted,
        skipped_invalid=skipped_invalid,
        skipped_duplicate=skipped_duplicate,
        source=source,
        columns_seen=tuple(columns),
    )


def read_stock_list(path: Path) -> StockList:
    """Read a stock list from a `.sql` dump or a `.csv`/`.txt` table.

    The format is chosen by extension because the two carry their own column
    lists; sniffing the content to guess would be one more thing that can be
    wrong silently.
    """
    if not path.is_file():
        raise StockListError(f"Berkas daftar saham tidak ditemukan: {path}")

    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() == ".sql":
        return read_sql_dump(text, source=str(path))
    return read_delimited(text, source=str(path))


__all__ = [
    "StockList",
    "StockListError",
    "read_delimited",
    "read_sql_dump",
    "read_stock_list",
]
