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
from firefox_bridge.downloader.errors import DownloadTimeout
from firefox_bridge.instance.cli import _failure_reason, _stock_delay, build_parser, run


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


# --- which diagnosis gets reported ----------------------------------------


class _Prober:
    """A warm-up tab that answers when asked, and counts how often it was."""

    def __init__(self, answer: str = "Cloudflare challenge") -> None:
        self.answer = answer
        self.calls = 0

    def probe_archive_reason(self, stock: str, year: int) -> str:  # noqa: ARG002
        self.calls += 1
        return self.answer


def test_a_reason_the_retry_loop_already_found_is_not_probed_again() -> None:
    """The 404 that ended the retries is the 404 that gets reported.

    Asking twice would buy a second navigation and could disagree with the very
    answer the run stopped on -- the recorded reason and the decision that was
    made would then be two different stories about one failure.
    """
    session = _Prober()
    failure = DownloadTimeout("tidak pernah menulis apa pun")
    failure.archive_reason = "404 Not Found"

    reason = _failure_reason(session, "ADES", 2025, failure)

    assert reason == "404 Not Found"
    assert session.calls == 0, "a diagnosis already in hand was bought twice"


def test_a_failure_nobody_diagnosed_is_probed_here() -> None:
    """No session during the retry, or an error that never reached the probe."""
    session = _Prober(answer="Cloudflare challenge")

    reason = _failure_reason(session, "ADES", 2025, DownloadTimeout("stuck"))

    assert reason == "Cloudflare challenge"
    assert session.calls == 1


def test_a_probe_that_fails_keeps_the_failure_line_readable() -> None:
    """Diagnosis is best effort: it reports what went wrong, and nothing more."""
    session = _Prober()

    def broken(stock: str, year: int) -> str:
        raise RuntimeError("bridge mati")

    session.probe_archive_reason = broken  # type: ignore[method-assign]

    reason = _failure_reason(session, "ADES", 2025, DownloadTimeout("stuck"))

    assert reason.startswith("probe gagal (RuntimeError")


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
