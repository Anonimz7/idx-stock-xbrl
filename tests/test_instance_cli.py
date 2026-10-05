"""The instance CLI's own contract: arguments in, exit code out.

No browser and no bridge are involved. Everything here runs before a download is
attempted, which is exactly why it deserves its own file: a defect on this path
takes down a run of nearly a thousand tickers, and only after the first stock has
already been processed -- late enough that the time lost is the reason for
running them in one pass.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from firefox_bridge import runconfig
from firefox_bridge.instance.cli import _stock_delay, build_parser, run


def _parse(*argv: str) -> argparse.Namespace:
    return build_parser().parse_args(["--stocks", "NCKL", "--year", "2025", *argv])


def test_an_unset_delay_falls_back_to_the_shared_default() -> None:
    """A missing ``--delay`` must not reach ``time.sleep(None)``.

    The page-flow CLI gets this value from ``runconfig.resolve``, which fills
    every unset option from the config file or the defaults. This program takes
    no config file, so it has to read the same default itself -- and the failure
    it avoids is a crash after the first stock, with the rest of the list never
    attempted.
    """
    args = _parse()

    assert args.delay is None
    assert _stock_delay(args) == runconfig.DEFAULT_DELAY_SECONDS


def test_an_explicit_delay_wins_over_the_default() -> None:
    assert _stock_delay(_parse("--delay", "5")) == 5


def test_dry_run_creates_no_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--dry-run`` is the mode that promises to change nothing; it must.

    Pointed at an empty root so the assertion is about this run rather than about
    whatever a developer already has in their download folder.
    """
    root = tmp_path / "instance"
    monkeypatch.setenv("FIREFOX_BRIDGE_INSTANCE_DIR", str(root))
    monkeypatch.delenv("FIREFOX_BRIDGE_DOWNLOAD_DIR", raising=False)

    exit_code = run(["--stocks", "NCKL,BBCA", "--year", "2025", "--dry-run"])

    assert exit_code == 0
    assert not root.exists()
    assert list(tmp_path.iterdir()) == []


def test_a_year_is_required() -> None:
    """Omitted options are caught before the browser is ever touched."""
    with pytest.raises(SystemExit) as caught:
        run(["--stocks", "NCKL"])

    assert caught.value.code == 2


def test_stocks_are_required() -> None:
    with pytest.raises(SystemExit) as caught:
        run(["--year", "2025"])

    assert caught.value.code == 2
