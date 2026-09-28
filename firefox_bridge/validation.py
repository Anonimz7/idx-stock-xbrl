"""Guards on the path data takes: stock code in, URL through, archive out.

The downloader moves bytes from a web page into a folder on disk, which means
three separate places where untrusted text becomes a real file. Each one is
checked here, and each check exists because the next stage trusts its input:

* A stock code becomes ``saham/<CODE>/<YEAR>/`` -- so ``..`` or a separator
  would write outside the download root.
* An ``href`` read from the page becomes the URL Firefox fetches -- so an
  off-host link would be downloaded as though IDX had published it.
* The finished file is moved into the archive folder -- so anything that is not
  a readable ZIP should never be recorded as a report.

These are deliberately small, dependency-free functions. They are the last thing
between a malformed page and a file on disk, so they answer questions rather
than accumulate configuration.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse

# IDX publishes reports under these hosts and nowhere else.
IDX_HOSTS = frozenset({"www.idx.co.id", "idx.co.id"})
ALLOWED_SCHEMES = frozenset({"http", "https"})

STOCK_CODE_MAX_LENGTH = 10

# A report archive is a few hundred kilobytes. A wildly larger file means the
# download went wrong, or was not a report at all.
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 20_000
# Guards against a zip bomb: total uncompressed size relative to the archive.
MAX_COMPRESSION_RATIO = 200

# Windows refuses these as file names regardless of extension, and a stock code
# becomes a folder name.
_RESERVED_WINDOWS_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{digit}" for digit in range(1, 10)}
    | {f"lpt{digit}" for digit in range(1, 10)}
)

# `PK\x03\x04` local file header, `PK\x05\x06` empty archive,
# `PK\x07\x08` spanned archive.
_ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


class ValidationError(ValueError):
    """Raised when untrusted input would escape the intended boundaries."""


def normalize_stock_code(code: str) -> str:
    """Return `code` uppercased, or raise if it cannot safely become a folder.

    Only alphanumerics survive. That is narrower than IDX might ever need, which
    is the point: a rejected real ticker is a one-line fix, while a traversed
    path is a file written where nobody expected it.
    """
    candidate = str(code or "").strip().upper()
    if not candidate:
        raise ValidationError("stock code kosong")
    if len(candidate) > STOCK_CODE_MAX_LENGTH:
        raise ValidationError(
            f"stock code terlalu panjang: {candidate!r} (maks {STOCK_CODE_MAX_LENGTH})"
        )
    if not all(character.isascii() and character.isalnum() for character in candidate):
        raise ValidationError(
            f"stock code hanya boleh huruf dan angka ASCII: {code!r}"
        )
    if candidate.lower() in _RESERVED_WINDOWS_NAMES:
        raise ValidationError(f"stock code memakai nama devices Windows: {candidate!r}")
    return candidate


def _reject(message: str) -> None:
    raise ValidationError(message)


def validate_report_url(
    href: str,
    stock: str,
    year: int,
    quarter: int,
) -> str:
    """Return `href` when it is the IDX archive for this stock and quarter.

    The host check is the load-bearing one. `is_report_link` matches on path
    fragments only, so a link to ``https://elsewhere.example/...inlineXBRL.zip``
    satisfies it -- and an attacker who can put an anchor on the IDX page would
    otherwise choose the URL this program fetches.
    """
    if not href or not href.strip():
        _reject(f"URL kosong untuk {stock} {year} TW{quarter}")
    candidate = href.strip()

    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        _reject(f"skema URL tidak diizinkan: {parsed.scheme!r} pada {candidate}")
    host = (parsed.hostname or "").lower()
    if host not in IDX_HOSTS:
        _reject(f"host di luar IDX: {host!r} pada {candidate}")

    # Percent-encoding is how IDX writes spaces in these paths, so the year and
    # quarter markers only match after decoding.
    path = unquote(parsed.path).lower()
    if not path.endswith(".zip"):
        _reject(f"bukan arsip .zip: {candidate}")
    if "inlinexbrl.zip" not in path:
        _reject(f"bukan inlineXBRL.zip: {candidate}")
    if f"tahun {year}" not in path and f"tahun%20{year}" not in parsed.path.lower():
        _reject(f"tahun {year} tidak ada di URL: {candidate}")

    if quarter == 4:
        if "/audit/" not in path:
            _reject(f"TW4 harus lewat /Audit/, bukan: {candidate}")
    elif f"/tw{quarter}/" not in path:
        _reject(f"segmen /TW{quarter}/ tidak ditemukan: {candidate}")

    expected = normalize_stock_code(stock).lower()
    if f"/{expected}/" not in path:
        _reject(f"URL milik saham lain, bukan {expected}: {candidate}")
    return candidate


def validate_archive(path: Path, max_bytes: int = MAX_ARCHIVE_BYTES) -> int:
    """Return the archive size, or raise if `path` is not a usable ZIP report.

    Runs before the file is moved into the archive folder, so a truncated or
    non-ZIP download is rejected while it is still in staging and still costs
    nothing to discard.
    """
    if not path.is_file():
        _reject(f"berkas tidak ditemukan: {path.name}")
    size = path.stat().st_size
    if size == 0:
        _reject(f"berkas kosong: {path.name}")
    if size > max_bytes:
        _reject(f"berkas terlalu besar: {size} byte > {max_bytes} byte ({path.name})")

    with path.open("rb") as handle:
        head = handle.read(4)
        if not any(head.startswith(signature) for signature in _ZIP_SIGNATURES):
            _reject(f"bukan arsip ZIP (signature): {path.name}")
        handle.seek(max(0, size - 22))
        if b"PK\x05\x06" not in handle.read():
            _reject(f"arsip ZIP terpotong (tidak ada end-of-central-directory): {path.name}")

    try:
        with zipfile.ZipFile(path) as archive:
            broken = archive.testzip()
            if broken is not None:
                _reject(f"entrus ZIP rusak: {broken} pada {path.name}")
            entries = archive.infolist()
            if not entries:
                _reject(f"arsip ZIP tanpa entri: {path.name}")
            if len(entries) > MAX_ARCHIVE_ENTRIES:
                _reject(f"arsip ZIP punya terlalu banyak entri: {len(entries)}")
            uncompressed = sum(entry.file_size for entry in entries)
            if size and uncompressed / size > MAX_COMPRESSION_RATIO:
                _reject(
                    f"rasio kompresi mencurigakan ({uncompressed / size:.0f}x): {path.name}"
                )
    except zipfile.BadZipFile as error:
        _reject(f"arsip ZIP tidak bisa dibaca: {path.name} ({error})")

    return size


__all__ = [
    "ALLOWED_SCHEMES",
    "IDX_HOSTS",
    "MAX_ARCHIVE_BYTES",
    "STOCK_CODE_MAX_LENGTH",
    "ValidationError",
    "normalize_stock_code",
    "validate_archive",
    "validate_report_url",
]
