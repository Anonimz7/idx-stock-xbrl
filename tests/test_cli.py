"""CLI contract: arguments, exit codes, and run summary."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from firefox_bridge import cli
from firefox_bridge.cli import (
    EXIT_FAILURES,
    EXIT_INVALID_INPUT,
    EXIT_SUCCESS,
    build_parser,
    main,
    parse_stock_codes,
    run,
)
from firefox_bridge.downloader.models import DownloadResult
from firefox_bridge.downloader.reporting import print_run_summary


def test_all_detected_is_the_documented_default_path() -> None:
    args = build_parser().parse_args(["--stocks", "NCKL", "--all-detected"])

    assert args.all_detected is True
    assert args.all_quarters is False
    assert args.quarter == 4
    assert args.year == 2025


def test_all_quarters_remains_a_legacy_alias() -> None:
    args = build_parser().parse_args(["--stocks", "NCKL", "--all-quarters"])

    assert args.all_quarters is True


def test_delay_below_one_second_is_rejected() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--stocks", "NCKL", "--delay", "0.5"])


def test_parse_stock_codes_normalizes_and_deduplicates() -> None:
    assert parse_stock_codes(" nckl , BBCA ,nckl, ") == ["NCKL", "BBCA"]
    assert parse_stock_codes(" , ,") == []


def test_run_rejects_an_empty_stock_list() -> None:
    assert run(["--stocks", " , "]) == EXIT_INVALID_INPUT


def test_run_reports_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[str, int, Path | None]] = []

    def fake_download_all(
        _client: Any,
        stock: str,
        year: int,
        download_dir: Path | None,
    ) -> list[DownloadResult]:
        calls.append((stock, year, download_dir))
        return [DownloadResult(stock, f"{stock}/TW1", str(tmp_path))]

    monkeypatch.setattr(cli, "download_all_detected", fake_download_all)
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)

    exit_code = run(
        [
            "--stocks",
            "NCKL,BBCA",
            "--year",
            "2025",
            "--all-detected",
            "--delay",
            "1",
            "--download-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == EXIT_SUCCESS
    assert [stock for stock, _year, _dir in calls] == ["NCKL", "BBCA"]
    assert calls[0][2] == tmp_path


def test_run_continues_after_a_failed_stock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempted: list[str] = []

    def fake_download_all(_client: Any, stock: str, *_args: Any) -> list[DownloadResult]:
        attempted.append(stock)
        if stock == "BBCA":
            raise RuntimeError("extension disconnected")
        return [DownloadResult(stock, f"{stock}/TW1", "report.zip")]

    monkeypatch.setattr(cli, "download_all_detected", fake_download_all)
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)

    exit_code = run(["--stocks", "NCKL,BBCA", "--all-detected", "--delay", "1"])

    assert attempted == ["NCKL", "BBCA"]
    assert exit_code == EXIT_FAILURES


def test_run_downloads_a_single_quarter(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []

    def fake_download_stock(
        _client: Any,
        _stock: str,
        _year: int,
        quarter: int,
        _download_dir: Path | None,
    ) -> DownloadResult:
        seen.append(quarter)
        return DownloadResult("NCKL", "NCKL/TW2", "report.zip")

    monkeypatch.setattr(cli, "download_stock", fake_download_stock)
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)

    assert run(["--stocks", "NCKL", "--quarter", "2", "--delay", "1"]) == EXIT_SUCCESS
    assert seen == [2]


def test_run_summary_exit_code() -> None:
    summary = DownloadResult("NCKL", "u", "f")
    from firefox_bridge.downloader.models import RunSummary

    assert RunSummary(results=[summary]).exit_code == EXIT_SUCCESS
    assert RunSummary(failures=["NCKL: boom"]).exit_code == EXIT_FAILURES


def test_print_run_summary_lists_results_and_failures(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from firefox_bridge.downloader.models import RunSummary

    print_run_summary(
        RunSummary(
            results=[DownloadResult("NCKL", "u", "NCKL.zip")],
            failures=["BBCA: boom"],
        )
    )

    output = capsys.readouterr().out
    assert "Successful: 1" in output
    assert "Failed:     1" in output
    assert "BBCA: boom" in output
    assert "NCKL.zip" in output


def test_main_delegates_to_run(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str] | None] = []
    monkeypatch.setattr(cli, "run", lambda argv: seen.append(argv) or 0)  # type: ignore[arg-type]

    assert main(["--stocks", "NCKL"]) == 0
    assert seen == [["--stocks", "NCKL"]]
