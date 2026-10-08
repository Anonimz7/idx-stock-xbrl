"""Typed domain models for a detected IDX report."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

AUDIT_QUARTER = 4


@dataclass(frozen=True, slots=True)
class ReportLink:
    """One inlineXBRL report link detected inside an IDX snapshot."""

    ref: str
    href: str
    quarter: int
    element: Mapping[str, Any]

    @property
    def period_label(self) -> str:
        """Return the IDX period folder name for this report."""
        return "Audit" if self.quarter == AUDIT_QUARTER else f"TW{self.quarter}"


@dataclass(frozen=True, slots=True)
class ReportTarget:
    """A single report that is expected to exist on disk."""

    stock: str
    year: int
    quarter: int

    @property
    def label(self) -> str:
        return f"{self.stock} {self.year} TW{self.quarter}"
