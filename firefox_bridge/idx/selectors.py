"""Semantic selectors for the IDX Laporan Keuangan panel.

IDX exposes three search inputs once the panel is open: company code, period
(TWn), and year (20xx). Every selector here is deliberately strict so a year can
never be typed into the wrong control.
"""

from __future__ import annotations

import re
from typing import Any

YEAR_SEARCHBOX_PATTERN = re.compile(r"^\s*(20\d{2})(?:\s|$)")
LAPORAN_KEUANGAN_LABEL = "Laporan Keuangan"


def snapshot_elements(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the element list of a snapshot, tolerating malformed payloads."""
    elements = snapshot.get("elements") or []
    return [element for element in elements if isinstance(element, dict)]


def find_year_dropdown_ref(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Return only the searchable input whose accessible name starts with a year.

    There is deliberately no generic combobox fallback: a wrong match would type
    the year into the company-code box and silently break the run.
    """
    for element in snapshot_elements(snapshot):
        role = (element.get("role") or "").lower()
        name = element.get("name") or ""
        if role == "searchbox" and YEAR_SEARCHBOX_PATTERN.match(name):
            return element
    return None


def find_dropdown_option_ref(
    snapshot: dict[str, Any],
    target: str,
) -> dict[str, Any] | None:
    """Return one exact dropdown option, or None when absent or ambiguous."""
    clean_target = " ".join(target.split())
    matches = [
        element
        for element in snapshot_elements(snapshot)
        if (element.get("role") or "").lower() == "option"
        and " ".join((element.get("name") or "").split()) == clean_target
    ]
    return matches[0] if len(matches) == 1 else None


def find_laporan_keuangan_ref(snapshot: dict[str, Any]) -> str | None:
    """Return the ref of the Laporan Keuangan button, or None when absent."""
    for element in snapshot_elements(snapshot):
        role = (element.get("role") or "").lower()
        name = (element.get("name") or "").strip()
        if role == "button" and name == LAPORAN_KEUANGAN_LABEL:
            return str(element.get("ref") or "") or None
    return None


def selected_year(element: dict[str, Any] | None) -> int | None:
    """Return the year currently shown by a year searchbox element."""
    if element is None:
        return None
    match = YEAR_SEARCHBOX_PATTERN.match(element.get("name") or "")
    return None if match is None else int(match.group(1))
