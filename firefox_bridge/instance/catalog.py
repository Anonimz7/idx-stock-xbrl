"""Read the instance catalog -- the list of what IDX actually publishes.

The downloader used to build a URL for any ``(stock, year)`` it was handed,
which made the list a guess: a stock with no audited archive still produced a
plausible URL, and the 404 it returned was indistinguishable from an outage.
The catalog inverts that. It only contains what was seen, so every entry
asserts the archive exists, and a 404 on one of these URLs is a fact about
IDX rather than a fact about our guess.

Read here instead of imported from the watcher: the watcher knows how to
parse IDX's pages and API; the downloader needs only the JSON the watcher
already froze. Depending on it would drag the scanning stack into a program
whose job starts after scanning ends.

The reader is deliberately dumb about URLs. It checks that the JSON is a
catalog and that each entry of the requested year carries a ticker and a URL;
whether a URL is fetchable belongs to :func:`validate_instance_url`, which
already rejects the wrong host, year, period, issuer and archive name before
a byte moves. Two layers checking the same string would be two chances to
disagree.
"""

from __future__ import annotations

import json
from pathlib import Path

from .urls import instance_url

HINT = (
    "  Bangun dulu: python -m idx_watcher.instance_catalog --source api "
    "--api-file <file JSON hasil unduhan>"
)


class CatalogError(Exception):
    """The catalog cannot be read, or one of its entries is unusable."""


def load_entries(path: Path | str, year: int) -> list[tuple[str, str]]:
    """Return ``(code, url)`` for every entry of ``year``, sorted by code.

    Sorted because the catalog is a dict, so its order is whatever order the
    entries happened to be merged in -- which changes between scans. Walking
    codes in a stable order makes a resumed run report the same way as the
    run it resumed, and that comparability is the whole point of printing a
    summary.
    """
    handle = Path(path)
    if not handle.is_file():
        raise CatalogError(f"katalog tidak ditemukan: {handle}\n{HINT}")

    try:
        raw = json.loads(handle.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise CatalogError(f"{handle} tidak bisa dibaca: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogError(f"{handle} bukan JSON valid: {exc}") from exc

    if not isinstance(raw, dict) or not isinstance(raw.get("entries"), dict):
        raise CatalogError(f"{handle} bukan katalog (tidak ada kunci `entries`)\n{HINT}")

    found: dict[str, str] = {}
    for key, item in raw["entries"].items():
        if not isinstance(item, dict):
            raise CatalogError(f"entri {key!r} bukan objek")
        if str(item.get("year")) != str(year):
            continue
        code = str(item.get("ticker") or "").strip().upper()
        url = str(item.get("url") or "").strip()
        if not code or not url:
            raise CatalogError(f"entri {key!r} tanpa ticker atau url")
        if code in found:
            raise CatalogError(f"ticker ganda untuk tahun {year}: {code}")
        found[code] = url

    if not found:
        available = sorted(
            {
                str(item.get("year"))
                for item in raw["entries"].values()
                if isinstance(item, dict) and item.get("year") is not None
            }
        )
        raise CatalogError(
            f"{handle} tidak memuat entri tahun {year}"
            f" (memuat tahun: {', '.join(available) or 'tidak ada'})"
        )
    return sorted(found.items())


def divergent(entries: list[tuple[str, str]], year: int) -> list[str]:
    """Return codes whose catalog URL has drifted from the published pattern.

    The catalog is fetched by hand, so a stale or truncated file is a real
    possibility (rencana-katalog.md, jebakan 18). A URL that no longer matches
    the shape this program has always reproduced is worth saying out loud
    before anything is fetched -- and then using anyway, because the catalog
    is the source of truth, not this check.
    """
    return [code for code, url in entries if url != instance_url(code, year)]


__all__ = ["CatalogError", "divergent", "load_entries"]
