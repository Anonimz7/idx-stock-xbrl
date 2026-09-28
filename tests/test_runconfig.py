"""The config file: where it is found, what it may say, and what wins.

A config file is the one input that can change what a run does without anyone
passing an argument. The tests below are mostly about refusing: a file that is
silently half-honoured is worse than no file, because the run then disagrees
with the thing on disk that says what it should have done.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from firefox_bridge import cli, runconfig
from firefox_bridge.cli import EXIT_INVALID_INPUT, EXIT_SUCCESS

# --- helpers ---------------------------------------------------------------


def write_config(tmp_path: Path, body: str, name: str = "firefox-bridge.toml") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def resolve(argv: list[str], config: str | None = None) -> runconfig.Resolution:
    args = cli.build_parser().parse_args(argv)
    return runconfig.resolve(args, config)


# --- discovery -------------------------------------------------------------


def test_no_config_file_means_no_config(tmp_path: Path) -> None:
    assert runconfig.discover_config_path(cwd=tmp_path) is None


def test_a_file_in_the_working_directory_is_found(tmp_path: Path) -> None:
    write_config(tmp_path, 'year = 2024\n')

    found = runconfig.discover_config_path(cwd=tmp_path)

    assert found == tmp_path / runconfig.CONFIG_FILENAME


def test_the_environment_names_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = write_config(tmp_path, "year = 2023\n", name="scheduled.toml")
    monkeypatch.setenv(runconfig.CONFIG_ENV, str(elsewhere))

    assert runconfig.discover_config_path(cwd=tmp_path) == elsewhere


def test_an_explicit_path_beats_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from_env = write_config(tmp_path, "year = 2023\n", name="from-env.toml")
    explicit = write_config(tmp_path, "year = 2024\n", name="explicit.toml")
    monkeypatch.setenv(runconfig.CONFIG_ENV, str(from_env))

    assert runconfig.discover_config_path(str(explicit), cwd=tmp_path) == explicit


def test_a_named_config_that_does_not_exist_is_an_error(tmp_path: Path) -> None:
    """Asking for a file and quietly running without it is the worst outcome."""
    with pytest.raises(runconfig.ConfigError, match="tidak ditemukan"):
        runconfig.load_config(str(tmp_path / "scheduled.toml"))


# --- reading ---------------------------------------------------------------


def test_a_json_file_is_read(tmp_path: Path) -> None:
    path = write_config(tmp_path, '{"year": 2024, "all_detected": true}', "conf.json")

    values, _ = runconfig.load_config(str(path))

    assert values == {"year": 2024, "all_detected": True}


def test_the_format_comes_from_the_suffix_not_from_guessing(tmp_path: Path) -> None:
    """A file named .json must not be parsed as TOML on the chance it works."""
    path = write_config(tmp_path, "year = 2024\n", "conf.json")

    with pytest.raises(runconfig.ConfigError, match="tidak valid"):
        runconfig.load_config(str(path))


def test_an_unknown_suffix_is_refused_with_the_supported_ones(tmp_path: Path) -> None:
    path = write_config(tmp_path, "year = 2024\n", "conf.ini")

    with pytest.raises(runconfig.ConfigError, match=r"\.toml, \.json"):
        runconfig.load_config(str(path))


def test_a_file_that_cannot_be_read_is_a_config_error_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A locked or access-denied file must read as "unusable", not as a crash."""
    path = write_config(tmp_path, "year = 2024\n")

    def refuse(*_args: Any, **_kwargs: Any) -> str:
        raise PermissionError(13, "file is locked by another process")

    monkeypatch.setattr(Path, "read_text", refuse)

    with pytest.raises(runconfig.ConfigError) as error:
        runconfig.read_config(path)

    assert str(path) in str(error.value)
    assert "tidak bisa dibaca" in str(error.value)


def test_a_broken_file_names_the_file_and_the_reason(tmp_path: Path) -> None:
    path = write_config(tmp_path, "year = \n")

    with pytest.raises(runconfig.ConfigError) as error:
        runconfig.load_config(str(path))

    assert str(path) in str(error.value)


def test_a_config_saved_with_a_byte_order_mark_still_reads(tmp_path: Path) -> None:
    """PowerShell, Notepad and Visual Studio all write one on Windows.

    Found by running the real CLI, not by a test: `tomllib` rejects a BOM and
    blames line 1 column 1, which sends the reader hunting for a syntax error
    that is not there. This is the one encoding the reader is most likely to
    produce, so it is the one that must not fail.
    """
    path = tmp_path / "firefox-bridge.toml"
    path.write_bytes('year = 2024\ndelay = 2.0\n'.encode("utf-8-sig"))

    values, _ = runconfig.load_config(str(path))

    assert values == {"year": 2024, "delay": 2.0}


def test_a_json_config_saved_with_a_byte_order_mark_still_reads(tmp_path: Path) -> None:
    path = tmp_path / "conf.json"
    path.write_bytes(b'{"year": 2024}'.replace(b"{", b"\xef\xbb\xbf{", 1))

    values, _ = runconfig.load_config(str(path))

    assert values == {"year": 2024}


def test_a_config_that_is_not_a_table_is_refused(tmp_path: Path) -> None:
    path = write_config(tmp_path, "[1, 2, 3]", "conf.json")

    with pytest.raises(runconfig.ConfigError, match="key-value"):
        runconfig.load_config(str(path))


# --- what a config may say -------------------------------------------------


def test_an_unknown_key_is_refused_and_lists_what_is_available(tmp_path: Path) -> None:
    """Silently ignoring `yeer = 2024` would run the default year against the file's word."""
    path = write_config(tmp_path, "yeer = 2024\n")

    with pytest.raises(runconfig.ConfigError) as error:
        runconfig.load_config(str(path))

    message = str(error.value)
    assert "yeer" in message
    assert "year" in message, "the error should say what a key may be called"


def test_stocks_may_be_a_list_or_a_comma_string() -> None:
    """Either shape is fine; what matters is that both name the same stocks."""
    listed = runconfig.resolve_config({"stocks": ["NCKL", " BBCA "]}, Path("a"))
    joined = runconfig.resolve_config({"stocks": "NCKL, BBCA"}, Path("a"))

    assert cli.parse_stock_codes(listed["stocks"]) == ["NCKL", "BBCA"]
    assert cli.parse_stock_codes(joined["stocks"]) == ["NCKL", "BBCA"]


def test_a_quoted_boolean_is_refused(tmp_path: Path) -> None:
    """`dry_run = "false"` read as True would turn a rehearsal into a real run."""
    path = write_config(tmp_path, 'dry_run = "false"\n')

    with pytest.raises(runconfig.ConfigError, match="tanpa tanda kutip"):
        runconfig.load_config(str(path))


def test_a_number_written_as_text_is_accepted(tmp_path: Path) -> None:
    """Harmless to be lenient about: there is only one way it can parse."""
    values, _ = runconfig.load_config(str(write_config(tmp_path, 'year = "2024"\n')))

    assert values["year"] == 2024


def test_a_boolean_is_not_accepted_where_a_number_belongs(tmp_path: Path) -> None:
    """`year = true` is 1, and 1 is not a year anyone meant to request."""
    path = write_config(tmp_path, "year = true\n")

    with pytest.raises(runconfig.ConfigError, match="harus berupa angka"):
        runconfig.load_config(str(path))


def test_an_empty_text_value_is_refused(tmp_path: Path) -> None:
    path = write_config(tmp_path, 'report = "   "\n')

    with pytest.raises(runconfig.ConfigError, match="tidak boleh kosong"):
        runconfig.load_config(str(path))


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('stocks = [1, 2]', "daftar harus berisi teks saja"),
        ("stocks = 42", "harus teks 'NCKL,BBCA'"),
        ("report = 5", "harus teks, bukan int"),
        ('year = "duapuluh"', "harus berupa angka"),
        ("quarter = true", "harus berupa angka 1-4"),
        ('quarter = "awal"', "harus berupa angka 1-4"),
    ],
)
def test_wrong_types_are_refused_by_name(tmp_path: Path, body: str, expected: str) -> None:
    """Each of these is a plausible typo, and each is caught before the browser."""
    with pytest.raises(runconfig.ConfigError) as error:
        runconfig.load_config(str(write_config(tmp_path, body)))

    assert expected in str(error.value)
    assert str(tmp_path) in str(error.value), "and it says which file"


# --- the rules the file must not weaken ------------------------------------


def test_a_delay_below_one_second_is_refused_in_the_file_too(tmp_path: Path) -> None:
    """The one-second floor exists because of the IDX page, not because of the CLI.

    Enforcing it on the command line only would leave the fastest way to provoke
    exactly the mis-rendering the floor was added to prevent.
    """
    path = write_config(tmp_path, "delay = 0.2\n")

    with pytest.raises(runconfig.ConfigError, match="minimal 1 detik"):
        runconfig.load_config(str(path))
    assert runconfig.resolve_config({"delay": 1.0}, Path("a"))["delay"] == 1.0


def test_the_year_and_quarter_bounds_are_the_same_ones_the_cli_uses(
    tmp_path: Path,
) -> None:
    for body, expected in (("year = 20255\n", "20255"), ("quarter = 7\n", "1 dan 4")):
        with pytest.raises(runconfig.ConfigError) as error:
            runconfig.load_config(str(write_config(tmp_path, body)))
        assert expected in str(error.value)


def test_history_is_not_a_setting() -> None:
    """`history = "rebuild"` in a file would rewrite the history on every run.

    Not a missing feature: a repair command should be typed when you mean it, not
    discovered in a file that a scheduled task reads unattended.
    """
    assert "history" not in runconfig.FIELDS
    assert "all_quarters" not in runconfig.FIELDS, "an alias is not a setting"


# --- precedence ------------------------------------------------------------


def test_the_file_supplies_what_the_command_line_omits(tmp_path: Path) -> None:
    path = write_config(tmp_path, 'stocks = "NCKL,BBCA"\nyear = 2024\n')
    args = cli.build_parser().parse_args(["--config", str(path)])

    runconfig.resolve(args, str(path))

    assert args.stocks == "NCKL,BBCA"
    assert args.year == 2024


def test_the_command_line_overrides_the_file(tmp_path: Path) -> None:
    path = write_config(tmp_path, 'year = 2024\ndelay = 5.0\n')

    args = cli.build_parser().parse_args(["--config", str(path), "--year", "2021"])
    runconfig.resolve(args, str(path))

    assert args.year == 2021
    assert args.delay == 5.0, "the file still owns what the command line did not say"


def test_the_default_is_last_and_never_reported_as_given(tmp_path: Path) -> None:
    path = write_config(tmp_path, "year = 2024\n")
    args = cli.build_parser().parse_args([])

    resolution = runconfig.resolve(args, str(path))

    assert args.quarter == runconfig.DEFAULT_QUARTER
    assert "quarter" in resolution.from_default
    assert "year" in resolution.from_file
    assert "year" not in resolution.from_default


def test_cli_stocks_replace_file_stocks_rather_than_adding_to_them(tmp_path: Path) -> None:
    """`--stocks` is an override. Merging would make it impossible to narrow a run."""
    path = write_config(tmp_path, 'stocks = "NCKL,BBCA"\n')

    args = cli.build_parser().parse_args(["--config", str(path), "--stocks", "TLKM"])
    runconfig.resolve(args, str(path))

    assert args.stocks == "TLKM"


def test_a_config_named_on_the_command_line_is_used_without_being_discovered(
    tmp_path: Path,
) -> None:
    named = write_config(tmp_path, "year = 2022\n", "named.toml")
    write_config(tmp_path, "year = 2019\n")  # would win if discovery ran

    assert resolve(["--config", str(named)], str(named)).values["year"] == 2022


# --- what the run says about it --------------------------------------------


def test_the_loaded_file_is_reported_not_applied_silently(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    path = write_config(tmp_path, "year = 2024\n")
    args = cli.build_parser().parse_args(["--config", str(path)])

    line = runconfig.describe(runconfig.resolve(args, str(path)))
    cli.progress(line, config=str(path))

    printed = capsys.readouterr().out
    assert str(path) in printed, "the user must be able to see which file was read"
    assert "year" in printed, "and which of their options it actually supplied"


def test_describe_says_when_there_was_no_file() -> None:
    args = cli.build_parser().parse_args([])

    line = runconfig.describe(runconfig.resolve(args, None))

    assert "tidak ada file" in line


def test_a_bad_config_exits_two_without_touching_the_browser(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    path = write_config(tmp_path, "yeer = 2024\n")

    code = cli.run(["--config", str(path), "--stocks", "NCKL", "--year", "2025"])

    assert code == EXIT_INVALID_INPUT
    assert "Config tidak valid" in capsys.readouterr().err


def test_stock_codes_from_a_config_still_go_through_validation(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Validation is the choke point, so it must not care where the code came from."""
    path = write_config(tmp_path, 'stocks = "../evil"\n')

    code = cli.run(["--config", str(path)])

    assert code == EXIT_INVALID_INPUT
    assert "tidak valid" in capsys.readouterr().err


class _StubBridge:
    """Just enough client to get past the extension health gate."""

    def status(self) -> dict[str, Any]:
        return {"connected": True, "extension": {"version": "0.1.8"}}


def test_a_config_reaches_the_dry_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end, with only the browser stubbed out."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(cli, "FirefoxBridgeClient", lambda *_a, **_k: _StubBridge())
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        cli,
        "plan_stock_year",
        lambda _c, stock, year, _d: calls.append({"stock": stock, "year": year}) or [],
    )
    path = write_config(tmp_path, 'stocks = "NCKL,BBCA"\nyear = 2024\ndry_run = true\n')

    code = cli.run(["--config", str(path)])

    assert code == EXIT_SUCCESS
    assert calls == [
        {"stock": "NCKL", "year": 2024},
        {"stock": "BBCA", "year": 2024},
    ]


def test_a_config_can_set_the_download_root(tmp_path: Path) -> None:
    path = write_config(tmp_path, f'download_dir = "{tmp_path.as_posix()}"\n')
    args = cli.build_parser().parse_args(["--config", str(path)])

    runconfig.resolve(args, str(path))

    assert args.download_dir == tmp_path.as_posix()


def test_the_report_records_where_each_option_came_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A report that says what succeeded but not what was asked cannot be audited."""
    from firefox_bridge.downloader.models import RunSummary
    from firefox_bridge.downloader.run_report import build_run_report

    path = write_config(tmp_path, 'year = 2024\ndelay = 2.0\n')
    args = cli.build_parser().parse_args(["--config", str(path), "--stocks", "NCKL"])
    resolution = runconfig.resolve(args, str(path))

    block = cli._command_block(args, cli.parse_stock_codes(args.stocks), resolution)
    report = build_run_report(
        summary=RunSummary(),
        failures=[],
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:01:00+00:00",
        command=block,
        environment={},
        staging={},
    )

    assert report["command"]["config"]["path"] == str(path)
    assert set(report["command"]["config"]["from_file"]) == {"year", "delay"}
    assert report["command"]["config"]["from_cli"] == ["stocks"]


def test_the_shipped_example_config_still_loads() -> None:
    """An example that no longer parses is worse than none.

    It is the first thing anybody copies, and it is never run by hand -- so
    without this it would rot silently and be discovered by a user, as their
    first error message.
    """
    example = Path(__file__).resolve().parent.parent / "firefox-bridge.example.toml"

    values, _ = runconfig.load_config(str(example))

    assert values, "the example should demonstrate at least one option"
    assert set(values) <= set(runconfig.FIELDS)


def test_json_and_toml_produce_the_same_run(tmp_path: Path) -> None:
    toml_path = write_config(tmp_path, 'stocks = "NCKL"\nyear = 2024\n')
    json_path = tmp_path / "conf.json"
    json_path.write_text(json.dumps({"stocks": "NCKL", "year": 2024}), encoding="utf-8")

    assert resolve(["--config", str(toml_path)], str(toml_path)).values == resolve(
        ["--config", str(json_path)], str(json_path)
    ).values
