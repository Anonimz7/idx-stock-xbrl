"""Decide what a run would do, before it does any of it.

The decision to skip or fetch is the part worth being able to ask about in
advance, because it is where the run touches the user's data: skipping means
trusting a recorded hash, fetching means overwriting a file. `--dry-run` exists
to answer "what would this run do" without answering it.

So the decision lives here, in one place, and both the real path and the dry run
ask the same function. A dry run that reimplemented the rule would be able to
disagree with reality -- and a dry run that is wrong in the reassuring
direction is worse than none, because it is trusted.

Nothing here writes. It reads the history and hashes the files that are already
on disk, which is exactly what the real path does before it starts, and stops.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..validation import normalize_stock_code, validate_report_url
from .hashing import file_sha256
from .history import history_entry, load_download_history
from .paths import final_report_path

ACTION_SKIP = "skip"
ACTION_DOWNLOAD = "download"
ACTION_REDOWNLOAD = "redownload"


@dataclass(frozen=True, slots=True)
class DownloadPlan:
    """What would happen to one report, and why."""

    stock: str
    year: int
    quarter: int
    href: str
    action: str
    reason: str
    final_path: str
    recorded_sha256: str = ""
    current_sha256: str = ""

    @property
    def would_download(self) -> bool:
        return self.action in {ACTION_DOWNLOAD, ACTION_REDOWNLOAD}

    @property
    def would_skip(self) -> bool:
        return self.action == ACTION_SKIP

    @property
    def history_needs_update(self) -> bool:
        """True when the real run would rewrite the history even while skipping.

        The file and the hash agree, but the URL or the recorded hash is missing,
        so the entry is incomplete and gets topped up. A dry run that hid this
        would claim "nothing changes" when in fact the JSON is rewritten.
        """
        return self.would_skip and self.reason == "history belum lengkap"


def plan_for(
    stock: str,
    year: int,
    quarter: int,
    href: str,
    download_dir: Path | None = None,
    history: dict[str, Any] | None = None,
) -> DownloadPlan:
    """Return what the run would do for one report, changing nothing."""
    code = normalize_stock_code(stock)
    # Raises on an off-host or mismatched URL, exactly as the real path does, so
    # a dry run cannot report "fine" for something the run would reject.
    validate_report_url(href, code, year, quarter)

    document = history if history is not None else load_download_history(download_dir)
    final_path = final_report_path(code, year, quarter, download_dir)
    recorded = history_entry(document, code, year, quarter)
    recorded_hash = str((recorded or {}).get("sha256") or "")

    def _plan(action: str, reason: str, current: str = "") -> DownloadPlan:
        return DownloadPlan(
            stock=code,
            year=year,
            quarter=quarter,
            href=href,
            action=action,
            reason=reason,
            final_path=str(final_path),
            recorded_sha256=recorded_hash,
            current_sha256=current,
        )

    if not final_path.is_file() or final_path.stat().st_size == 0:
        if recorded is not None:
            return _plan(ACTION_DOWNLOAD, "JSON mencatat, tetapi file tidak ada")
        return _plan(ACTION_DOWNLOAD, "belum pernah diunduh")

    current = file_sha256(final_path)
    if recorded_hash and recorded_hash != current:
        return _plan(ACTION_REDOWNLOAD, "hash file tidak cocok dengan JSON", current)

    if recorded is None or recorded.get("url") != href or not recorded_hash:
        return _plan(ACTION_SKIP, "history belum lengkap", current)
    return _plan(ACTION_SKIP, "sudah tercatat dan hash valid", current)


__all__ = [
    "ACTION_DOWNLOAD",
    "ACTION_REDOWNLOAD",
    "ACTION_SKIP",
    "DownloadPlan",
    "plan_for",
]
