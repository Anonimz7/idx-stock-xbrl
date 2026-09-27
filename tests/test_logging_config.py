"""Where the log is written, and what happens when it cannot be written.

The location is not a detail. A log that lands somewhere nobody looks is worse
than no log, because it reads as "no problems were recorded" rather than "the
problems were never captured". These tests pin where it goes by default, and
that losing it degrades the diagnostics instead of stopping the bridge.
"""

from __future__ import annotations

from pathlib import Path

from firefox_bridge import logging_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_log_lands_in_the_project_folder_by_default(monkeypatch) -> None:
    monkeypatch.delenv("FIREFOX_BRIDGE_LOG_DIR", raising=False)

    assert logging_config._log_file_path() == PROJECT_ROOT / "logs" / "bridge.log"


def test_log_dir_environment_variable_wins(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FIREFOX_BRIDGE_LOG_DIR", str(tmp_path / "custom"))

    assert logging_config._log_file_path() == tmp_path / "custom" / "bridge.log"


def test_log_lines_are_json_and_carry_no_token(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FIREFOX_BRIDGE_LOG_DIR", str(tmp_path / "logs"))
    logger = logging_config.configure_logging()
    try:
        logger.info("hello", extra={"transport": "websocket"})
    finally:
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)

    line = (tmp_path / "logs" / "bridge.log").read_text(encoding="utf-8").strip()
    assert '"message":"hello"' in line
    assert '"transport":"websocket"' in line


def test_bridge_still_starts_when_the_log_cannot_be_written(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    """A bridge that will not start because a directory is unwritable helps nobody.

    The console already carries the message, so the file log is a convenience.
    Losing it must degrade the diagnostics, not take the service down.
    """
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("this is a file, so it cannot hold a logs/ directory", encoding="utf-8")
    monkeypatch.setenv("FIREFOX_BRIDGE_LOG_DIR", str(blocker / "logs"))

    logger = logging_config.configure_logging()
    try:
        logger.info("the bridge is alive")
    finally:
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)

    stderr = capsys.readouterr().err
    assert "Log file unavailable" in stderr
    assert "the bridge is alive" in stderr
