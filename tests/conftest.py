"""Shared test helpers for the production test suite."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from firefox_bridge.logging_config import reset_logging

FIXTURES = Path(__file__).parent / "fixtures"
IDX_SNAPSHOT = FIXTURES / "idx_nckl_2025_snapshot.json"


@pytest.fixture(autouse=True)
def isolated_log_dir(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Keep the test suite out of the developer's real log file.

    The downloader narrates itself through the logger, so any test that exercises
    it writes records. Without this, running `pytest` appends a few hundred lines
    of fake stock codes and pytest temp paths to `logs/bridge.log` -- the one
    file a person reads when a real run misbehaves. A log that cannot be trusted
    is worse than no log, so the suite gets its own.
    """
    log_dir = tmp_path_factory.mktemp("suite-log")
    monkeypatch.setenv("FIREFOX_BRIDGE_LOG_DIR", str(log_dir))
    reset_logging()
    yield
    reset_logging()


@pytest.fixture(autouse=True)
def isolated_download_dir(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Keep the test suite out of the developer's real `Downloads/saham`.

    This one is not cosmetic. Every path helper takes an optional download root
    and falls back to `%USERPROFILE%\\Downloads` when it is omitted -- so a test
    that calls the CLI without `--download-dir` resolves against the real
    folder. `scan_staging` then clears stale files and prunes directories there.

    Found by measurement, not review: a marker directory placed in the real
    staging folder did not survive a full `pytest` run. Pointing the whole suite
    at a temp root makes the fallback unreachable from a test.
    """
    root = tmp_path_factory.mktemp("suite-downloads")
    monkeypatch.setenv("FIREFOX_BRIDGE_DOWNLOAD_DIR", str(root))
    yield


def make_snapshot(elements: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a minimal snapshot document for the given elements."""
    return {"elements": elements}


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove real sleeping so pacing tests stay fast but still observable."""
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)


@pytest.fixture
def idx_snapshot() -> dict[str, Any]:
    """Return the captured IDX Laporan Keuangan snapshot used as regression base."""
    with IDX_SNAPSHOT.open("r", encoding="utf-8") as handle:
        return json.load(handle)
