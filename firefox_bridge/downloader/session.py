"""Named download sessions.

A session is a named run: every processed stock is recorded, so starting the
program with an existing session name continues from the last processed stock
instead of re-visiting the whole list. Session files live next to the download
history, under ``<download-dir>/sessions/<name>.json``.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .paths import download_root

SESSION_VERSION = 1
_SESSION_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def validate_session_name(name: str | None) -> str:
    """Return the stripped name, or raise ``ValueError`` when it is unsafe."""
    cleaned = (name or "").strip()
    if not _SESSION_NAME_RE.fullmatch(cleaned):
        raise ValueError(
            "nama sesi hanya boleh huruf/angka/dash/underscore, "
            "maksimal 64 karakter, diawali huruf atau angka"
        )
    return cleaned


def sessions_dir(download_dir: Path | None = None) -> Path:
    """Return the sessions directory, creating it on first use."""
    directory = download_root(download_dir) / "sessions"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def session_path(download_dir: Path | None, name: str) -> Path:
    """Return the JSON file for one session name."""
    return sessions_dir(download_dir) / f"{validate_session_name(name)}.json"


def new_session(name: str, year: int) -> dict[str, Any]:
    """Build a fresh session document."""
    now = datetime.now(UTC).isoformat()
    return {
        "version": SESSION_VERSION,
        "name": validate_session_name(name),
        "year": year,
        "created": now,
        "updated": now,
        "stocks_done": [],
        "last_stock": None,
    }


def load_session(download_dir: Path | None, name: str) -> dict[str, Any] | None:
    """Load an existing session, or return None when the name is new."""
    path = session_path(download_dir, name)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("stocks_done"), list):
        raise ValueError(f"Format JSON sesi tidak valid: {path}")
    return data


def save_session(download_dir: Path | None, session: dict[str, Any]) -> Path:
    """Write the session atomically so a crash cannot truncate it."""
    path = session_path(download_dir, session["name"])
    session["updated"] = datetime.now(UTC).isoformat()
    temporary = path.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(session, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    temporary.replace(path)
    return path


def mark_stock_done(session: dict[str, Any], stock: str) -> None:
    """Record one processed stock (success, failure, or pre-check skip)."""
    code = stock.upper()
    if code not in session["stocks_done"]:
        session["stocks_done"].append(code)
    session["last_stock"] = code


def list_sessions(download_dir: Path | None = None) -> list[dict[str, Any]]:
    """Summarize every known session, most recently updated first."""
    summaries: list[dict[str, Any]] = []
    directory = sessions_dir(download_dir)
    for path in sorted(directory.glob("*.json")):
        if path.suffixes[-2:] == [".json", ".tmp"] or path.name.endswith(".tmp"):
            continue
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        done = data.get("stocks_done")
        summaries.append(
            {
                "name": data.get("name", path.stem),
                "year": data.get("year"),
                "done": len(done) if isinstance(done, list) else 0,
                "last_stock": data.get("last_stock"),
                "updated": data.get("updated"),
            }
        )
    summaries.sort(key=lambda item: str(item.get("updated") or ""), reverse=True)
    return summaries


def prompt_session_name(download_dir: Path | None = None) -> str:
    """Ask the user for a session name: an existing one continues it."""
    known = list_sessions(download_dir)
    if known:
        print("Sesi yang sudah ada:")
        for item in known:
            print(
                f"  - {item['name']}: {item['done']} emiten diproses, "
                f"terakhir {item['last_stock']}, tahun {item['year']}"
            )
        print("Ketik nama sesi yang sudah ada untuk melanjutkan,")
    print("atau ketik nama baru untuk mulai sesi baru.")
    while True:
        try:
            answer = input("Nama sesi: ")
        except EOFError:
            raise ValueError("tidak ada input; gunakan --session NAMA")
        try:
            return validate_session_name(answer)
        except ValueError as error:
            print(f"Nama tidak valid: {error}")
