"""CLI contract: arguments, exit codes, and run summary."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest
from firefox_bridge import cli, runconfig
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


def _parse(argv: list[str], config: str | None = None) -> argparse.Namespace:
    """Parse and resolve, exactly as `run()` does.

    Stopping at `parse_args` would assert on a namespace the real program never
    uses: since CLI-003 the parser leaves every unset option at `None`, and the
    defaults are applied afterwards so the config file gets a chance to supply
    them first.
    """
    args = build_parser().parse_args(argv)
    runconfig.resolve(args, config)
    return args


def test_all_detected_is_the_documented_default_path() -> None:
    args = _parse(["--stocks", "NCKL", "--all-detected"])

    assert args.all_detected is True
    assert args.all_quarters is False
    assert args.quarter == 4
    assert args.year == 2025


def test_all_quarters_remains_a_legacy_alias() -> None:
    args = _parse(["--stocks", "NCKL", "--all-quarters"])

    assert args.all_quarters is True
    assert args.all_detected is False, "an unset alias is False, not None"


def test_delay_below_one_second_is_rejected() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--stocks", "NCKL", "--delay", "0.5"])


def test_a_year_outside_the_sanity_bound_is_rejected() -> None:
    """Before CLI-003, `--year 20255` was accepted and quietly downloaded nothing."""
    for value in ("1899", "20255", "0"):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["--stocks", "NCKL", "--year", value])
    assert _parse(["--stocks", "NCKL", "--year", "1990"]).year == 1990
    assert _parse(["--stocks", "NCKL", "--year", "2100"]).year == 2100


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
            "--session",
            "sesi-lapor-ok",
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

    exit_code = run(
        ["--stocks", "NCKL,BBCA", "--all-detected", "--delay", "1",
         "--session", "sesi-lanjut-gagal"]
    )

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

    assert (
        run(
            [
                "--stocks",
                "NCKL",
                "--quarter",
                "2",
                "--delay",
                "1",
                "--session",
                "sesi-kuartal",
            ]
        )
        == EXIT_SUCCESS
    )
    assert seen == [2]


def test_run_skips_history_complete_stock_without_page_visit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Emiten yang tahunnya sudah lengkap di history tidak dibuka halamannya."""
    from firefox_bridge.downloader.history import empty_history, record_download_history
    from firefox_bridge.downloader.paths import final_report_path
    from firefox_bridge.downloader.session import load_session

    history = empty_history()
    path = final_report_path("NCKL", 2025, 1, tmp_path)
    path.write_bytes(b"PK\x03\x04test")
    record_download_history(history, "NCKL", 2025, 1, "u", path, tmp_path)

    calls: list[str] = []

    def fake_download_all(
        _client: Any, stock: str, _year: int, _download_dir: Path | None
    ) -> list[DownloadResult]:
        calls.append(stock)
        return []

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
            "--session",
            "sesi-skip-uji",
        ]
    )

    assert exit_code == EXIT_SUCCESS
    assert calls == ["BBCA"], "NCKL harus dilewati tanpa membuka halaman"
    session = load_session(tmp_path, "sesi-skip-uji")
    assert session is not None
    assert session["stocks_done"] == ["NCKL", "BBCA"]


def test_run_resumes_session_from_last_stock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Sesi yang sudah ada melanjutkan dari emiten terakhir, tanpa mengulang."""
    from firefox_bridge.downloader.session import (
        load_session,
        mark_stock_done,
        new_session,
        save_session,
    )

    session = new_session("sesi-lanjut", 2025)
    mark_stock_done(session, "NCKL")
    save_session(tmp_path, session)

    attempted: list[str] = []

    def fake_download_all(_client: Any, stock: str, *_args: Any) -> list[DownloadResult]:
        attempted.append(stock)
        return []

    monkeypatch.setattr(cli, "download_all_detected", fake_download_all)
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)

    exit_code = run(
        [
            "--stocks",
            "NCKL,BBCA,BBRI",
            "--year",
            "2025",
            "--all-detected",
            "--delay",
            "1",
            "--download-dir",
            str(tmp_path),
            "--session",
            "sesi-lanjut",
        ]
    )

    assert exit_code == EXIT_SUCCESS
    assert attempted == ["BBCA", "BBRI"]
    reloaded = load_session(tmp_path, "sesi-lanjut")
    assert reloaded is not None
    assert reloaded["stocks_done"] == ["NCKL", "BBCA", "BBRI"]
    assert reloaded["last_stock"] == "BBRI"


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


def _fake_all_downloads(stock: str, tmp_path: Path):
    def fake_download_all(
        _client: Any,
        code: str,
        year: int,
        download_dir: Path | None,
    ) -> list[DownloadResult]:
        return [DownloadResult(code, f"{code}/TW1", str(tmp_path))]

    return fake_download_all


def test_run_minutes_triggers_rest_between_stocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sleeps: list[float] = []
    state_seen_during_rest: list[bool] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        # Rehat terjadwal ~0.06 dtk (beda dari jeda antar-emiten 1 dtk):
        # penanda rest_state.json harus ada selama rehat, lalu dihapus setelahnya.
        if 0.055 < seconds < 0.065:
            state_seen_during_rest.append(
                (tmp_path / "rest_state.json").exists()
            )

    monkeypatch.setattr(cli, "download_all_detected", _fake_all_downloads("NCKL", tmp_path))
    monkeypatch.setattr(cli.time, "sleep", fake_sleep)

    exit_code = run(
        [
            "--stocks", "NCKL,BBCA",
            "--year", "2025",
            "--all-detected",
            "--delay", "1",
            "--run-minutes", "0.000001",  # window langsung habis setelah emiten pertama
            "--rest-minutes", "0.001",    # ~0.06 detik
            "--download-dir", str(tmp_path),
            "--session", "sesi-rehat",
        ]
    )

    assert exit_code == EXIT_SUCCESS
    long_sleeps = [s for s in sleeps if 0.055 < s < 0.065]
    assert long_sleeps, "rehat terjadwal tidak terjadi"
    assert all(pytest.approx(0.06) == s for s in long_sleeps)
    assert state_seen_during_rest and all(state_seen_during_rest)
    assert not (tmp_path / "rest_state.json").exists()  # dibersihkan setelah rehat


def test_run_minutes_disabled_means_no_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(cli, "download_all_detected", _fake_all_downloads("NCKL", tmp_path))
    monkeypatch.setattr(cli.time, "sleep", lambda s: sleeps.append(s))

    exit_code = run(
        [
            "--stocks", "NCKL",
            "--year", "2025",
            "--all-detected",
            "--delay", "1",
            "--download-dir", str(tmp_path),
            "--session", "sesi-tanpa-rehat",
        ]
    )

    assert exit_code == EXIT_SUCCESS
    assert not (tmp_path / "rest_state.json").exists()


def test_run_clears_stale_rest_state_on_startup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "rest_state.json").write_text('{"pause_until": 9999999999}', encoding="utf-8")
    monkeypatch.setattr(cli, "download_all_detected", _fake_all_downloads("NCKL", tmp_path))
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)

    exit_code = run(
        [
            "--stocks", "NCKL",
            "--year", "2025",
            "--all-detected",
            "--delay", "1",
            "--download-dir", str(tmp_path),
            "--session", "sesi-bersih",
        ]
    )

    assert exit_code == EXIT_SUCCESS
    assert not (tmp_path / "rest_state.json").exists()


def test_run_minutes_accepted_from_config_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "run.toml"
    config.write_text("run_minutes = 30\nrest_minutes = 30\n", encoding="utf-8")
    args = _parse(["--stocks", "NCKL", "--all-detected"], config=str(config))
    assert args.run_minutes == 30.0
    assert args.rest_minutes == 30.0
