"""Assemble and check the direct URL of an IDX audited ``instance.zip``.

The existing flow *discovers* hrefs by reading the Laporan Keuangan panel, because
which periods a page offers is only knowable from the page. The audited annual
archive is different: for a given ``(stock, year)`` it sits at a fixed path, so the
URL can be built instead of found -- which is the whole reason this program exists.

Only the audited annual report is addressed here. IDX publishes interim periods
under ``/TWn/`` and does not publish ``instance.zip`` for them, so a URL that is
not under ``/Audit/`` is wrong by construction rather than merely unverified.

That is also why this module carries its own validator instead of reusing
:func:`firefox_bridge.validation.validate_report_url`: that function rejects
anything whose archive name is not ``inlineXBRL.zip``, so it would reject every
URL this program produces. The two answer the same questions -- host, year,
period, stock -- but about different archives.
"""

from __future__ import annotations

from urllib.parse import quote, unquote, urlparse

from ..idx.models import AUDIT_QUARTER
from ..validation import IDX_HOSTS, ValidationError, normalize_stock_code

IDX_DOWNLOAD_BASE = (
    "https://www.idx.co.id/Portals/0/StaticData/ListedCompanies/"
    "Corporate_Actions/New_Info_JSX/Jenis_Informasi/"
    "01_Laporan_Keuangan/02_Soft_Copy_Laporan_Keuangan"
)
# A literal ``//`` separates the base from the year folder. It is present in every
# published link and in the captured fixture, so it is reproduced exactly rather
# than normalized to a single slash.
DOUBLE_SLASH = "//"
YEAR_SEGMENT = "Laporan Keuangan Tahun {year}"
AUDITED_SEGMENT = "Audit"
INSTANCE_FILENAME = "instance.zip"

# Reused rather than restated: "the audited annual report is quarter 4" is one
# fact about IDX, and two copies of it would be two chances to disagree.
AUDITED_QUARTER = AUDIT_QUARTER


def instance_url(stock: str, year: int) -> str:
    """Return the direct URL of one audited annual ``instance.zip``.

    The stock code is normalized here rather than by the caller, because the
    folder segment has to match what :func:`validate_instance_url` will later
    expect, and agreeing on one place to case-fold removes the chance of the two
    drifting apart.
    """
    code = normalize_stock_code(stock)
    year_segment = quote(YEAR_SEGMENT.format(year=year))
    return (
        f"{IDX_DOWNLOAD_BASE}{DOUBLE_SLASH}{year_segment}/"
        f"{AUDITED_SEGMENT}/{code}/{INSTANCE_FILENAME}"
    )


def validate_instance_url(href: str, stock: str, year: int) -> str:
    """Return ``href`` when it is the IDX archive for this stock and year.

    Nothing on this path reads an href out of a page, so unlike
    :func:`firefox_bridge.validation.validate_report_url` there is no
    attacker-chosen anchor to defend against -- the URL came from
    :func:`instance_url`. The checks below are therefore a self-check: they turn a
    future mistake in the template, a wrong year, or a caller passing a stock code
    it did not intend into a rejection before any bytes are fetched, instead of a
    404 or -- worse -- a file filed under the wrong folder.
    """
    if not href or not href.strip():
        raise ValidationError(f"URL kosong untuk {stock} {year}")
    candidate = href.strip()

    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValidationError(f"skema URL tidak diizinkan: {parsed.scheme!r} pada {candidate}")
    host = (parsed.hostname or "").lower()
    if host not in IDX_HOSTS:
        raise ValidationError(f"host di luar IDX: {host!r} pada {candidate}")

    # Percent-encoding is how IDX writes spaces in these paths, so the year and
    # period markers only match after decoding.
    path = unquote(parsed.path).lower()
    if not path.endswith(f"/{INSTANCE_FILENAME}"):
        raise ValidationError(f"bukan {INSTANCE_FILENAME}: {candidate}")
    if f"tahun {year}" not in path:
        raise ValidationError(f"tahun {year} tidak ada di URL: {candidate}")
    if f"/{AUDITED_SEGMENT.lower()}/" not in path:
        raise ValidationError(f"hanya laporan teraudit di /Audit/, bukan: {candidate}")

    expected = normalize_stock_code(stock).lower()
    if f"/{expected}/" not in path:
        raise ValidationError(f"URL milik saham lain, bukan {expected}: {candidate}")
    return candidate


__all__ = [
    "AUDITED_QUARTER",
    "AUDITED_SEGMENT",
    "IDX_DOWNLOAD_BASE",
    "INSTANCE_FILENAME",
    "instance_url",
    "validate_instance_url",
]
