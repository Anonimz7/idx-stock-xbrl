"""The staging folder is scratch space, and scratch space gets cleared.

Firefox writes a report into `saham/staging/<YEAR>/<STOCK>/` and Python moves it
to its final home afterwards. A run killed between those two steps leaves a
partial file behind, and the next run finds it at exactly the path it is about to
download to.

That is not hypothetical. Measured: a leftover partial file with no temp sibling
is reported as a finished download, because the wait loop only requires the size
to hold steady across two samples -- and a file nobody is writing always holds
steady. The archive check downstream rejects the truncated result, so the file
never gets recorded as a report, but the run still fails confusingly and the
leftover stays forever.

The invariant that fixes it: nothing in staging is worth keeping. A completed
archive there has not been written to history yet, so discarding it costs one
re-download and removes the possibility of mistaking it for a fresh one.

The one exception is a download genuinely in progress. Firefox keeps a
`.crdownload` or `.part` sibling for the whole transfer, and that is the signal
to leave everything alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .filesystem import TEMPORARY_SUFFIXES
from .paths import SAHAM_FOLDER, STAGING_FOLDER, download_root


def staging_root(download_dir: Path | None = None) -> Path:
    """Return the staging folder for a download root."""
    return download_root(download_dir) / SAHAM_FOLDER / STAGING_FOLDER


@dataclass(frozen=True)
class StagingScan:
    """What a staging scan found and did."""

    removed: list[Path] = field(default_factory=list)
    kept_in_flight: list[Path] = field(default_factory=list)
    empty_dirs_removed: list[Path] = field(default_factory=list)
    examined: int = 0

    @property
    def clean(self) -> bool:
        return not self.removed and not self.kept_in_flight


def _is_in_flight(path: Path) -> bool:
    """Return True while Firefox is still writing this download.

    The sibling name follows the convention `is_download_complete` uses: the
    suffix is appended to the whole path, giving `report.zip.crdownload`.
    """
    return any(Path(f"{path}{suffix}").exists() for suffix in TEMPORARY_SUFFIXES)


def _temp_sibling(path: Path) -> tuple[str, Path] | None:
    """Return ``(suffix, target)`` when `path` is itself a Firefox temp file.

    A temp file has to be visited explicitly, not skipped: it belongs to the
    download it is named after, and deleting it would abort a transfer in
    progress. Left alone while the target exists; removed as an orphan once the
    target is gone, which is what an interrupted run leaves behind.
    """
    for suffix in TEMPORARY_SUFFIXES:
        if path.name.endswith(suffix):
            return suffix, path.with_name(path.name[: -len(suffix)])
    return None


def scan_staging(staging_dir: Path, *, dry_run: bool = False) -> StagingScan:
    """Report and clear stale files under `staging_dir`.

    In-flight downloads are reported and left untouched, along with the empty
    directories that still contain them. `dry_run` reports what would go without
    touching the filesystem.
    """
    if not staging_dir.is_dir():
        return StagingScan()

    removed: list[Path] = []
    kept: list[Path] = []
    examined = 0

    for path in sorted(staging_dir.rglob("*")):
        if path.is_dir():
            continue

        sibling = _temp_sibling(path)
        if sibling is not None:
            _suffix, target = sibling
            if target.exists():
                continue  # still in flight; the target's own pass keeps it
            removed.append(path)  # orphaned by an interrupted run
        else:
            examined += 1
            if _is_in_flight(path):
                kept.append(path)
                continue
            removed.append(path)

        if not dry_run:
            path.unlink(missing_ok=True)

    emptied: list[Path] = []
    if not dry_run:
        # Deepest first, so a folder emptied by the pass above is itself removed.
        for directory in sorted(
            (path for path in staging_dir.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            if not any(directory.iterdir()):
                directory.rmdir()
                emptied.append(directory)

    return StagingScan(
        removed=removed,
        kept_in_flight=kept,
        empty_dirs_removed=emptied,
        examined=examined,
    )


def describe(scan: StagingScan) -> str:
    """Return a one-line summary suitable for a progress line."""
    if scan.examined == 0:
        return "staging bersih"
    parts = [f"{len(scan.removed)} file basi dibuang"]
    if scan.kept_in_flight:
        parts.append(f"{len(scan.kept_in_flight)} unduhan berjalan dipertahankan")
    if scan.empty_dirs_removed:
        parts.append(f"{len(scan.empty_dirs_removed)} folder kosong dibuang")
    return "staging: " + ", ".join(parts)


__all__ = [
    "StagingScan",
    "describe",
    "scan_staging",
    "staging_root",
]
