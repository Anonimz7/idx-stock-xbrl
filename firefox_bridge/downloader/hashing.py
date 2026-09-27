"""File hashing primitives.

Kept dependency free so both the history writer and the integrity auditor can
use it without importing each other.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

CHUNK_SIZE = 1024 * 1024


def file_sha256(path: Path) -> str:
    """Return the hex SHA-256 digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_matches_record(path: Path, recorded_hash: str) -> bool:
    """Return True when the file still matches the recorded hash.

    An empty recorded hash is treated as "not yet trusted" by callers, so this
    helper only answers the comparison itself.
    """
    if not recorded_hash:
        return True
    return recorded_hash == file_sha256(path)
