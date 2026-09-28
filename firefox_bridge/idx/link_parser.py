"""Parsing rules that turn IDX snapshot elements into report links."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from ..validation import IDX_HOSTS
from .models import ReportLink
from .selectors import snapshot_elements

REPORT_FILENAME_MARKER = "inlinexbrl"
REPORT_FILE_SUFFIX = ".zip"
AUDIT_PATH_SEGMENT = "/audit/"
_QUARTER_PATH_PATTERN = re.compile(r"/tw([1-4])/")


def year_path_markers(year: int) -> tuple[str, str]:
    """Return the accepted URL fragments for one reporting year."""
    return (f"tahun%20{year}".lower(), f"tahun {year}".lower())


def is_idx_host(href: str) -> bool:
    """Return True only for a URL that IDX itself would serve.

    Recognition is a decision about *where* a link points, not just what it is
    called. A path fragment such as `/Laporan%20Keuangan%20Tahun%202025/TW1/...`
    can be written on any host at all, so a name-based match alone would accept
    a link that sends the download somewhere else entirely.
    """
    try:
        parsed = urlparse(href.strip())
    except ValueError:
        return False
    return (parsed.scheme or "").lower() in {"http", "https"} and (
        (parsed.hostname or "").lower() in IDX_HOSTS
    )


def is_report_link(href: str, year: int) -> bool:
    """Return True when the href is an inlineXBRL archive for the given year."""
    candidate = href.strip()
    if not is_idx_host(candidate):
        return False
    normalized = candidate.lower()
    if not normalized.endswith(REPORT_FILE_SUFFIX):
        return False
    if REPORT_FILENAME_MARKER not in normalized:
        return False
    return any(marker in normalized for marker in year_path_markers(year))


def quarter_from_report_href(href: str) -> int | None:
    """Map IDX TW1-TW3 and Audit paths to report numbers 1-4."""
    normalized = href.lower()
    match = _QUARTER_PATH_PATTERN.search(normalized)
    if match:
        return int(match.group(1))
    if AUDIT_PATH_SEGMENT in normalized:
        return 4
    return None


def find_inline_xbrl_links(
    snapshot: dict[str, Any],
    year: int,
) -> list[dict[str, Any]]:
    """Return every recognized inlineXBRL report link for one IDX year.

    Results are ordered by quarter and only the first link per quarter is kept,
    so a duplicated DOM node can never produce a duplicate download.
    """
    by_quarter: dict[int, dict[str, Any]] = {}
    for element in snapshot_elements(snapshot):
        href = (element.get("href") or "").strip()
        if not is_report_link(href, year):
            continue
        quarter = quarter_from_report_href(href)
        if quarter is not None:
            by_quarter.setdefault(quarter, element)
    return [by_quarter[quarter] for quarter in sorted(by_quarter)]


def find_inline_xbrl_link(
    snapshot: dict[str, Any],
    year: int,
    quarter: int,
) -> dict[str, Any] | None:
    """Return the link element for one quarter, or None when it is absent."""
    for element in find_inline_xbrl_links(snapshot, year):
        if quarter_from_report_href(element.get("href") or "") == quarter:
            return element
    return None


def report_links(snapshot: dict[str, Any], year: int) -> list[ReportLink]:
    """Return typed report links for one IDX year, ordered by quarter."""
    links: list[ReportLink] = []
    for element in find_inline_xbrl_links(snapshot, year):
        href = (element.get("href") or "").strip()
        quarter = quarter_from_report_href(href)
        if quarter is None:
            continue
        links.append(
            ReportLink(
                ref=str(element.get("ref") or ""),
                href=href,
                quarter=quarter,
                element=element,
            )
        )
    return links
