"""Filesystem operations around a Firefox download."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

TEMPORARY_SUFFIXES = (".crdownload", ".part")
DEFAULT_TIMEOUT_SECONDS = 180.0


def is_download_complete(path: Path) -> bool:
    """Return True when the file exists, is non-empty, and has no temp sibling."""
    if not path.is_file():
        return False
    if any(Path(f"{path}{suffix}").exists() for suffix in TEMPORARY_SUFFIXES):
        return False
    return path.stat().st_size > 0


def wait_for_completed_download(
    path: Path,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> int:
    """Wait until Firefox finished writing the staged download and return its size.

    A single unchanged size sample is enough because Firefox keeps the temp
    sibling in place for the whole transfer.
    """
    deadline = time.monotonic() + timeout
    previous_size = -1
    while time.monotonic() < deadline:
        if is_download_complete(path):
            size = path.stat().st_size
            if size > 0 and size == previous_size:
                return size
            previous_size = size
        else:
            previous_size = -1
        time.sleep(1)
    raise TimeoutError(f"Download tidak selesai dalam {timeout:.0f} detik: {path}")


def move_completed_download(
    source: Path,
    destination: Path,
    *,
    replace: bool = False,
) -> None:
    """Move a completed staged archive to its final home.

    ``replace`` is only set after an integrity failure, so a corrupt archive is
    overwritten deliberately rather than silently kept.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if replace or destination.is_file() and destination.stat().st_size == 0:
            destination.unlink()
        elif source.is_file() and source.stat().st_size == destination.stat().st_size:
            source.unlink()
            return
        else:
            raise FileExistsError(f"File tujuan sudah ada: {destination}")
    shutil.move(str(source), str(destination))
