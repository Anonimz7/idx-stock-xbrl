"""The downloader's own narration has to survive the run.

Before this, the downloader printed its trace to stdout and nothing else. A
quarter that failed was findable only by scrolling back through console output,
which is exactly what nobody does the morning after. These tests pin that the
trace is now in the log file, filterable by the fields that matter, and that the
terminal still reads the way it did before.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from firefox_bridge import progress as progress_module
from firefox_bridge.downloader.models import DownloadResult, RunSummary
from firefox_bridge.downloader.reporting import print_run_summary
from firefox_bridge.logging_config import (
    LOGGER_NAME,
    PROGRESS_LOGGER_NAME,
    configure_logging,
    get_logger,
    get_progress_logger,
    reset_logging,
    safe_extra,
)
from firefox_bridge.progress import notice, problem, progress


@pytest.fixture(autouse=True)
def _isolated_log(tmp_path: Path) -> Iterator[Path]:
    """Point this module's log assertions at a per-test file.

    The suite-wide `isolated_log_dir` keeps tests out of the developer's log, but
    these tests need to know exactly which file their own records landed in, so
    they get a fresh one per test.
    """
    reset_logging()
    log_dir = tmp_path / "logs"
    os.environ["FIREFOX_BRIDGE_LOG_DIR"] = str(log_dir)
    try:
        yield log_dir / "bridge.log"
    finally:
        reset_logging()
        os.environ.pop("FIREFOX_BRIDGE_LOG_DIR", None)


def _records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_progress_writes_to_stdout_and_to_the_file(
    _isolated_log: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    progress("STEP 1 OK: profil terbuka", stock="NCKL", year=2025, step=1)

    assert "STEP 1 OK: profil terbuka" in capsys.readouterr().out
    records = _records(_isolated_log)
    assert records, "the downloader's line never reached the log file"
    assert records[-1]["message"] == "STEP 1 OK: profil terbuka"
    assert records[-1]["logger"] == PROGRESS_LOGGER_NAME


def test_records_carry_the_fields_a_later_run_filters_on(_isolated_log: Path) -> None:
    progress("STEP DETECTED: TW2 <href>", stock="NCKL", year=2025, quarter=2)

    record = _records(_isolated_log)[-1]
    assert (record["stock"], record["year"], record["quarter"]) == ("NCKL", 2025, 2)


def test_every_fielded_record_is_json_serialisable(_isolated_log: Path) -> None:
    """A field that cannot be serialized must cost a character, not the record.

    `logging` drops a record it cannot format and writes a `--- Logging error ---`
    block to stderr instead, so a single `Path` handed to the logger would make
    that line vanish from the log without anyone noticing. The log is the thing
    you read when a run misbehaves, so a missing line is worse than a stringified
    field.
    """
    progress("dengan objek", stock="NCKL", weird=Path("C:/tmp/x"))
    progress("dengan set", stock="NCKL", tags={"a", "b"})

    records = _records(_isolated_log)
    assert len(records) == 2, "a non-serialisable field swallowed the record"
    assert records[0]["weird"] == str(Path("C:/tmp/x"))
    assert records[1]["tags"] in ("{'a', 'b'}", "{'b', 'a'}")


def test_console_does_not_repeat_the_progress_line(
    _isolated_log: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI prints its own plain line; the console handler must stay quiet.

    Otherwise every step would appear twice -- once plain, once in the verbose
    `asctime LEVEL name:` shape.
    """
    progress("WAIT: jeda sebelum klik")

    captured = capsys.readouterr()
    assert captured.out.count("WAIT: jeda sebelum klik") == 1
    assert "WAIT: jeda sebelum klik" not in captured.err


def test_problem_uses_stderr_and_warns_in_the_file(
    _isolated_log: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    problem("FAILED NCKL 2025: timeout", stock="NCKL", year=2025)

    captured = capsys.readouterr()
    assert "FAILED NCKL 2025" in captured.err
    assert "FAILED NCKL 2025" not in captured.out
    assert _records(_isolated_log)[-1]["level"] == "WARNING"


def test_notice_prints_to_stdout_but_warns_in_the_file(
    _isolated_log: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A recoverable oddity belongs on stdout but must not hide at INFO."""
    notice("HASH MISMATCH: TW1", stock="NCKL")

    assert "HASH MISMATCH: TW1" in capsys.readouterr().out
    assert _records(_isolated_log)[-1]["level"] == "WARNING"


def test_detail_is_recorded_without_echoing(_isolated_log: Path, capsys: pytest.CaptureFixture[str]) -> None:
    progress_module.detail("ukuran file", stock="NCKL", bytes=243904)

    assert capsys.readouterr().out == ""
    assert _records(_isolated_log)[-1]["bytes"] == 243904


def test_reserved_field_names_are_dropped_rather_than_crashing(
    _isolated_log: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`logging` raises KeyError if `extra` overwrites one of its own attributes.

    A field innocently named `filename` must cost one key, not the run.
    """
    progress("dengan nama(field) yangMANYAK", filename="NCKL.zip", module="x", stock="NCKL")

    record = _records(_isolated_log)[-1]
    assert record["stock"] == "NCKL"
    assert "filename" not in record
    assert "module" not in record


def test_safe_extra_keeps_ordinary_fields() -> None:
    assert safe_extra({"stock": "NCKL", "year": 2025}) == {"stock": "NCKL", "year": 2025}
    assert safe_extra({"filename": "x", "msg": "y", "_private": "z", "name": "n"}) == {}


def test_run_summary_reaches_the_log(capsys: pytest.CaptureFixture[str], _isolated_log: Path) -> None:
    summary = RunSummary()
    summary.results.append(
        DownloadResult(stock="NCKL", href="https://idx/…/inlineXBRL.zip", filename="NCKL_T1.zip")
    )
    summary.failures.append("BBCA: timeout")
    print_run_summary(summary)

    records = _records(_isolated_log)
    messages = [record["message"] for record in records]
    assert "  Successful: 1" in messages
    assert "  Failed:     1" in messages
    assert "  - BBCA: timeout" in messages
    # A failure in the summary is exactly the thing `level` exists to surface.
    assert any(record["level"] == "WARNING" and "BBCA" in record["message"] for record in records)


def test_token_print_in_server_stays_a_plain_print() -> None:
    """The server's `print(settings.token)` must never become a log record.

    A token in the log file would outlive the run and the temp directory.
    """
    server = (
        Path(__file__).resolve().parent.parent / "firefox_bridge" / "server.py"
    ).read_text(encoding="utf-8")
    assert "print(settings.token)" in server
    assert "progress(settings.token)" not in server
    assert "problem(settings.token)" not in server


def test_configure_logging_is_idempotent_for_the_progress_logger(
    _isolated_log: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging()
    configure_logging()
    progress("satu kali saja", stock="NCKL")

    assert capsys.readouterr().out.count("satu kali saja") == 1
    assert len([r for r in _records(_isolated_log) if "satu kali saja" in r["message"]]) == 1


def test_get_progress_logger_reuses_the_bridge_handlers() -> None:
    """The progress logger must not open the same file a second time."""
    configure_logging()
    progress_logger = get_progress_logger()

    assert not progress_logger.handlers, "progress logger carries its own handler"
    assert progress_logger.propagate is True


def test_a_foreign_handler_does_not_disable_the_file_log(
    _isolated_log: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A handler someone else attached must not switch the log off.

    The first version of this asked "does the logger have any handlers" to decide
    whether it was configured. A test runner -- or any application embedding the
    bridge -- attaches handlers of its own, that question answers yes, and the
    file log is then skipped in silence. Nothing errors; the log just quietly
    stops existing, which is the failure mode this whole change exists to fix.
    """
    logger = logging.getLogger(LOGGER_NAME)
    foreign = logging.NullHandler()
    logger.addHandler(foreign)
    try:
        progress("harus tetap masuk file", stock="NCKL")
    finally:
        logger.removeHandler(foreign)

    assert _records(_isolated_log), "a foreign handler silently disabled the log"
    assert "harus tetap masuk file" in capsys.readouterr().out


def test_configure_logging_leaves_foreign_handlers_alone(_isolated_log: Path) -> None:
    logger = logging.getLogger(LOGGER_NAME)
    foreign = logging.NullHandler()
    logger.addHandler(foreign)
    try:
        configure_logging()
        assert foreign in logger.handlers, "configure_logging unhooked someone else's handler"
    finally:
        logger.removeHandler(foreign)
        reset_logging()


def test_get_logger_configures_itself_when_only_foreign_handlers_exist(
    _isolated_log: Path,
) -> None:
    logger = logging.getLogger(LOGGER_NAME)
    foreign = logging.NullHandler()
    logger.addHandler(foreign)
    try:
        get_logger()
        assert any(isinstance(handler, logging.FileHandler) for handler in logger.handlers)
    finally:
        logger.removeHandler(foreign)
        reset_logging()
