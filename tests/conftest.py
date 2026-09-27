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
